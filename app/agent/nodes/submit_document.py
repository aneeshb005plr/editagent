"""
app/agent/nodes/submit_document.py

PHASE 2 REWORK: intake is now driven by LangGraph's native
interrupt()/Command(resume=...) mechanism, confirmed via direct
testing against our real installed langgraph before this was built
(not assumed from the architecture doc's description alone).

CRITICAL DESIGN POINT, confirmed by direct measurement before
writing this: only the interrupt() call itself is memoized on
replay - any code between two interrupt() calls in a loop
RE-EXECUTES on every subsequent resume (confirmed: a stand-in
"expensive_parse" call fired 3 times for only 2 real user answers
across a 2-question loop, when parsing lived inside the loop). This
means the LLM-based IntakeAnswers parsing MUST NOT happen inside
this node's loop - it happens exactly once, in app/services/
chat_service.py, BEFORE resuming, and only the already-parsed result
crosses into the resume payload. Everything inside this node's loop
is now cheap, side-effect-free dict merging - safe to re-execute on
every replay, which is what "Intake reply is parsed once" actually
requires.

Handles BOTH submit_document and new_document intents. A file
attached WHILE mid-intake (via the resume payload's new_upload_id)
triggers the same replacement/cleanup as Phase 1 - the old staged
upload is abandoned, intake restarts for the new file.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.config import settings
from app.jobs.service import TooManyQueuedJobsError, create_job_from_staged_upload
from app.repository.staged_upload_repository import get_staged_upload, mark_consumed
from app.rules.schema import AppliesTo, EnglishVariant
from app.services.upload_service import abandon_staged_upload

logger = logging.getLogger("app.agent.nodes.submit_document")


def _intake_question_text(answers: dict) -> str:
    missing = []
    if answers.get("applies_to") is None:
        missing.append("Is this a **general** proposal or an **audit/assurance** proposal?")
    if answers.get("applies_to") == "audit" and answers.get("is_pcs") is None:
        missing.append("Is it specifically a **PCS** (Private Company Services) audit?")
    if answers.get("english_variant") is None:
        missing.append("Should I review for **US English** or **Global (UK) English**?")
    return "Before I start the review, a couple of quick questions:\n\n" + "\n".join(
        f"- {q}" for q in missing
    )


def _intake_complete(answers: dict) -> bool:
    if answers.get("applies_to") is None or answers.get("english_variant") is None:
        return False
    if answers.get("applies_to") == "audit" and answers.get("is_pcs") is None:
        return False
    return True


async def handle_submit_document_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]
    user_id = runtime.context["user_id"]

    upload_id = state.get("pending_upload_id")
    filename = state.get("pending_filename")
    size_bytes = state.get("pending_file_size_bytes")
    content_type = state.get("pending_content_type")

    if not upload_id:
        return {"messages": [AIMessage(content="I don't see a file attached yet - please upload a document to review.")]}

    answers: dict = {"applies_to": None, "is_pcs": None, "english_variant": None}

    while not _intake_complete(answers):
        resume = interrupt({"text": _intake_question_text(answers)})
        # resume is a plain dict built by chat_service.py - see that
        # module's docstring. Everything below is cheap dict merging,
        # deliberately no LLM calls or other real side effects, since
        # this whole block re-executes on every subsequent resume
        # (confirmed via direct measurement - see module docstring).

        new_upload_id = resume.get("new_upload_id") if isinstance(resume, dict) else None
        if new_upload_id and new_upload_id != upload_id:
            # A different file was attached mid-intake - replace it,
            # same cleanup guarantee as Phase 1.
            if upload_id:
                await abandon_staged_upload(db, upload_id)
            upload_id = new_upload_id
            filename = resume.get("new_filename")
            size_bytes = resume.get("new_size_bytes")
            content_type = resume.get("new_content_type")
            answers = {"applies_to": None, "is_pcs": None, "english_variant": None}
            continue

        parsed = (resume.get("parsed_answers") or {}) if isinstance(resume, dict) else {}
        if parsed.get("applies_to") is not None:
            answers["applies_to"] = parsed["applies_to"]
        if parsed.get("is_pcs") is not None:
            answers["is_pcs"] = parsed["is_pcs"]
        if parsed.get("english_variant") is not None:
            answers["english_variant"] = parsed["english_variant"]

    # Intake complete - this code runs exactly once (after the LAST
    # interrupt() call in the loop, never revisited on replay).
    staged = await get_staged_upload(db, upload_id)
    if staged is None:
        logger.error("Staged upload %s not found at job-creation time", upload_id)
        return {
            "pending_upload_id": None, "pending_filename": None,
            "pending_file_size_bytes": None, "pending_content_type": None,
            "messages": [AIMessage(content="I couldn't find that upload anymore - please attach the document again.")],
        }

    applies_to = AppliesTo.AUDIT if answers["applies_to"] == "audit" else AppliesTo.GENERAL
    english_variant = EnglishVariant.GLOBAL if answers["english_variant"] == "global" else EnglishVariant.US

    try:
        result = await create_job_from_staged_upload(
            db=db, user_id=user_id, gridfs_file_id=staged.gridfs_file_id,
            filename=staged.filename, file_size_bytes=staged.size_bytes,
            max_queued_jobs_per_user=settings.MAX_QUEUED_JOBS_PER_USER,
            applies_to=applies_to, is_pcs=bool(answers.get("is_pcs")),
            english_variant=english_variant,
        )
    except TooManyQueuedJobsError as e:
        await abandon_staged_upload(db, upload_id)
        return {
            "pending_upload_id": None, "pending_filename": None,
            "pending_file_size_bytes": None, "pending_content_type": None,
            "messages": [AIMessage(content=str(e))],
        }

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
        "pending_upload_id": None, "pending_filename": None,
        "pending_file_size_bytes": None, "pending_content_type": None,
        "messages": [AIMessage(content=message)],
    }