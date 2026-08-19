"""
app/jobs/repository.py

Mongo access for review_jobs. The one operation worth being precise
about: claim_next_pending_job() uses find_one_and_update() - a
single-document MongoDB operation, which is atomic by MongoDB's own
guarantees (confirmed via the real, current PyMongo async API, not
assumed) - so two concurrent calls (e.g. if this were ever scaled to
multiple worker instances, even though it's in-process/single-
instance today) cannot claim the same job. A naive "find the oldest
pending job, then separately update it" would have a real race
condition; this doesn't.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.jobs.schema import JobStatus, ReviewJob

_COLLECTION = "review_jobs"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_job(doc: dict) -> ReviewJob:
    doc = dict(doc)
    doc.pop("_id", None)
    return ReviewJob(**doc)


async def create_job(db: AsyncDatabase, job: ReviewJob) -> str:
    """Inserts a new job, returns its id as a string."""

    result = await db[_COLLECTION].insert_one(job.model_dump())
    return str(result.inserted_id)


async def get_job(db: AsyncDatabase, job_id: str) -> ReviewJob | None:
    doc = await db[_COLLECTION].find_one({"_id": ObjectId(job_id)})
    return _to_job(doc) if doc else None


async def get_active_job_for_user(db: AsyncDatabase, user_id: str) -> ReviewJob | None:
    """For the confirmed one-active-job-per-user design (queue a
    second submission rather than run it concurrently, or reject/
    replace - see the earlier discovery discussion this implements).
    'Active' means PENDING or RUNNING - a queued-but-not-yet-started
    job still counts, since the point is limiting how much work one
    user has outstanding at once, not just concurrent execution."""

    doc = await db[_COLLECTION].find_one(
        {
            "user_id": user_id,
            "status": {"$in": [JobStatus.PENDING.value, JobStatus.RUNNING.value]},
        }
    )
    return _to_job(doc) if doc else None


async def count_active_jobs_for_user(db: AsyncDatabase, user_id: str) -> int:
    """Backs settings.MAX_QUEUED_JOBS_PER_USER - a real safety cap
    that existed in config.py since early in this build but was
    never actually wired to anything until now. Prevents one user
    from queueing an unbounded number of reviews."""

    return await db[_COLLECTION].count_documents(
        {
            "user_id": user_id,
            "status": {"$in": [JobStatus.PENDING.value, JobStatus.RUNNING.value]},
        }
    )


async def claim_next_pending_job(db: AsyncDatabase) -> tuple[str, ReviewJob] | None:
    """Atomically claims the OLDEST pending job (FIFO across all
    users) BELONGING TO A USER WHO DOESN'T ALREADY HAVE A JOB
    RUNNING - this is what keeps "one active job per user" a real
    guarantee once the worker processes multiple jobs concurrently
    (see worker.py), not just an artifact of the worker having no
    concurrency at all.

    NOT SAFE TO CALL CONCURRENTLY FROM MULTIPLE CALLERS WITHOUT
    EXTERNAL SERIALIZATION - confirmed by direct testing, not just
    reasoned about: this function does a READ (distinct() on
    currently-RUNNING user_ids) then a WRITE (the atomic
    find_one_and_update) as two SEPARATE operations. find_one_and_
    update() is atomic on its own, but the read-then-write PAIR is
    not - two concurrent calls can both read "no one running yet"
    before either commits, both then successfully claim a job from
    the SAME user. Confirmed by direct test: two jobs from one user
    both ended up RUNNING simultaneously when this was called via
    asyncio.gather() with no external lock. worker.py's
    _worker_slot_loop() wraps every call to this function in a
    shared asyncio.Lock (app.state.job_claim_lock) specifically to
    serialize this - that lock is REQUIRED, not optional, for
    multi-slot correctness. A single-process asyncio.Lock is the
    right fix (not a MongoDB transaction) because this worker is
    confirmed in-process/single-instance - see worker.py's docstring.

    Returns (job_id, job) or None if nothing eligible is pending."""

    now = _utcnow()
    running_user_ids = await db[_COLLECTION].distinct(
        "user_id", {"status": JobStatus.RUNNING.value}
    )
    doc = await db[_COLLECTION].find_one_and_update(
        {
            "status": JobStatus.PENDING.value,
            "user_id": {"$nin": running_user_ids},
        },
        {"$set": {"status": JobStatus.RUNNING.value, "started_at": now, "heartbeat_at": now, "updated_at": now}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        return None
    job_id = str(doc["_id"])
    return job_id, _to_job(doc)


async def heartbeat_job(db: AsyncDatabase, job_id: str) -> None:
    await db[_COLLECTION].update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {"heartbeat_at": _utcnow(), "updated_at": _utcnow()}},
    )


async def complete_job(db: AsyncDatabase, job_id: str, finding_count: int) -> None:
    now = _utcnow()
    await db[_COLLECTION].update_one(
        {"_id": ObjectId(job_id)},
        {
            "$set": {
                "status": JobStatus.SUCCEEDED.value,
                "finding_count": finding_count,
                "completed_at": now,
                "updated_at": now,
            }
        },
    )


async def fail_job(db: AsyncDatabase, job_id: str, error_message: str) -> None:
    now = _utcnow()
    await db[_COLLECTION].update_one(
        {"_id": ObjectId(job_id)},
        {
            "$set": {
                "status": JobStatus.FAILED.value,
                "error_message": error_message,
                "completed_at": now,
                "updated_at": now,
            }
        },
    )


async def requeue_stale_jobs(db: AsyncDatabase, stale_threshold_seconds: int) -> int:
    """A RUNNING job whose heartbeat is older than the threshold gets
    reset to PENDING so claim_next_pending_job() can pick it up again
    (e.g. after a process crash mid-job). Returns the number requeued.

    See ReviewJob's own docstring for the honest current limitation:
    heartbeat only updates at phase boundaries, not mid-LLM-call, so
    this threshold needs to be set generously relative to a single
    judgment/consistency batch's real duration - not yet tuned
    against real 100MB-scale timing."""

    cutoff = _utcnow() - timedelta(seconds=stale_threshold_seconds)
    result = await db[_COLLECTION].update_many(
        {"status": JobStatus.RUNNING.value, "heartbeat_at": {"$lt": cutoff}},
        {"$set": {"status": JobStatus.PENDING.value, "updated_at": _utcnow()}},
    )
    return result.modified_count