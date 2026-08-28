"""
app/services/job_resolver.py

Phase 3B: ONE clear job-resolution path, rather than "which job did
the user mean" logic scattered across nodes. Every caller (check_status,
finding_followup, and later phases) should go through this, not
reimplement its own guessing.

Typed result contract (ResolutionStatus, JobResolutionResult) lives
in app/schemas/job_resolution.py - this file holds only the service
logic, matching the established schema/logic separation used
throughout this project.

Resolution precedence, exactly as specified:
1. An explicit job_id (from a structured action - not yet produced by
   any current caller, but the parameter exists so this doesn't need
   another signature change when Phase 3E's action contract arrives).
2. An explicit, unambiguous filename reference given in the current
   turn.
3. The current focused_job_id, when nothing more specific was given.
4. Jobs submitted from THIS conversation (bounded, targeted query) -
   only when there's exactly one, since more than one is genuinely
   ambiguous.

Never guesses when multiple jobs are plausible - returns AMBIGUOUS
with a bounded list of candidates for the caller to present, using a
timestamp as the safe, user-facing discriminator per the Phase 3
doc's own example.

Ownership-scoped throughout: every lookup is scoped to user_id by
construction (part of the query itself, or checked immediately after
a lookup), and a malformed job_id is handled the same way as a
nonexistent or someone-else's job - NOT_FOUND, no distinguishing
information leaked (Scenario 8).

Bounded throughout: every query uses an explicit limit, never
to_list(length=None) (Scenario 9) - safe even with hundreds/thousands
of historical jobs, since nothing here ever loads a user's full
history.
"""

from __future__ import annotations

from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from app.repositories import job_repository as jobs_repository
from app.schemas.job import ReviewJob
from app.schemas.job_resolution import JobResolutionResult, ResolutionStatus

_RECENT_CONVERSATION_JOBS_LIMIT = 10
_FILENAME_MATCH_LIMIT = 10


async def _lookup_owned_job(db: AsyncDatabase, user_id: str, job_id: str) -> ReviewJob | None:
    try:
        job = await jobs_repository.get_job(db, job_id)
    except InvalidId:
        return None
    if job is None or job.user_id != user_id:
        return None
    return job


async def resolve_job_reference(
    db: AsyncDatabase,
    user_id: str,
    conversation_id: str,
    explicit_job_id: str | None = None,
    filename_reference: str | None = None,
    focused_job_id: str | None = None,
) -> JobResolutionResult:
    # 1. Explicit job_id - highest precedence, always wins if valid and owned.
    if explicit_job_id:
        job = await _lookup_owned_job(db, user_id, explicit_job_id)
        if job is not None:
            return JobResolutionResult(status=ResolutionStatus.RESOLVED, job_id=explicit_job_id, job=job)
        return JobResolutionResult(status=ResolutionStatus.NOT_FOUND)

    # 2. Explicit filename reference - MUST terminate the resolution
    # path here (FIX, final correction pass, item E: previously fell
    # through to focused_job_id/conversation context on zero matches,
    # meaning an explicit reference to a document that doesn't exist
    # could silently resolve to a COMPLETELY different job - a real
    # bug, confirmed by re-reading this exact code path).
    if filename_reference:
        matches = await jobs_repository.list_jobs_by_filename(
            db, user_id, filename_reference, limit=_FILENAME_MATCH_LIMIT
        )
        if len(matches) == 1:
            job_id, job = matches[0]
            return JobResolutionResult(status=ResolutionStatus.RESOLVED, job_id=job_id, job=job)
        if len(matches) > 1:
            # Safe discriminator: created_at timestamp, per the Phase 3 doc's
            # own example ("recent timestamp or short review reference") -
            # each candidate's ReviewJob.created_at is available to the
            # caller for presenting the clarification.
            return JobResolutionResult(status=ResolutionStatus.AMBIGUOUS, candidates=matches)
        # Zero matches - an EXPLICIT reference to something that
        # doesn't exist must terminate as NOT_FOUND, never fall
        # through to focused_job_id or conversation context (that
        # would silently substitute a different job the user never
        # asked about).
        return JobResolutionResult(status=ResolutionStatus.NOT_FOUND)

    # 3. Current focus, if nothing more specific was given.
    if focused_job_id:
        job = await _lookup_owned_job(db, user_id, focused_job_id)
        if job is not None:
            return JobResolutionResult(status=ResolutionStatus.RESOLVED, job_id=focused_job_id, job=job)
        # Focus pointed at something no longer valid/owned - don't
        # silently fall through to guessing something else either;
        # treat as no usable context.

    # 4. Jobs from this conversation - only if unambiguous.
    conversation_jobs = await jobs_repository.list_jobs_by_conversation(
        db, user_id, conversation_id, limit=_RECENT_CONVERSATION_JOBS_LIMIT
    )
    if len(conversation_jobs) == 1:
        job_id, job = conversation_jobs[0]
        return JobResolutionResult(status=ResolutionStatus.RESOLVED, job_id=job_id, job=job)
    if len(conversation_jobs) > 1:
        return JobResolutionResult(status=ResolutionStatus.AMBIGUOUS, candidates=conversation_jobs)

    return JobResolutionResult(status=ResolutionStatus.NO_CONTEXT)