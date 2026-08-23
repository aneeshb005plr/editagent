"""
app/api/v1/documents.py

The HTTP surface for document review: submit a file, check status/
get findings. Thin routes - all real logic already lives in
app/jobs/service.py (submission) and app/jobs/repository.py +
app/jobs/findings_repository.py (status/results). This file's job is
request parsing, auth/ownership checks, and translating between HTTP
and those existing functions - nothing here duplicates logic that
already exists elsewhere.

WHO ACTUALLY CALLS THESE ROUTES: Streamlit (for local testing/demo),
and any future non-Teams client. Teams itself does NOT call these -
a Teams integration goes through a separate Bot Framework message
endpoint (Activity handler, via the Microsoft 365 Agents SDK), and
the future LangGraph conversational shell calls submit_review_job()/
repository.get_job()/get_findings_for_job() DIRECTLY as Python
functions, in-process - not over HTTP. This is exactly why the real
logic lives in app/jobs/service.py rather than in these route
handlers: both this REST surface and the future Teams shell can call
the same underlying functions without duplicating logic between them.

AUTH: see app/auth/dependencies.py - get_current_user_id() supports TWO
real modes (settings.AUTH_MODE = "header" or "entra"), not one
placeholder and one guess. Defaults to "header", matching the
confirmed reality that Entra auth wasn't implemented in the first
app this pattern is modeled on.

DB DEPENDENCY: uses the real, existing app/database.py's
get_database() - not reimplemented here. Previously this file
defined its own local get_db() reading request.app.state.mongo_db
directly, duplicating logic that already existed correctly - fixed
once the real app/database.py was available.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pymongo.asynchronous.database import AsyncDatabase

from app.auth.dependencies import get_current_user_id
from app.api.v1.schemas import JobStatusResponse, SubmitReviewResponse
from app.config import settings
from app.database import get_database
from app.documents.dispatcher import _extension_of, supported_extensions
from app.jobs import repository
from app.jobs.findings_repository import get_findings_for_job
from app.jobs.service import TooManyQueuedJobsError, submit_review_job
from app.rules.schema import AppliesTo, EnglishVariant

logger = logging.getLogger("app.api.v1.documents")

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/review", response_model=SubmitReviewResponse, status_code=202)
async def submit_review(
    file: UploadFile = File(...),
    applies_to: AppliesTo = Form(AppliesTo.GENERAL),
    is_pcs: bool = Form(False),
    english_variant: EnglishVariant = Form(EnglishVariant.US),
    user_id: str = Depends(get_current_user_id),
    db: AsyncDatabase = Depends(get_database),
) -> SubmitReviewResponse:
    """Submits a document for background review. Returns 202
    (Accepted) - the review has NOT run yet, only been queued; poll
    GET /documents/review/{job_id} for status and results.

    is_pcs=True without applies_to=AppliesTo.AUDIT is accepted but
    has no effect - the PCS carve-out only matters when AUDIT rules
    are active in the first place (see review_document()'s own
    parameters) - not rejected as an error since a client sending
    both flags together isn't actually wrong, just redundant in that
    combination.
    """

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = _extension_of(file.filename)
    if ext not in supported_extensions():
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}' - supported: {', '.join(supported_extensions())}",
        )

    if file.size is not None and file.size > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.MAX_FILE_SIZE_MB}MB limit ({file.size / 1024 / 1024:.1f}MB)",
        )

    file_bytes = await file.read()

    try:
        result = await submit_review_job(
            db=db,
            user_id=user_id,
            file_bytes=file_bytes,
            filename=file.filename,
            max_queued_jobs_per_user=settings.MAX_QUEUED_JOBS_PER_USER,
            applies_to=applies_to,
            is_pcs=is_pcs,
            english_variant=english_variant,
        )
    except TooManyQueuedJobsError as e:
        raise HTTPException(status_code=429, detail=str(e))

    message = (
        "Queued behind your current review - it'll run once that one finishes."
        if result.had_existing_active_job
        else "Review submitted and will begin shortly."
    )

    return SubmitReviewResponse(
        job_id=result.job_id,
        status="pending",
        queued_behind_existing_job=result.had_existing_active_job,
        message=message,
    )


@router.get("/review/{job_id}", response_model=JobStatusResponse)
async def get_review_status(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncDatabase = Depends(get_database),
) -> JobStatusResponse:
    """Status + findings (once complete) for a previously submitted
    review. Returns 404 for a job that doesn't exist OR belongs to a
    different user - deliberately the SAME response for both cases
    (not 403 for "exists but not yours") to avoid confirming to a
    caller that a given job_id exists at all, standard practice for
    not leaking other users' data through error-response shape."""

    try:
        job = await repository.get_job(db, job_id)
    except Exception:
        job = None

    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="Review job not found")

    findings = None
    if job.status.value == "succeeded":
        findings = await get_findings_for_job(db, job_id)

    return JobStatusResponse(
        job_id=job_id,
        status=job.status,
        filename=job.filename,
        applies_to=job.applies_to,
        is_pcs=job.is_pcs,
        english_variant=job.english_variant,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        finding_count=job.finding_count,
        error_message=job.error_message,
        findings=findings,
    )