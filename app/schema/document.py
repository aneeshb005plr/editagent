"""
app/schema/document.py

Request/response models for the documents review API. Matches the
established platform convention (from the sibling RFP Analyzer
codebase's app/schema/ package - knowledge_source.py, admin.py,
job.py) of a top-level app/schema/ with one file per domain - NOT
nested under app/api/v1/. Previously this file lived at
app/api/v1/schemas.py, inconsistent with app/schema/chat.py's
naming (chat_schemas.py) and with the wrong layer entirely per the
established convention - fixed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.jobs.schema import JobStatus
from app.review.models import Finding
from app.rules.schema import AppliesTo, EnglishVariant


class SubmitReviewResponse(BaseModel):
    job_id: str
    status: JobStatus
    queued_behind_existing_job: bool
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    filename: str
    applies_to: AppliesTo
    is_pcs: bool
    english_variant: EnglishVariant
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    finding_count: int | None
    error_message: str | None
    findings: list[Finding] | None