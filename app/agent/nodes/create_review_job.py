"""
app/agent/nodes/create_review_job.py

NEW per external review point 2 ("not insisting on a nested
StateGraph... a group of top-level intake nodes is also fine" - what
matters is one interrupt per node invocation + state persistence).
Separated from submit_document.py's asking/interpreting responsibility:
this node runs exactly once, reached via that node's own
Command(goto="create_review_job") once intake_answers are complete,
and does the actual job creation - idempotent via source_upload_id
(app/jobs/service.py's create_job_from_staged_upload()), so a retry
can't create a duplicate job pointing at the same GridFS file.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.config import settings
from app.jobs.service import TooManyQueuedJobsError, create_job_from_staged_upload
from app.repository.staged_upload_repository import get_staged_upload, mark_consumed
from app.rules.schema import AppliesTo, EnglishVariant
from app.services.upload_service import abandon_staged_upload

logger = logging.getLogger("app.agent.nodes.create_review_job")

_CLEAR_INTAKE_FIELDS = {
    "pending_upload_id": None, "pending_filename": None,
    "pending_file_size_bytes": None, "pending_content_type": None,
    "intake_answers": None,
}


async def create_review_job_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]
    user_id = runtime.context["user_id"]

    upload_id = state.get("pending_upload_id")
    answers = state.get("intake_answers") or {}

    staged = await get_staged_upload(db, upload_id)
    if staged is None:
        logger.error("Staged upload %s not found at job-creation time", upload_id)
        return {
            **_CLEAR_INTAKE_FIELDS,
            "messages": [AIMessage(content="I couldn't find that upload anymore - please attach the document again.")],
        }

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
        await abandon_staged_upload(db, upload_id)
        return {**_CLEAR_INTAKE_FIELDS, "messages": [AIMessage(content=str(e))]}

    await mark_consumed(db, upload_id, result.job_id)

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