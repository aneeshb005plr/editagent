"""
app/schema/staged_upload.py

See app/services/upload_service.py for the compensation logic
(point 6 of external review) and the expiry/cleanup mechanism
(point 9) built on top of expires_at below.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field


class StagedUploadStatus(str, Enum):
    STAGED = "staged"
    CONSUMED = "consumed"
    ABANDONED = "abandoned"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_expiry() -> datetime:
    return _utcnow() + timedelta(hours=24)


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
    expires_at: datetime = Field(default_factory=_default_expiry)
    # FIX for external review point 9: a real, previously-missing gap
    # - a user who attaches a file, starts intake, then closes the
    # browser and never returns had NO cleanup path at all (only
    # explicit replacement and explicit failed-job-creation were
    # handled). 24h is a reasonable, untuned default - matches this
    # project's established pattern of flagging untuned defaults
    # rather than pretending a number is final.