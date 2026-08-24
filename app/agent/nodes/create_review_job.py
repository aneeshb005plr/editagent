"""
app/agent/nodes/create_review_job.py

FIX for external review point 5: previously only checked whether the
staged upload record existed at all - an ABANDONED record (GridFS
bytes already deleted) or an EXPIRED-but-still-STAGED one could
theoretically become a ReviewJob pointing at a file that's gone or
about to be cleaned up. Now distinguishes every real state:
CONSUMED (resolve to the existing job), ABANDONED (reject clearly),
expired-STAGED (reject, opportunistically clean up), missing (ask to
re-upload), and only a genuinely valid STAGED record proceeds.

TWO-PHASE RESERVE closes a real race window found while implementing
this: if the record were only marked consumed AFTER a job is
created, a concurrent cleanup sweep could win the CAS on the
STAGED->ABANDONED transition and delete the GridFS file in the brief
window between "job created" and "upload marked consumed" - the job
would then point at a file that no longer exists. Fixed by
RESERVING first (CAS STAGED->CONSUMED with job_id=None, BEFORE the
job exists) - the instant that reservation is won, the record is no
longer STAGED, so cleanup's own CAS can never match it again. The
real job_id is filled in afterward via set_consumed_job_id(), which
is safe as a plain (non-CAS) update since by then no one else can be
racing against this specific record.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.config import settings
from app.jobs.service import TooManyQueuedJobsError, create_job_from_staged_upload
from app.jobs.storage import delete_file
from app.repository.staged_upload_repository import (
    get_staged_upload,
    mark_consumed,
    release_reservation,
    set_consumed_job_id,
)
from app.rules.schema import AppliesTo, EnglishVariant
from app.schema.staged_upload import StagedUploadStatus
from app.services.upload_service import abandon_staged_upload

logger = logging.getLogger("app.agent.nodes.create_review_job")

_CLEAR_INTAKE_FIELDS = {
    "pending_upload_id": None, "pending_filename": None,
    "pending_file_size_bytes": None, "pending_content_type": None,
    "intake_answers": None,
}

_UPLOAD_GONE_MESSAGE = "That upload is no longer available - please attach the document again."


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
            # Already consumed - a retry of this exact turn (e.g. the
            # response was lost after this node already ran). Resolve
            # to the existing job rather than erroring.
            return {"active_job_id": staged.consumed_job_id, **_CLEAR_INTAKE_FIELDS,
                    "messages": [AIMessage(content="That review is already underway.")]}
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content="That submission is already being processed - please wait a moment.")]}

    if staged.status == StagedUploadStatus.ABANDONED:
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=_UPLOAD_GONE_MESSAGE)]}

    if staged.expires_at < datetime.now(timezone.utc):
        await abandon_staged_upload(db, upload_id)
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content="That upload has expired - please attach the document again.")]}

    # Genuinely STAGED and not expired - RESERVE before creating the
    # job (see module docstring for why this ordering matters).
    reserved = await mark_consumed(db, upload_id, job_id=None)
    if not reserved:
        # Lost the race to a concurrent cleanup sweep (or another
        # invocation) between our read above and this CAS attempt.
        refreshed = await get_staged_upload(db, upload_id)
        if refreshed and refreshed.status == StagedUploadStatus.CONSUMED and refreshed.consumed_job_id:
            return {"active_job_id": refreshed.consumed_job_id, **_CLEAR_INTAKE_FIELDS,
                    "messages": [AIMessage(content="That review is already underway.")]}
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
        # We already reserved (CONSUMED, no job) but couldn't create
        # the job - release the reservation properly. FIXED REAL BUG
        # found while writing this: mark_abandoned()'s CAS requires
        # status==STAGED, which no longer matches here (we're
        # CONSUMED) - calling it would have silently no-op'd, leaving
        # this record stuck forever. release_reservation() is the
        # correctly-scoped transition for this specific state.
        logger.info("Reserved upload %s but job creation was rejected (queue full) - releasing", upload_id)
        await release_reservation(db, upload_id)
        await delete_file(db, staged.gridfs_file_id)
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=str(e))]}

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