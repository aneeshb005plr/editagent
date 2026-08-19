"""
app/jobs/worker.py

The background job processor - runs IN-PROCESS with the FastAPI app
(as asyncio background tasks started from the lifespan), not as a
separately deployed service. This is a deliberate, confirmed choice,
not a default: knowledge-sync-worker (a genuinely separate, shared,
multi-agent deployment) exists because it serves many agents from
one process independently of any single agent's uptime. EditEdge is
single-agent - the operational simplicity of one deployable unit
outweighs the benefit of a separate worker process at this stage,
and the heartbeat/stale-job-requeue pattern (borrowed from that same
proven design) plus GridFS-backed file durability (see storage.py)
together cover the main risk a separate process would otherwise
protect against (a mid-job crash), without needing a second Docker
image or deployment to operate.

CONCURRENCY: a pool of settings.MAX_CONCURRENT_JOBS persistent
"worker slots" (see _worker_slot_loop), each independently claiming
and processing jobs in its own loop - NOT an explicit
asyncio.Semaphore guarding a single shared loop. Considered and
rejected: claim a bounded BATCH then asyncio.gather() it and wait for
the whole batch before claiming more - rejected because it wastes
capacity (if 2 of 3 batched jobs finish quickly, the worker sits idle
until the 3rd finishes instead of immediately grabbing new work).
The slot-pool pattern keeps every slot maximally utilized: as soon as
one slot's job finishes, that SAME slot's loop immediately tries to
claim the next one - no coordination needed, no semaphore needed,
concurrency is naturally bounded by "exactly N slot coroutines
exist." Each slot self-heals independently too (see
_supervised_slot_loop) - one slot crashing doesn't take the other
slots down with it, which a single shared supervised loop would risk.

"ONE ACTIVE JOB PER USER" IS PRESERVED under this concurrency - see
repository.claim_next_pending_job()'s exclusion of users who already
have a RUNNING job. Two DIFFERENT users' jobs running at the same
time (across different slots) is exactly what this concurrency adds;
two of the SAME user's jobs running at the same time stays prevented,
same as before - that was a deliberate fairness/resource-protection
decision (documented in claim_next_pending_job()), not just an
artifact of the old single-job-at-a-time design.

HONEST LIMITATION, stated plainly rather than glossed over: heartbeat
currently updates at phase boundaries only (claimed, after-parse,
after-review, completed) - NOT mid-LLM-call. review_document() is
called as one atomic await from this module's perspective; a single
long judgment/consistency batch has no internal checkpoint to
heartbeat from. This is fine as long as STALE_JOB_THRESHOLD_SECONDS
is set generously relative to real batch durations (we have one real
data point: ~32s for a judgment pass on a 7-block document against
the real endpoint - a 100MB document's batches could run far longer).
A real fix would thread an optional heartbeat callback through
judgment.py's/consistency.py's batch loops - a contained, worthwhile
future change once real 100MB timing data exists to size it against,
not attempted here ahead of that evidence.

REQUIRES THREE CONFIG SETTINGS - now present in config.py (see
app/config.py's "Job system" section):

    POLL_INTERVAL_SECONDS: int = 5
    # How often an idle slot re-checks for a new pending job.

    STALE_JOB_THRESHOLD_SECONDS: int = 900
    # A RUNNING job with no heartbeat this old gets requeued. See the
    # heartbeat-granularity limitation above - needs real tuning once
    # the 100MB spike produces real timing data.

    MAX_CONCURRENT_JOBS: int = 3
    # How many worker slots run concurrently - a real, currently
    # UNTUNED guess, same status as image_extraction.py's own
    # max_concurrent. Bounds how many simultaneous review pipelines
    # (each making real LLM calls) can hit the shared GenAI service
    # at once - needs real tuning against actual cost/latency budget,
    # not left at a guessed default in production.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import FastAPI

from app.config import settings
from app.documents.pipeline import parse_and_extract
from app.jobs import repository
from app.jobs.findings_repository import save_findings
from app.jobs.schema import ReviewJob
from app.jobs.storage import delete_file, retrieve_file
from app.review.engine import review_document
from app.rules.taxonomy import RULE_SET

logger = logging.getLogger("app.jobs.worker")


async def _process_job(app: FastAPI, job_id: str, job: ReviewJob) -> None:
    db = app.state.mongo_db
    genai_client = app.state.genai_client
    # Reuses the SAME shared client connect_genai() already set up at
    # startup (see app/llm.py) - not a fresh client per job. No
    # Request object exists in this context, so this reads
    # app.state directly rather than going through the
    # get_genai_client(request) dependency, which is request-scoped.
    # Shared across ALL slots too - one pooled connection, not one
    # per slot, matching app/llm.py's whole reason for existing.

    try:
        file_bytes = await retrieve_file(db, job.gridfs_file_id)

        parsed = await parse_and_extract(
            file_bytes=file_bytes,
            filename=job.filename,
            max_size_mb=settings.MAX_FILE_SIZE_MB,
            vision_model=genai_client.bind(temperature=0, max_tokens=1000),
        )
        await repository.heartbeat_job(db, job_id)

        findings = await review_document(
            parsed=parsed,
            rule_set=RULE_SET,
            applies_to=job.applies_to,
            judgment_model=genai_client,
            english_variant=job.english_variant,
            is_pcs=job.is_pcs,
        )
        await repository.heartbeat_job(db, job_id)

        await save_findings(db, job_id, findings)
        await repository.complete_job(db, job_id, finding_count=len(findings))
        await delete_file(db, job.gridfs_file_id)
        # Delete on SUCCESS only - see storage.py's documented
        # retention policy for why a failed job's file is kept.

        logger.info(
            "Job %s completed: %s -> %d findings",
            job_id, job.filename, len(findings),
        )

    except Exception as e:
        logger.error("Job %s failed: %s", job_id, job.filename, exc_info=True)
        await repository.fail_job(db, job_id, error_message=str(e))
        # Deliberately does NOT re-raise - a failed job is a normal,
        # expected outcome the worker must survive, not a crash.


async def _worker_slot_loop(app: FastAPI, slot_id: int) -> None:
    """One persistent slot - claims, processes, repeat, forever.
    Concurrency across slots is what MAX_CONCURRENT_JOBS actually
    means: N of these coroutines run at once, each independently
    working through the queue. Every slot also performs the stale-
    job-requeue check before each claim attempt - a small, cheap,
    idempotent operation, so redundancy across slots is an acceptable
    trade for not needing any cross-slot coordination for THAT part.

    THE CLAIM ITSELF IS SERIALIZED ACROSS SLOTS VIA app.state.
    job_claim_lock - FIXED A REAL, CONFIRMED RACE CONDITION found by
    direct testing (not theoretical): claim_next_pending_job() does a
    READ (which users are currently running) then a WRITE (claim a
    job from an excluded-user-free set) as two separate operations.
    When two slots called this concurrently via asyncio.gather, BOTH
    could read "no one running yet" before either committed its
    write - confirmed directly, two jobs from the SAME user both
    ended up RUNNING simultaneously, exactly the case "one active job
    per user" exists to prevent. A single-process asyncio.Lock is the
    right fix here (not a MongoDB transaction) BECAUSE this worker is
    confirmed in-process/single-instance - there's no cross-process
    race to guard against, only cross-coroutine, which asyncio.Lock
    handles correctly and cheaply. Only the claim step itself is
    serialized (fast: one distinct() + one find_one_and_update()) -
    actual job PROCESSING afterward stays fully concurrent across
    slots, so this does not reintroduce the throughput problem the
    slot-pool design was built to avoid."""

    db = app.state.mongo_db

    while True:
        app.state.worker_last_iteration = datetime.now(timezone.utc)

        try:
            requeued = await repository.requeue_stale_jobs(db, settings.STALE_JOB_THRESHOLD_SECONDS)
            if requeued:
                logger.warning("Slot %d: requeued %d stale job(s)", slot_id, requeued)

            async with app.state.job_claim_lock:
                claim = await repository.claim_next_pending_job(db)
        except Exception:
            logger.error("Slot %d: error during claim/requeue check", slot_id, exc_info=True)
            claim = None

        if claim is None:
            await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)
            continue

        job_id, job = claim
        logger.info(
            "Slot %d claimed job %s: %s (user=%s)",
            slot_id, job_id, job.filename, job.user_id,
        )
        await _process_job(app, job_id, job)
        # No sleep here - immediately loop to check for more work,
        # this slot just freed up.


async def _supervised_slot_loop(app: FastAPI, slot_id: int) -> None:
    """Self-healing wrapper per slot, matching knowledge-sync-
    worker's own documented pattern - if _worker_slot_loop() itself
    raises (should be unreachable given the try/except inside it, but
    defense-in-depth matters), log it and restart THIS SLOT after a
    short delay, without affecting the other slots. This is a real
    improvement over a single shared supervised loop: one slot
    crash-looping doesn't reduce total worker capacity to zero, only
    to N-1 slots while it recovers."""

    while True:
        try:
            await _worker_slot_loop(app, slot_id)
        except asyncio.CancelledError:
            raise  # real shutdown request, not a crash - let it propagate
        except Exception:
            logger.error(
                "Slot %d crashed unexpectedly - restarting in 5s", slot_id, exc_info=True
            )
            await asyncio.sleep(5)


def start_worker(app: FastAPI) -> list[asyncio.Task]:
    """Call from the FastAPI lifespan, after connect_to_mongo() and
    connect_genai() have both completed (every slot depends on both
    app.state.mongo_db and app.state.genai_client existing). Spawns
    settings.MAX_CONCURRENT_JOBS independent slot tasks, sharing one
    asyncio.Lock (app.state.job_claim_lock) that serializes just the
    claim step across them - see _worker_slot_loop's docstring for
    why this is required, not optional."""

    app.state.job_claim_lock = asyncio.Lock()
    tasks = [
        asyncio.create_task(_supervised_slot_loop(app, slot_id))
        for slot_id in range(settings.MAX_CONCURRENT_JOBS)
    ]
    app.state.worker_tasks = tasks
    logger.info("Job worker started (in-process, %d concurrent slots)", len(tasks))
    return tasks


async def stop_worker(app: FastAPI) -> None:
    """Call from the FastAPI lifespan shutdown, symmetric with
    start_worker(). Cancels all slots cleanly rather than leaving
    orphaned tasks."""

    tasks = getattr(app.state, "worker_tasks", None)
    if not tasks:
        return

    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Job worker stopped (%d slots)", len(tasks))