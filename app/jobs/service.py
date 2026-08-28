"""
app/jobs/service.py

FIX for external review point 3: submit_review_job() (REST path) now
compensates (deletes the just-uploaded GridFS file) if
repository.create_job() fails AFTER store_file() succeeded - closes
the exact same class of orphan window already fixed for
stage_upload() (app/services/upload_service.py) and for the
queue-capacity-check ordering (both from the previous hardening
pass), just at one more point in the same function.

FIX for external review point 2: create_job_from_staged_upload()'s
application-level "check then create" is only a fast-path
optimization now, NOT the actual idempotency guarantee - the REAL
guarantee is the partial unique index on source_upload_id
(repository.ensure_indexes()) plus catching the resulting
DuplicateKeyError here and resolving to the existing job. Two
concurrent calls for the same staged upload can both pass the
application-level pre-check (a genuine race, the same class the
external review flagged), but only one insert can actually succeed
at the database level - the loser catches DuplicateKeyError and
returns the winner's job instead of erroring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.jobs import repository
from app.jobs.schema import ReviewJob
from app.jobs.storage import delete_file, store_file
from app.rules.schema import AppliesTo, EnglishVariant

logger = logging.getLogger("app.jobs.service")


class TooManyQueuedJobsError(Exception):
    """FIX for external review point 3 (second production-hardening
    pass): user_message is a clean, generic string with no internal
    identifiers - callers building user-facing text (chat replies,
    HTTP error details) should use THIS, not str(e)/args, which
    still include the raw user_id for logging/debugging purposes."""

    def __init__(self, user_id: str, limit: int):
        self.user_id = user_id
        self.limit = limit
        self.user_message = (
            "You already have the maximum number of reviews queued or running. "
            "Please try again after one finishes."
        )
        super().__init__(
            f"User {user_id} already has {limit} active review(s) queued/running - "
            f"submit again once one completes."
        )


@dataclass
class SubmissionResult:
    job_id: str
    had_existing_active_job: bool


async def _check_queue_capacity(db: AsyncDatabase, user_id: str, max_queued_jobs_per_user: int) -> bool:
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
    had_existing = await _check_queue_capacity(db, user_id, max_queued_jobs_per_user)

    gridfs_file_id = await store_file(db, file_bytes, filename)

    job = ReviewJob(
        user_id=user_id, filename=filename, file_size_bytes=len(file_bytes),
        gridfs_file_id=gridfs_file_id, applies_to=applies_to, is_pcs=is_pcs,
        english_variant=english_variant,
    )
    try:
        job_id = await repository.create_job(db, job)
    except Exception:
        # FIX for external review point 3 - compensate rather than
        # leave an orphaned GridFS object with nothing tracking it.
        logger.error("Job record creation failed after GridFS upload succeeded - compensating", exc_info=True)
        await delete_file(db, gridfs_file_id)
        raise

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
    origin_conversation_id: str | None = None,
) -> SubmissionResult:
    if source_upload_id:
        existing = await repository.get_job_by_source_upload_id(db, source_upload_id)
        if existing is not None:
            existing_job_id, _ = existing
            return SubmissionResult(job_id=existing_job_id, had_existing_active_job=False)

    had_existing = await _check_queue_capacity(db, user_id, max_queued_jobs_per_user)

    job = ReviewJob(
        user_id=user_id, filename=filename, file_size_bytes=file_size_bytes,
        gridfs_file_id=gridfs_file_id, applies_to=applies_to, is_pcs=is_pcs,
        english_variant=english_variant, source_upload_id=source_upload_id,
        origin_conversation_id=origin_conversation_id,
    )

    try:
        job_id = await repository.create_job(db, job)
    except DuplicateKeyError:
        # FIX for external review point 2: lost a genuine race - a
        # concurrent call for the SAME source_upload_id won. The
        # database-level unique index (repository.ensure_indexes())
        # is what actually guarantees this can happen to at most one
        # loser, not the application-level pre-check above (which is
        # only a fast-path optimization, not the real guarantee).
        existing = await repository.get_job_by_source_upload_id(db, source_upload_id)
        if existing is not None:
            existing_job_id, _ = existing
            return SubmissionResult(job_id=existing_job_id, had_existing_active_job=False)
        raise  # shouldn't happen - a duplicate key means it must exist

    return SubmissionResult(job_id=job_id, had_existing_active_job=had_existing)