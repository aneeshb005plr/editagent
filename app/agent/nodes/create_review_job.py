"""
app/agent/nodes/create_review_job.py

FIX for external review points 1-2 (second production-hardening
pass): "CONSUMED + consumed_job_id=None" is now treated as a genuine
RECONCILIATION state, not just a "please wait" state, in every place
it's encountered - both right after an unexpected (non-
TooManyQueuedJobsError) exception from create_job_from_staged_upload(),
and on any later re-entry that finds a record already stuck this way
(e.g. because a prior set_consumed_job_id() call itself failed after
the job was actually created).

_reconcile_stuck_reservation() is the shared logic: look up the real
job via source_upload_id FIRST. If found, repair the link and return
it - this is exactly why the unique source_upload_id constraint from
the prior hardening pass matters, since it's what makes "the job DID
get created despite an error being reported to us" a real, expected
possibility rather than something we can hand-wave away.

Two different "not found" behaviors depending on WHO is asking,
deliberately:
  - Right after OUR OWN failed creation attempt: we know with
    certainty our attempt is over (succeeded-but-reported-error, or
    genuinely failed) - not found means genuinely failed, so we
    safely release the reservation and clean up.
  - On a later, independent re-entry (not from our own attempt):
    another invocation may be mid-creation RIGHT NOW - not found here
    is genuinely ambiguous, so we do NOT release (that could race
    against a real, in-progress creation and delete a file it's about
    to need) - we ask the user to wait, same as before, but only
    AFTER first checking whether it's already resolved.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.config import settings
from app.jobs import repository as jobs_repository
from app.jobs.service import TooManyQueuedJobsError, create_job_from_staged_upload
from app.jobs.storage import delete_file
from app.repository.staged_upload_repository import (
    get_staged_upload,
    mark_consumed,
    release_reservation,
    set_consumed_job_id,
)
from app.rules.schema import AppliesTo, EnglishVariant
from app.schema.staged_upload import StagedUpload, StagedUploadStatus
from app.services.upload_service import abandon_staged_upload

logger = logging.getLogger("app.agent.nodes.create_review_job")

_CLEAR_INTAKE_FIELDS = {
    "pending_upload_id": None, "pending_filename": None,
    "pending_file_size_bytes": None, "pending_content_type": None,
    "intake_answers": None,
}

_UPLOAD_GONE_MESSAGE = "That upload is no longer available - please attach the document again."
_STILL_PROCESSING_MESSAGE = "That submission is already being processed - please wait a moment."


async def _reconcile_stuck_reservation(db, upload_id: str, staged: StagedUpload, may_release: bool) -> dict | None:
    """Shared reconciliation for a CONSUMED-with-no-job_id record.
    Returns a state-update dict if resolved, or None if the caller
    should treat it as still-ambiguous (only meaningful when
    may_release=False)."""

    existing = await jobs_repository.get_job_by_source_upload_id(db, upload_id)
    if existing is not None:
        existing_job_id, _ = existing
        await set_consumed_job_id(db, upload_id, existing_job_id)
        logger.info("Reconciled stuck reservation for upload %s -> job %s", upload_id, existing_job_id)
        return {"active_job_id": existing_job_id, **_CLEAR_INTAKE_FIELDS,
                "messages": [AIMessage(content="That review is already underway.")]}

    if may_release:
        # FIX per explicit review request: release_reservation() is a
        # CAS returning bool - the return value was previously never
        # checked, so the GridFS file was deleted UNCONDITIONALLY even
        # when this call LOST the race (e.g. another invocation
        # created and linked the real job in the window between our
        # lookup above and this release attempt) - deleting a file a
        # live job still needs.
        released = await release_reservation(db, upload_id)
        if released:
            # We won the CAS - no one else can be holding or using
            # this file anymore, safe to delete.
            logger.warning("Upload %s reserved but no job found after an unexpected creation error - releasing", upload_id)
            await delete_file(db, staged.gridfs_file_id)
            return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content="That submission couldn't be completed - please try again.")]}

        # Lost the CAS - someone else already transitioned this
        # record between our lookup and this attempt. Re-check for a
        # job rather than assume anything: it may have just been
        # created and linked (recover it), or abandoned by something
        # else entirely (nothing to delete, we don't own the file).
        refreshed = await jobs_repository.get_job_by_source_upload_id(db, upload_id)
        if refreshed is not None:
            refreshed_job_id, _ = refreshed
            await set_consumed_job_id(db, upload_id, refreshed_job_id)
            logger.info("Lost release CAS for upload %s, but found and linked the job that appeared in the meantime -> %s", upload_id, refreshed_job_id)
            return {"active_job_id": refreshed_job_id, **_CLEAR_INTAKE_FIELDS,
                    "messages": [AIMessage(content="That review is already underway.")]}

        logger.info("Lost release CAS for upload %s and no job found - leaving it for reconciliation elsewhere", upload_id)
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

    return None


async def create_review_job_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]
    user_id = runtime.context["user_id"]

    upload_id = state.get("pending_upload_id")
    answers = state.get("intake_answers") or {}

    staged = await get_staged_upload(db, upload_id)
    if staged is None:
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

    if staged.status == StagedUploadStatus.CONSUMED:
        if staged.consumed_job_id:
            return {"active_job_id": staged.consumed_job_id, **_CLEAR_INTAKE_FIELDS,
                    "messages": [AIMessage(content="That review is already underway.")]}
        # FIX for external review point 2: reconcile FIRST, rather
        # than immediately assuming "still processing" - a prior
        # set_consumed_job_id() call may have failed even though the
        # job itself was created successfully.
        resolved = await _reconcile_stuck_reservation(db, upload_id, staged, may_release=False)
        if resolved is not None:
            return resolved
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=_STILL_PROCESSING_MESSAGE)]}

    if staged.status == StagedUploadStatus.ABANDONED:
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

    if staged.expires_at < datetime.now(timezone.utc):
        await abandon_staged_upload(db, upload_id)
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content="That upload has expired - please attach the document again.")]}

    reserved = await mark_consumed(db, upload_id, job_id=None)
    if not reserved:
        # FIX per explicit review request: previously only handled
        # CONSUMED+job_id-present here - CONSUMED+job_id=None (a
        # CONCURRENT invocation legitimately holds this reservation
        # right now and may still be creating the job) incorrectly
        # fell through to "upload gone". Reuses the existing
        # _reconcile_stuck_reservation(may_release=False) - it never
        # releases/deletes here, since we do NOT own this reservation.
        refreshed = await get_staged_upload(db, upload_id)
        if refreshed is None:
            return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

        if refreshed.status == StagedUploadStatus.CONSUMED:
            if refreshed.consumed_job_id:
                return {"active_job_id": refreshed.consumed_job_id, **_CLEAR_INTAKE_FIELDS,
                        "messages": [AIMessage(content="That review is already underway.")]}
            resolved = await _reconcile_stuck_reservation(db, upload_id, refreshed, may_release=False)
            if resolved is not None:
                return resolved
            return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=_STILL_PROCESSING_MESSAGE)]}

        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

    applies_to = AppliesTo.AUDIT if answers.get("applies_to") == "audit" else AppliesTo.GENERAL
    english_variant = EnglishVariant.GLOBAL if answers.get("english_variant") == "global" else EnglishVariant.US

    try:
        result = await create_job_from_staged_upload(
            db=db, user_id=user_id, gridfs_file_id=staged.gridfs_file_id,
            filename=staged.filename, file_size_bytes=staged.size_bytes,
            max_queued_jobs_per_user=settings.MAX_QUEUED_JOBS_PER_USER,
            applies_to=applies_to, is_pcs=bool(answers.get("is_pcs")),
            english_variant=english_variant,
            source_upload_id=upload_id,
        )
    except TooManyQueuedJobsError as e:
        logger.info("Reserved upload %s but job creation was rejected (queue full) - releasing", upload_id)
        await release_reservation(db, upload_id)
        await delete_file(db, staged.gridfs_file_id)
        # FIX for external review point 3 (queue-cap message leaking
        # user_id): use the clean, generic user-facing message rather
        # than str(e), which includes the internal user_id value.
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=e.user_message)]}
    except Exception:
        # FIX for external review point 1 - THE most important
        # remaining issue per the review: any OTHER exception here
        # (Mongo transient failure, network problem, etc.) previously
        # had NO recovery at all - the reservation would be stuck as
        # CONSUMED-with-no-job forever, invisible to cleanup (which
        # only examines STAGED records). Now reconciles: checks
        # whether the job actually got created despite the error
        # (a real possibility - e.g. the insert committed but the
        # driver reported a network failure before we saw success)
        # before concluding it genuinely failed.
        logger.error("Unexpected error creating job for upload %s", upload_id, exc_info=True)
        resolved = await _reconcile_stuck_reservation(db, upload_id, staged, may_release=True)
        if resolved is not None:
            return resolved
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content="That submission couldn't be completed - please try again.")]}

    await set_consumed_job_id(db, upload_id, result.job_id)

    message = (
        "Got it - I'll queue this behind your current review; it'll start once that "
        "one finishes."
        if result.had_existing_active_job
        else f"Thanks - I've started reviewing {staged.filename}. "
        f"I'll let you know when it's done, or ask me to check status anytime."
    )

    return {
        "active_job_id": result.job_id,
        **_CLEAR_INTAKE_FIELDS,
        "messages": [AIMessage(content=message)],
    }