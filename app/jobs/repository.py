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


async def ensure_indexes(db) -> None:
    """FIX for external review point 2: the application-level
    get_job_by_source_upload_id() "check, then create" pattern is
    only a best-effort check - two concurrent calls can both see "no
    existing job" and both create one. A partial unique index makes
    the guarantee REAL: MongoDB itself rejects the second insert. See
    create_job_from_staged_upload() in app/jobs/service.py for how
    the resulting DuplicateKeyError is handled (resolves to the
    existing job, not treated as an error).

    CAUGHT A REAL BUG BEFORE THIS EVER RAN: the partial filter uses
    {"$type": "string"}, NOT {"$exists": True}. Confirmed via direct
    test that Pydantic's model_dump() includes source_upload_id=None
    for every REST-path job (Pydantic includes all fields by default,
    not just set ones) - and MongoDB's $exists: true matches a field
    that's PRESENT even when its value is null. Using $exists would
    have applied the unique constraint to every REST job's null
    value too, breaking every REST submission after the first with a
    spurious duplicate-key error. $type: "string" correctly matches
    only real, non-null source_upload_id values.

    Not yet wired into a startup hook - call this once during app
    startup (e.g. alongside other index-creation calls, if any exist
    elsewhere in this codebase's lifespan)."""

    await db[_COLLECTION].create_index(
        "source_upload_id",
        unique=True,
        partialFilterExpression={"source_upload_id": {"$type": "string"}},
    )


async def list_jobs_by_filename(db, user_id: str, filename: str, limit: int = 10):
    """Phase 3B job resolver support - bounded, ownership-scoped by
    construction (user_id is part of the query, not checked after
    the fact). Used to detect "same filename, multiple jobs"
    ambiguity (Scenario 7). Returns (job_id, ReviewJob) pairs -
    ReviewJob doesn't carry its own Mongo _id, same convention as
    get_job_by_source_upload_id() above."""
    cursor = db[_COLLECTION].find({"user_id": user_id, "filename": filename}).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [(str(doc["_id"]), _to_job(doc)) for doc in docs]


async def list_jobs_by_conversation(db, user_id: str, origin_conversation_id: str, limit: int = 10):
    """Phase 3B job resolver support - bounded. Scopes to jobs
    submitted from a specific conversation, for the "recent owned
    ReviewJobs / origin conversation context" resolution step.
    Returns (job_id, ReviewJob) pairs, same reasoning as above."""
    cursor = db[_COLLECTION].find(
        {"user_id": user_id, "origin_conversation_id": origin_conversation_id}
    ).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [(str(doc["_id"]), _to_job(doc)) for doc in docs]