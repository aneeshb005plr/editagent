"""
app/agent/nodes/create_review_job.py

FIX (final Phase 3B/3C correction pass, items B/J/D): every path that
sets focused_job_id now ALSO clears focused_finding_id (a finding is
job-scoped; switching which job is focused must never leave a stale
finding reference from a DIFFERENT job behind) and, where a
submission is genuinely being recorded (fresh creation OR
reconciling a staged upload to an already-existing job), sets
last_submitted_job_id consistently too - not just on the single
"happy path" creation. Every response from this node also explicitly
sets requires_user_input=False - nothing here ever asks the user a
question; it reports outcomes (success, still-processing, error).

Everything else (the CAS reservation/reconciliation protections
themselves) is UNCHANGED from the prior hardening passes - this
correction only touches state-field consistency, not the underlying
safety logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.config import settings
from app.repositories import job_repository as jobs_repository
from app.services.job_service import TooManyQueuedJobsError, create_job_from_staged_upload
from app.jobs.storage import delete_file
from app.repositories.staged_upload_repository import (
    get_staged_upload,
    mark_consumed,
    release_reservation,
    set_consumed_job_id,
)
from app.rules.schema import AppliesTo, EnglishVariant
from app.schemas.staged_upload import StagedUpload, StagedUploadStatus
from app.services.upload_service import abandon_staged_upload

logger = logging.getLogger("app.agent.nodes.create_review_job")

_CLEAR_INTAKE_FIELDS = {
    "pending_upload_id": None, "pending_filename": None,
    "pending_file_size_bytes": None, "pending_content_type": None,
    "intake_answers": None,
}

_UPLOAD_GONE_MESSAGE = "That upload is no longer available - please attach the document again."
_STILL_PROCESSING_MESSAGE = "That submission is already being processed - please wait a moment."


def _focus_on_job(job_id: str) -> dict:
    """FIX item B/J: the ONE place focus gets set to a job_id as a
    consequence of a submission (fresh or reconciled) - always
    clears focused_finding_id alongside it, always sets
    last_submitted_job_id too. Used by every success/reconciliation
    path below so this can't drift between them."""
    return {"focused_job_id": job_id, "focused_finding_id": None, "last_submitted_job_id": job_id}


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
        return {**_focus_on_job(existing_job_id), **_CLEAR_INTAKE_FIELDS, "requires_user_input": False,
                "messages": [AIMessage(content="That review is already underway.")]}

    if may_release:
        released = await release_reservation(db, upload_id)
        if released:
            logger.warning("Upload %s reserved but no job found after an unexpected creation error - releasing", upload_id)
            await delete_file(db, staged.gridfs_file_id)
            return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False,
                    "messages": [AIMessage(content="That submission couldn't be completed - please try again.")]}

        refreshed = await jobs_repository.get_job_by_source_upload_id(db, upload_id)
        if refreshed is not None:
            refreshed_job_id, _ = refreshed
            await set_consumed_job_id(db, upload_id, refreshed_job_id)
            logger.info("Lost release CAS for upload %s, but found and linked the job that appeared in the meantime -> %s", upload_id, refreshed_job_id)
            return {**_focus_on_job(refreshed_job_id), **_CLEAR_INTAKE_FIELDS, "requires_user_input": False,
                    "messages": [AIMessage(content="That review is already underway.")]}

        logger.info("Lost release CAS for upload %s and no job found - leaving it for reconciliation elsewhere", upload_id)
        return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

    return None


async def create_review_job_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]
    user_id = runtime.context["user_id"]

    upload_id = state.get("pending_upload_id")
    answers = state.get("intake_answers") or {}

    staged = await get_staged_upload(db, upload_id)
    if staged is None:
        return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

    if staged.status == StagedUploadStatus.CONSUMED:
        if staged.consumed_job_id:
            return {**_focus_on_job(staged.consumed_job_id), **_CLEAR_INTAKE_FIELDS, "requires_user_input": False,
                    "messages": [AIMessage(content="That review is already underway.")]}
        resolved = await _reconcile_stuck_reservation(db, upload_id, staged, may_release=False)
        if resolved is not None:
            return resolved
        return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False, "messages": [AIMessage(content=_STILL_PROCESSING_MESSAGE)]}

    if staged.status == StagedUploadStatus.ABANDONED:
        return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

    if staged.expires_at < datetime.now(timezone.utc):
        await abandon_staged_upload(db, upload_id)
        return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False,
                "messages": [AIMessage(content="That upload has expired - please attach the document again.")]}

    reserved = await mark_consumed(db, upload_id, job_id=None)
    if not reserved:
        refreshed = await get_staged_upload(db, upload_id)
        if refreshed is None:
            return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

        if refreshed.status == StagedUploadStatus.CONSUMED:
            if refreshed.consumed_job_id:
                return {**_focus_on_job(refreshed.consumed_job_id), **_CLEAR_INTAKE_FIELDS, "requires_user_input": False,
                        "messages": [AIMessage(content="That review is already underway.")]}
            resolved = await _reconcile_stuck_reservation(db, upload_id, refreshed, may_release=False)
            if resolved is not None:
                return resolved
            return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False, "messages": [AIMessage(content=_STILL_PROCESSING_MESSAGE)]}

        return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

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
            origin_conversation_id=runtime.context.get("conversation_id"),
        )
    except TooManyQueuedJobsError as e:
        logger.info("Reserved upload %s but job creation was rejected (queue full) - releasing", upload_id)
        await release_reservation(db, upload_id)
        await delete_file(db, staged.gridfs_file_id)
        return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False, "messages": [AIMessage(content=e.user_message)]}
    except Exception:
        logger.error("Unexpected error creating job for upload %s", upload_id, exc_info=True)
        resolved = await _reconcile_stuck_reservation(db, upload_id, staged, may_release=True)
        if resolved is not None:
            return resolved
        return {**_CLEAR_INTAKE_FIELDS, "requires_user_input": False,
                "messages": [AIMessage(content="That submission couldn't be completed - please try again.")]}

    await set_consumed_job_id(db, upload_id, result.job_id)

    message = (
        "Got it - I'll queue this behind your current review; it'll start once that "
        "one finishes."
        if result.had_existing_active_job
        else f"Thanks - I've started reviewing {staged.filename}. "
        f"I'll let you know when it's done, or ask me to check status anytime."
    )

    return {
        **_focus_on_job(result.job_id),
        **_CLEAR_INTAKE_FIELDS,
        "requires_user_input": False,
        "messages": [AIMessage(content=message)],
    }