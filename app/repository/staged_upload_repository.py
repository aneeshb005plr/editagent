"""
app/repositories/staged_upload_repository.py

Persistence for StagedUpload records. Matches the established
layer-based convention (app/repository/message_repository.py
already lives here) - not nested under app/agent/, since staging is
conceptually a chat-turn concern but the repository layer itself
stays consistent regardless of which channel/service uses it.
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


async def create_staged_upload(db: AsyncDatabase, upload: StagedUpload) -> str:
    result = await db[_COLLECTION].insert_one(upload.model_dump())
    return str(result.inserted_id)


async def get_staged_upload(db: AsyncDatabase, upload_id: str) -> StagedUpload | None:
    doc = await db[_COLLECTION].find_one({"_id": ObjectId(upload_id)})
    return _to_upload(doc) if doc else None


async def mark_consumed(db: AsyncDatabase, upload_id: str, job_id: str) -> None:
    await db[_COLLECTION].update_one(
        {"_id": ObjectId(upload_id)},
        {"$set": {"status": StagedUploadStatus.CONSUMED.value, "consumed_job_id": job_id, "updated_at": _utcnow()}},
    )


async def mark_abandoned(db: AsyncDatabase, upload_id: str) -> None:
    await db[_COLLECTION].update_one(
        {"_id": ObjectId(upload_id)},
        {"$set": {"status": StagedUploadStatus.ABANDONED.value, "updated_at": _utcnow()}},
    )