"""
app/jobs/service.py

The submission entry point - what a future API route (not yet built)
should call to enqueue a review, rather than talking to storage.py/
repository.py directly. Ties together:
- MAX_QUEUED_JOBS_PER_USER enforcement (a config setting that existed
  since early in this build but was never actually wired to anything
  until now)
- GridFS storage of the raw upload
- Job record creation

Deliberately does NOT reject or block a second submission just
because the user already has an active job - it QUEUES it (matches
the confirmed design: FIFO processing via claim_next_pending_job's
sort by created_at naturally handles this, no separate scheduling
logic needed). The return value tells the caller whether the user
already had one active, so a future conversational layer can inform
them ("I'll queue this behind your current review") - deciding
whether to surface that, and any "replace instead?" UX, belongs to
that future layer, not this one.
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
    # True if the user already had a PENDING/RUNNING job before this
    # one was created - signal for a future caller to mention
    # queueing, not acted on here.


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
    """Raises TooManyQueuedJobsError if the user is already at their
    queue limit - this is a real, enforced cap, not just a config
    value sitting unused (confirmed: MAX_QUEUED_JOBS_PER_USER existed
    in config.py since early in this build with nothing checking it
    until this function)."""

    active_count = await repository.count_active_jobs_for_user(db, user_id)
    if active_count >= max_queued_jobs_per_user:
        raise TooManyQueuedJobsError(user_id, max_queued_jobs_per_user)

    had_existing = active_count > 0

    gridfs_file_id = await store_file(db, file_bytes, filename)

    job = ReviewJob(
        user_id=user_id,
        filename=filename,
        file_size_bytes=len(file_bytes),
        gridfs_file_id=gridfs_file_id,
        applies_to=applies_to,
        is_pcs=is_pcs,
        english_variant=english_variant,
    )
    job_id = await repository.create_job(db, job)

    return SubmissionResult(job_id=job_id, had_existing_active_job=had_existing)