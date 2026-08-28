"""
app/agent/nodes/submit_document.py

PHASE 3B/3C REWRITE - interrupt() removed entirely. See
app/agent/state.py's module docstring for the full reasoning
(empirically confirmed incompatibility between interrupt()'s
resume-only-the-exact-paused-call semantics and suspension/detour).

This is now a PLAIN node, like every other handler: runs once per
invocation when classify_intent_node routes here, reads
pending_action_signal to know what THIS turn means (a fresh
attachment, an intake answer, continue, or cancel - classify_intent_
node has ALREADY determined this, possibly via one combined LLM
call, possibly deterministically - this node does no classification
of its own), updates persisted state, and either asks the next
question (a normal AIMessage, turn ends, no pause) or routes to
create_review_job via Command(goto=...) once complete.

Because there's no interrupt()/loop anymore, this node's own
completeness-check-then-Command(goto=) pattern is simpler than
before: no self-loop back to this same node is needed - a
genuinely fresh classify_intent_node pass happens naturally on the
NEXT turn, which will route back here again if there's still more
to ask.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.services.upload_service import abandon_staged_upload

_CLEAR_PENDING_FIELDS = {
    "pending_upload_id": None, "pending_filename": None,
    "pending_file_size_bytes": None, "pending_content_type": None,
    "intake_answers": None,
}


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


async def handle_submit_document_node(state: ChatState, runtime: Runtime[ChatContext]):
    db = runtime.context["db"]

    new_upload_id = state.get("new_upload_id")
    pending_upload_id = state.get("pending_upload_id")

    # A brand-new attachment (classify_intent_node has already ruled
    # out any conflict with a different pending upload before routing
    # here) - start fresh intake.
    if new_upload_id and (not pending_upload_id or new_upload_id == pending_upload_id):
        fresh_answers = {"applies_to": None, "is_pcs": None, "english_variant": None}
        return {
            "pending_upload_id": new_upload_id,
            "pending_filename": state.get("new_filename"),
            "pending_file_size_bytes": state.get("new_file_size_bytes"),
            "pending_content_type": state.get("new_content_type"),
            "intake_answers": fresh_answers,
            "messages": [AIMessage(content=_intake_question_text(fresh_answers))],
        }

    if not pending_upload_id:
        return {"messages": [AIMessage(content="I don't see a file attached yet - please upload a document to review.")]}

    signal = state.get("pending_action_signal") or {}
    action = signal.get("action")

    if action == "cancel_intake":
        await abandon_staged_upload(db, pending_upload_id)
        return {
            **_CLEAR_PENDING_FIELDS,
            "messages": [AIMessage(content="No problem - I've cancelled that. Let me know if you'd like to submit something else.")],
        }

    answers = dict(state.get("intake_answers") or {"applies_to": None, "is_pcs": None, "english_variant": None})

    if action == "intake_answer":
        if signal.get("applies_to") is not None:
            answers["applies_to"] = signal["applies_to"]
        if signal.get("is_pcs") is not None:
            answers["is_pcs"] = signal["is_pcs"]
        if signal.get("english_variant") is not None:
            answers["english_variant"] = signal["english_variant"]
    # action == "continue_intake": no new info, just re-ask what's missing below.

    if _intake_complete(answers):
        return Command(goto="create_review_job", update={"intake_answers": answers})

    return {"intake_answers": answers, "messages": [AIMessage(content=_intake_question_text(answers))]}