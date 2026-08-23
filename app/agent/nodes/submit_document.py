"""
app/agent/nodes/submit_document.py

Handles BOTH submit_document and new_document intents (see
classify_intent.py's route_by_intent) - a new file arriving mid-
conversation is handled identically to a first submission; the job
system already correctly queues it behind any active job for this
user (confirmed, tested - see app/jobs/repository.py's
claim_next_pending_job()).
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.models import IntakeAnswers
from app.agent.state import ChatState, PendingIntake
from app.jobs.service import TooManyQueuedJobsError, submit_review_job
from app.rules.schema import AppliesTo, EnglishVariant

logger = logging.getLogger("app.agent.nodes.submit_document")


def _intake_question_text(pending: PendingIntake) -> str:
    missing = []
    if pending.get("applies_to") is None:
        missing.append("Is this a **general** proposal or an **audit/assurance** proposal?")
    if pending.get("applies_to") == "audit" and pending.get("is_pcs") is None:
        missing.append("Is it specifically a **PCS** (Private Company Services) audit?")
    if pending.get("english_variant") is None:
        missing.append("Should I review for **US English** or **Global (UK) English**?")
    return "Before I start the review, a couple of quick questions:\n\n" + "\n".join(
        f"- {q}" for q in missing
    )


def _intake_complete(pending: PendingIntake) -> bool:
    if pending.get("applies_to") is None or pending.get("english_variant") is None:
        return False
    if pending.get("applies_to") == "audit" and pending.get("is_pcs") is None:
        return False
    return True


async def handle_submit_document_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]
    user_id = runtime.context["user_id"]
    genai_client = runtime.context["genai_client"]

    pending_intake = state.get("pending_intake")
    file_bytes = state.get("pending_file_bytes")
    filename = state.get("pending_filename")

    # Starting fresh intake for a newly-attached file.
    if file_bytes and (pending_intake is None or pending_intake.get("filename") != filename):
        pending_intake = PendingIntake(
            stage="awaiting_answers", filename=filename,
            applies_to=None, is_pcs=None, english_variant=None,
        )
        return {
            "pending_intake": pending_intake,
            "messages": [AIMessage(content=_intake_question_text(pending_intake))],
        }

    if pending_intake is None:
        return {
            "messages": [AIMessage(content="I don't see a file attached yet - please upload a document to review.")]
        }

    structured = genai_client.with_structured_output(IntakeAnswers)
    last_message = state["messages"][-1].content
    try:
        parsed: IntakeAnswers = await structured.ainvoke([
            HumanMessage(content=last_message),
        ])
    except Exception:
        logger.error("Intake answer re-parsing failed in submit_document handler", exc_info=True)
        parsed = IntakeAnswers()

    if parsed.applies_to is not None:
        pending_intake["applies_to"] = parsed.applies_to
    if parsed.is_pcs is not None:
        pending_intake["is_pcs"] = parsed.is_pcs
    if parsed.english_variant is not None:
        pending_intake["english_variant"] = parsed.english_variant

    if not _intake_complete(pending_intake):
        return {
            "pending_intake": pending_intake,
            "messages": [AIMessage(content=_intake_question_text(pending_intake))],
        }

    try:
        result = await submit_review_job(
            db=db,
            user_id=user_id,
            file_bytes=file_bytes,
            filename=pending_intake["filename"],
            max_queued_jobs_per_user=5,  # TODO: thread through from settings
            applies_to=AppliesTo.AUDIT if pending_intake["applies_to"] == "audit" else AppliesTo.GENERAL,
            is_pcs=bool(pending_intake.get("is_pcs")),
            english_variant=EnglishVariant.GLOBAL if pending_intake["english_variant"] == "global" else EnglishVariant.US,
        )
    except TooManyQueuedJobsError as e:
        return {
            "pending_intake": None,
            "pending_file_bytes": None,
            "pending_filename": None,
            "messages": [AIMessage(content=str(e))],
        }

    message = (
        "Got it - I'll queue this behind your current review; it'll start once that "
        "one finishes."
        if result.had_existing_active_job
        else f"Thanks - I've started reviewing {pending_intake['filename']}. "
        f"I'll let you know when it's done, or ask me to check status anytime."
    )

    return {
        "active_job_id": result.job_id,
        "pending_intake": None,
        "pending_file_bytes": None,
        "pending_filename": None,
        "messages": [AIMessage(content=message)],
    }