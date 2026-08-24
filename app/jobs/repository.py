from __future__ import annotations
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from pymongo import ReturnDocument
from app.jobs.schema import JobStatus, ReviewJob

_COLLECTION = "review_jobs"

def _utcnow(): return datetime.now(timezone.utc)

def _to_job(doc):
    doc = dict(doc); doc.pop("_id", None)
    return ReviewJob(**doc)

async def create_job(db, job):
    result = await db[_COLLECTION].insert_one(job.model_dump())
    return str(result.inserted_id)

async def get_job(db, job_id):
    doc = await db[_COLLECTION].find_one({"_id": ObjectId(job_id)})
    return _to_job(doc) if doc else None

async def count_active_jobs_for_user(db, user_id):
    return await db[_COLLECTION].count_documents({"user_id": user_id, "status": {"$in": [JobStatus.PENDING.value, JobStatus.RUNNING.value]}})

async def claim_next_pending_job(db):
    now = _utcnow()
    running_user_ids = await db[_COLLECTION].distinct("user_id", {"status": JobStatus.RUNNING.value})
    doc = await db[_COLLECTION].find_one_and_update(
        {"status": JobStatus.PENDING.value, "user_id": {"$nin": running_user_ids}},
        {"$set": {"status": JobStatus.RUNNING.value, "started_at": now, "heartbeat_at": now, "updated_at": now}},
        sort=[("created_at", 1)], return_document=ReturnDocument.AFTER,
    )
    if doc is None: return None
    return str(doc["_id"]), _to_job(doc)

async def heartbeat_job(db, job_id):
    await db[_COLLECTION].update_one({"_id": ObjectId(job_id)}, {"$set": {"heartbeat_at": _utcnow(), "updated_at": _utcnow()}})

async def complete_job(db, job_id, finding_count):
    now = _utcnow()
    await db[_COLLECTION].update_one({"_id": ObjectId(job_id)}, {"$set": {"status": JobStatus.SUCCEEDED.value, "finding_count": finding_count, "completed_at": now, "updated_at": now}})

async def fail_job(db, job_id, error_message):
    now = _utcnow()
    await db[_COLLECTION].update_one({"_id": ObjectId(job_id)}, {"$set": {"status": JobStatus.FAILED.value, "error_message": error_message, "completed_at": now, "updated_at": now}})

async def cancel_job(db, job_id, user_id):
    result = await db[_COLLECTION].update_one(
        {"_id": ObjectId(job_id), "user_id": user_id, "status": JobStatus.PENDING.value},
        {"$set": {"status": JobStatus.FAILED.value, "error_message": "Cancelled by user", "updated_at": _utcnow()}},
    )
    return result.modified_count > 0

async def list_jobs_for_user(db, user_id, limit=10):
    cursor = db[_COLLECTION].find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [_to_job(doc) for doc in docs]

async def requeue_stale_jobs(db, stale_threshold_seconds):
    cutoff = _utcnow() - timedelta(seconds=stale_threshold_seconds)
    result = await db[_COLLECTION].update_many(
        {"status": JobStatus.RUNNING.value, "heartbeat_at": {"$lt": cutoff}},
        {"$set": {"status": JobStatus.PENDING.value, "updated_at": _utcnow()}},
    )
    return result.modified_count


async def get_job_by_source_upload_id(db, source_upload_id: str):
    """IDEMPOTENCY FIX, per external review: lets
    create_job_from_staged_upload() check for an already-created job
    before creating a duplicate on retry. Returns (job_id, job) -
    ReviewJob itself doesn't carry its own Mongo _id, so the caller
    needs the id returned alongside it, same pattern as
    claim_next_pending_job()."""
    doc = await db[_COLLECTION].find_one({"source_upload_id": source_upload_id})
    if doc is None:
        return None
    job_id = str(doc["_id"])
    return job_id, _to_job(doc)