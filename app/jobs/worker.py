"""
app/jobs/worker.py

RECONSTRUCTED after an earlier environment reset in this session -
faithful to the real, tested version built and presented earlier in
this conversation. FIX for external review point 4: wires
cleanup_expired_staged_uploads() into each slot's idle cycle,
alongside the already-established requeue_stale_jobs() call - same
pattern, same reasoning (cheap, idempotent, redundancy across slots
is an acceptable trade for not needing cross-slot coordination).
Only safe to wire in NOW that point 1's CAS fix exists - previously
cleanup could race destructively with job creation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings
from app.documents.pipeline import parse_and_extract
from app.jobs import repository
from app.jobs.findings_repository import save_findings
from app.jobs.schema import ReviewJob
from app.jobs.storage import delete_file, retrieve_file
from app.review.engine import review_document
from app.rules.taxonomy import RULE_SET
from app.services.upload_service import cleanup_expired_staged_uploads

logger = logging.getLogger("app.jobs.worker")


async def _process_job(app, job_id: str, job: ReviewJob) -> None:
    db = app.state.mongo_db
    genai_client = app.state.genai_client
    try:
        file_bytes = await retrieve_file(db, job.gridfs_file_id)
        parsed = await parse_and_extract(
            file_bytes=file_bytes, filename=job.filename, max_size_mb=settings.MAX_FILE_SIZE_MB,
            vision_model=genai_client.bind(temperature=0, max_tokens=1000),
        )
        await repository.heartbeat_job(db, job_id)
        findings = await review_document(
            parsed=parsed, rule_set=RULE_SET, applies_to=job.applies_to,
            judgment_model=genai_client, english_variant=job.english_variant, is_pcs=job.is_pcs,
        )
        await repository.heartbeat_job(db, job_id)
        await save_findings(db, job_id, findings)
        await repository.complete_job(db, job_id, finding_count=len(findings))
        await delete_file(db, job.gridfs_file_id)
        logger.info("Job %s completed: %s -> %d findings", job_id, job.filename, len(findings))
    except Exception:
        logger.error("Job %s failed: %s", job_id, job.filename, exc_info=True)
        await repository.fail_job(db, job_id, error_message=str(Exception))


async def _worker_slot_loop(app, slot_id: int) -> None:
    db = app.state.mongo_db

    while True:
        app.state.worker_last_iteration = datetime.now(timezone.utc)

        try:
            requeued = await repository.requeue_stale_jobs(db, settings.STALE_JOB_THRESHOLD_SECONDS)
            if requeued:
                logger.warning("Slot %d: requeued %d stale job(s)", slot_id, requeued)

            # FIX for external review point 4: expired staged uploads
            # (a user attached a file, never completed intake, never
            # returned) previously had no cleanup path at all. Same
            # cheap/idempotent/redundant-across-slots reasoning as
            # requeue_stale_jobs() above - now safe to run
            # concurrently with job creation because of point 1's CAS
            # fix (cleanup can no longer win a race against an
            # in-progress reservation).
            cleaned = await cleanup_expired_staged_uploads(db)
            if cleaned:
                logger.info("Slot %d: cleaned up %d expired staged upload(s)", slot_id, cleaned)

            async with app.state.job_claim_lock:
                claim = await repository.claim_next_pending_job(db)
        except Exception:
            logger.error("Slot %d: error during claim/requeue/cleanup check", slot_id, exc_info=True)
            claim = None

        if claim is None:
            await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)
            continue

        job_id, job = claim
        logger.info("Slot %d claimed job %s: %s (user=%s)", slot_id, job_id, job.filename, job.user_id)
        await _process_job(app, job_id, job)


async def _supervised_slot_loop(app, slot_id: int) -> None:
    while True:
        try:
            await _worker_slot_loop(app, slot_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Slot %d crashed unexpectedly - restarting in 5s", slot_id, exc_info=True)
            await asyncio.sleep(5)


def start_worker(app) -> list[asyncio.Task]:
    app.state.job_claim_lock = asyncio.Lock()
    tasks = [asyncio.create_task(_supervised_slot_loop(app, slot_id)) for slot_id in range(settings.MAX_CONCURRENT_JOBS)]
    app.state.worker_tasks = tasks
    logger.info("Job worker started (in-process, %d concurrent slots)", len(tasks))
    return tasks


async def stop_worker(app) -> None:
    tasks = getattr(app.state, "worker_tasks", None)
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Job worker stopped (%d slots)", len(tasks))