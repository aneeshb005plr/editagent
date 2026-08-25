"""
app/repository/staged_upload_repository.py

FIX for external review (production-hardening pass) point 1 - THE
most important remaining correctness issue, per the review: status
transitions previously updated by _id alone, no precondition on the
CURRENT status. This meant cleanup (STAGED->ABANDONED) and job
creation (STAGED->CONSUMED) could race - both could "succeed"
against the same record, with whichever write landed last silently
overwriting the other, including a scenario where a job gets created
pointing at a gridfs_file_id that a concurrent cleanup sweep then
deletes.

mark_consumed()/mark_abandoned() are now genuine compare-and-set
operations: the Mongo filter itself requires status == STAGED, and
both return whether the transition actually succeeded (bool) -
confirmed via find_one_and_update's real atomicity, same guarantee
already relied on elsewhere in this codebase (app/jobs/repository.py's
claim_next_pending_job()). Callers (app/services/upload_service.py,
app/agent/nodes/create_review_job.py) only act on a WON transition.

mark_consumed()'s job_id is now Optional - see create_review_job.py
for why: it CAS-reserves (STAGED->CONSUMED) BEFORE the job actually
exists, closing the window where a concurrent cleanup sweep could
otherwise delete the GridFS file between "job created" and "upload
marked consumed". set_consumed_job_id() fills in the real job_id
afterward, once we already own the record (no race possible there -
a CAS reservation already excluded every other caller).
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.schema.staged_upload import StagedUpload, StagedUploadStatus

_COLLECTION = "staged_uploads"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_upload(doc: dict) -> StagedUpload:
    doc = dict(doc)
    doc.pop("_id", None)
    return StagedUpload(**doc)

async def ensure_indexes(db) -> None:
    """FIX for external review point 7/1 (second production-
    hardening pass): the cleanup query (status=STAGED, expires_at 
    now) had no supporting index - as staged_uploads grows, every
    worker slot's periodic cleanup check would mean a full collection
    scan. Compound index on (status, expires_at) matches the query
    shape directly."""

    await db[_COLLECTION].create_index([("status", 1), ("expires_at", 1)])


async def create_staged_upload(db: AsyncDatabase, upload: StagedUpload) -> str:
    result = await db[_COLLECTION].insert_one(upload.model_dump())
    return str(result.inserted_id)


async def get_staged_upload(db: AsyncDatabase, upload_id: str) -> StagedUpload | None:
    doc = await db[_COLLECTION].find_one({"_id": ObjectId(upload_id)})
    return _to_upload(doc) if doc else None


async def mark_consumed(db: AsyncDatabase, upload_id: str, job_id: str | None) -> bool:
    """Compare-and-set: only transitions if current status is STAGED.
    Returns True if THIS call won the transition, False if the
    record was already consumed/abandoned by someone else (a lost
    race, not an error - callers must check this, not assume
    success)."""

    result = await db[_COLLECTION].update_one(
        {"_id": ObjectId(upload_id), "status": StagedUploadStatus.STAGED.value},
        {"$set": {"status": StagedUploadStatus.CONSUMED.value, "consumed_job_id": job_id, "updated_at": _utcnow()}},
    )
    return result.modified_count > 0


async def set_consumed_job_id(db: AsyncDatabase, upload_id: str, job_id: str) -> None:
    """Fills in the real job_id on an ALREADY-reserved (CONSUMED)
    record - not a CAS, since by this point the caller already won
    the STAGED->CONSUMED transition and owns the record; no one else
    can be racing against this specific field."""

    await db[_COLLECTION].update_one(
        {"_id": ObjectId(upload_id)},
        {"$set": {"consumed_job_id": job_id, "updated_at": _utcnow()}},
    )


async def mark_abandoned(db: AsyncDatabase, upload_id: str) -> bool:
    """Compare-and-set - same guarantee as mark_consumed(). Returns
    True only if THIS call won the STAGED->ABANDONED transition."""

    result = await db[_COLLECTION].update_one(
        {"_id": ObjectId(upload_id), "status": StagedUploadStatus.STAGED.value},
        {"$set": {"status": StagedUploadStatus.ABANDONED.value, "updated_at": _utcnow()}},
    )
    return result.modified_count > 0


async def release_reservation(db: AsyncDatabase, upload_id: str) -> bool:
    """FIX for a bug caught during this same hardening pass: job
    creation can fail AFTER a reservation already transitioned
    STAGED->CONSUMED (see app/agent/nodes/create_review_job.py).
    mark_abandoned()'s CAS requires status==STAGED, which no longer
    matches a reserved-but-jobless record - calling it here would
    silently no-op, leaving the record stuck as CONSUMED with no
    job_id forever, invisible to cleanup (which only examines STAGED
    records). This is the correct, explicitly-scoped transition for
    THAT specific state: CONSUMED-with-no-job -> ABANDONED. Still a
    real compare-and-set (only transitions a record that's genuinely
    reserved-but-jobless, not an already-fulfilled consumption)."""

    result = await db[_COLLECTION].update_one(
        {"_id": ObjectId(upload_id), "status": StagedUploadStatus.CONSUMED.value, "consumed_job_id": None},
        {"$set": {"status": StagedUploadStatus.ABANDONED.value, "updated_at": _utcnow()}},
    )
    return result.modified_count > 0


async def find_expired_staged_uploads(db, limit: int = 100):
    cursor = db[_COLLECTION].find({
        "status": StagedUploadStatus.STAGED.value,
        "expires_at": {"$lt": datetime.now(timezone.utc)},
    }).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [(str(doc["_id"]), _to_upload(doc)) for doc in docs]