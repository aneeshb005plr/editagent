from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field
from app.rules.schema import AppliesTo, EnglishVariant

class JobStatus(str, Enum):
    PENDING = "pending"; RUNNING = "running"; SUCCEEDED = "succeeded"; FAILED = "failed"

def _utcnow(): return datetime.now(timezone.utc)

class ReviewJob(BaseModel):
    user_id: str
    filename: str
    file_size_bytes: int
    gridfs_file_id: str
    applies_to: AppliesTo = AppliesTo.GENERAL
    is_pcs: bool = False
    english_variant: EnglishVariant = EnglishVariant.US
    status: JobStatus = JobStatus.PENDING
    error_message: str | None = None
    finding_count: int | None = None
    source_upload_id: str | None = None
    # IDEMPOTENCY FIX, per external review: when a job is created from
    # a staged upload (the chat flow), this ties the job back to that
    # upload. create_job_from_staged_upload() checks for an EXISTING
    # job with this source_upload_id before creating a new one - a
    # retry (e.g. mark_consumed() failing after the job was already
    # created, then the caller retrying) returns the existing job
    # instead of creating a duplicate pointing at the same GridFS file.
    # None for jobs from the direct REST path (no staged upload
    # involved there).
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None