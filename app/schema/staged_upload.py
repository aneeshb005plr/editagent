"""
app/schemas/staged_upload.py

Phase 1 of the chat architecture plan: large file bytes must never
live in checkpointed LangGraph state. MongoDB has a hard 16MB BSON
document size limit; ChatState is exactly what MongoDBSaver writes
into the checkpoint collection every turn - and this project's own
stated requirement is up to 100MB files. Without staging, intake
would not just be inefficient, it would outright fail for any file
over ~16MB. This is the real, load-bearing fix Phase 1 exists for.

A staged upload is a short-lived record: bytes go into GridFS
immediately (reusing app/jobs/storage.py's existing store_file/
retrieve_file/delete_file - already generic, not coupled to "review
job" specifically, so no duplicated GridFS logic here), and only a
small upload_id reference travels through ChatState from then on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class StagedUploadStatus(str, Enum):
    STAGED = "staged"
    CONSUMED = "consumed"
    ABANDONED = "abandoned"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StagedUpload(BaseModel):
    user_id: str
    gridfs_file_id: str
    filename: str
    content_type: str | None = None
    size_bytes: int
    status: StagedUploadStatus = StagedUploadStatus.STAGED
    consumed_job_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)