"""
app/jobs/service.py

FIXED REAL REGRESSION, confirmed via direct reproduction and via
external review: submit_review_job() previously called store_file()
BEFORE the queue-capacity check (introduced when Phase 1 refactored
this to share code with create_job_from_staged_upload) - meaning a
user already at their queue limit still got a real file uploaded to
GridFS that then sat orphaned forever, since TooManyQueuedJobsError
was only raised AFTER the upload succeeded. Confirmed by direct test:
filled a user's queue, submitted, watched the file land in storage
anyway despite the rejection. The claim "unchanged behavior" in the
prior version of this file's docstring was WRONG - this function's
docstring is corrected below, and the fix restores the ORIGINAL
pre-Phase-1 order (check capacity, THEN store).

IDEMPOTENCY FIX, per external review: create_job_from_staged_upload()
now checks for an existing job tied to the same source_upload_id
before creating a new one - a retry after a partial failure (job
created, then mark_consumed() fails, caller retries) returns the
existing job instead of creating a second one pointing at the same
GridFS file (which would leave one job's eventual GridFS cleanup
breaking the other job that still needs the same file).
"""

from __future__ import annotations

from dataclasses import dataclass

from pymongo.asynchronous.database import AsyncDatabase

from app.jobs import repository
from app.jobs.schema import ReviewJob
from app.jobs.storage import store_file
from app.rules.schema import AppliesTo, EnglishVariant


class TooManyQueuedJobsError(Exception):
    def __init__(self, user_id: str, limit: int):
        self.user_id = user_id
        self.limit = limit
        super().__init__(
            f"User {user_id} already has {limit} active review(s) queued/running - "
            f"submit again once one completes."
        )


@dataclass
class SubmissionResult:
    job_id: str
    had_existing_active_job: bool


async def _check_queue_capacity(db: AsyncDatabase, user_id: str, max_queued_jobs_per_user: int) -> bool:
    """Returns had_existing_active_job. Raises TooManyQueuedJobsError
    if at capacity. Deliberately a separate, explicit step BOTH
    submission paths call FIRST, before acquiring/referencing any
    file - so a rejection never has a chance to orphan anything."""

    active_count = await repository.count_active_jobs_for_user(db, user_id)
    if active_count >= max_queued_jobs_per_user:
        raise TooManyQueuedJobsError(user_id, max_queued_jobs_per_user)
    return active_count > 0


async def submit_review_job(
    db: AsyncDatabase,
    user_id: str,
    file_bytes: bytes,
    filename: str,
    max_queued_jobs_per_user: int,
    applies_to: AppliesTo = AppliesTo.GENERAL,
    is_pcs: bool = False,
    english_variant: EnglishVariant = EnglishVariant.US,
) -> SubmissionResult:
    """The REST route's single-shot submission path. FIXED ORDER:
    capacity check happens BEFORE store_file() - a rejected
    submission never uploads anything, matching the original
    pre-Phase-1 behavior (confirmed via direct test, not just
    asserted)."""

    had_existing = await _check_queue_capacity(db, user_id, max_queued_jobs_per_user)

    gridfs_file_id = await store_file(db, file_bytes, filename)

    job = ReviewJob(
        user_id=user_id, filename=filename, file_size_bytes=len(file_bytes),
        gridfs_file_id=gridfs_file_id, applies_to=applies_to, is_pcs=is_pcs,
        english_variant=english_variant,
    )
    job_id = await repository.create_job(db, job)

    return SubmissionResult(job_id=job_id, had_existing_active_job=had_existing)


async def create_job_from_staged_upload(
    db: AsyncDatabase,
    user_id: str,
    gridfs_file_id: str,
    filename: str,
    file_size_bytes: int,
    max_queued_jobs_per_user: int,
    applies_to: AppliesTo = AppliesTo.GENERAL,
    is_pcs: bool = False,
    english_variant: EnglishVariant = EnglishVariant.US,
    source_upload_id: str | None = None,
) -> SubmissionResult:
    """Chat flow's path - the file is already in GridFS from an
    earlier stage_upload() call. IDEMPOTENT when source_upload_id is
    provided: checks for an already-created job FIRST, before
    touching queue capacity at all - a retry for the same staged
    upload returns the existing job rather than creating a
    duplicate."""

    if source_upload_id:
        existing = await repository.get_job_by_source_upload_id(db, source_upload_id)
        if existing is not None:
            # Retry of an already-completed submission - return the
            # existing job, don't re-check capacity or create anything.
            existing_job_id, _ = existing
            return SubmissionResult(job_id=existing_job_id, had_existing_active_job=False)

    had_existing = await _check_queue_capacity(db, user_id, max_queued_jobs_per_user)

    job = ReviewJob(
        user_id=user_id, filename=filename, file_size_bytes=file_size_bytes,
        gridfs_file_id=gridfs_file_id, applies_to=applies_to, is_pcs=is_pcs,
        english_variant=english_variant, source_upload_id=source_upload_id,
    )
    job_id = await repository.create_job(db, job)

    return SubmissionResult(job_id=job_id, had_existing_active_job=had_existing)