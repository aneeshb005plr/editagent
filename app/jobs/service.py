"""
app/jobs/service.py

Job submission. Two entry points sharing one internal record-creation
path:

- submit_review_job() - the ORIGINAL, unchanged-signature path used
  by the REST /documents/review route (single-shot: file bytes +
  all intake answers arrive together, no staging needed - that
  route was never subject to the checkpointed-state problem Phase 1
  fixes, since it doesn't go through the graph at all).

- create_job_from_staged_upload() - Phase 1 addition, used by the
  chat flow once intake is complete. Takes an EXISTING gridfs_file_id
  from a prior stage_upload() call rather than raw bytes, so the
  file is genuinely uploaded ONCE - confirmed real requirement from
  the architecture doc ("File is not uploaded twice").

Both call the same private _create_job_record() for the actual
active-job-count check and ReviewJob creation, so this logic can't
drift between the two paths.
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


async def _create_job_record(
    db: AsyncDatabase,
    user_id: str,
    gridfs_file_id: str,
    filename: str,
    file_size_bytes: int,
    max_queued_jobs_per_user: int,
    applies_to: AppliesTo,
    is_pcs: bool,
    english_variant: EnglishVariant,
) -> SubmissionResult:
    active_count = await repository.count_active_jobs_for_user(db, user_id)
    if active_count >= max_queued_jobs_per_user:
        raise TooManyQueuedJobsError(user_id, max_queued_jobs_per_user)

    had_existing = active_count > 0

    job = ReviewJob(
        user_id=user_id, filename=filename, file_size_bytes=file_size_bytes,
        gridfs_file_id=gridfs_file_id, applies_to=applies_to, is_pcs=is_pcs,
        english_variant=english_variant,
    )
    job_id = await repository.create_job(db, job)

    return SubmissionResult(job_id=job_id, had_existing_active_job=had_existing)


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
    """Unchanged signature/behavior - the REST route's single-shot
    submission path. Stores the file itself; use
    create_job_from_staged_upload() instead when the file was already
    staged (the chat flow's path, post-Phase-1)."""

    gridfs_file_id = await store_file(db, file_bytes, filename)
    return await _create_job_record(
        db, user_id, gridfs_file_id, filename, len(file_bytes),
        max_queued_jobs_per_user, applies_to, is_pcs, english_variant,
    )


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
) -> SubmissionResult:
    """Phase 1 addition - does NOT call store_file(), since the bytes
    are already in GridFS from an earlier stage_upload() call. Caller
    (app/agent/nodes/submit_document.py) is responsible for marking
    the staged upload consumed after this succeeds."""

    return await _create_job_record(
        db, user_id, gridfs_file_id, filename, file_size_bytes,
        max_queued_jobs_per_user, applies_to, is_pcs, english_variant,
    )