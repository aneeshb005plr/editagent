"""
app/api/v1/schemas.py

Request/response models for the documents review API - real Pydantic
boundary types (HTTP request/response bodies), same reasoning as
every other Pydantic type in this codebase (app/review/models.py,
app/jobs/schema.py): this is where untrusted/external data crosses a
real boundary, unlike the internal hot-path dataclasses in
app/documents/base.py.
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
    # True if the user already had an active (pending/running) job
    # when this one was submitted - this one is NOT running yet, it's
    # FIFO-queued behind the existing one. See app/jobs/service.py's
    # submit_review_job() docstring - the actual queueing decision
    # already happened; this just tells the caller it occurred, for
    # a future conversational layer to mention it.
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
    # Populated ONLY when status == SUCCEEDED - a still-running or
    # queued job has no findings yet, and a failed job has none at
    # all (error_message explains why instead). Deliberately not an
    # empty list in those cases - None distinguishes "not available
    # yet/at all" from "genuinely zero findings," which is itself a
    # meaningful, real result (a clean document) worth being able to
    # tell apart from "review hasn't finished."