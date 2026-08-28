"""
app/agent/nodes/attachment_conflict.py

Phase 3C: handles "a new attachment arrived while a DIFFERENT
upload is already mid-intake" - the MVP supports only one unfinished
staged intake per conversation, so this presents an explicit
replace/keep choice rather than silently picking one or creating two
unresolved intakes.

Reuses app/services/upload_service.py's existing CAS-safe
abandon_staged_upload() for both directions of cleanup (whichever
upload doesn't survive the choice) - no new CAS logic duplicated
here.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.services.upload_service import abandon_staged_upload

_CLEAR_CONFLICT_FIELDS = {
    "conflicting_upload_id": None, "conflicting_filename": None,
    "conflicting_file_size_bytes": None, "conflicting_content_type": None,
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


async def handle_attachment_conflict_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]

    pending_filename = state.get("pending_filename")
    conflicting_filename = state.get("conflicting_filename")
    signal = state.get("pending_action_signal") or {}
    action = signal.get("action")

    if action == "replace":
        # B is abandoned via the SAME CAS-safe path used everywhere
        # else - deletes the GridFS bytes only if this call genuinely
        # wins the STAGED->ABANDONED transition.
        await abandon_staged_upload(db, state.get("pending_upload_id"))

        fresh_answers = {"applies_to": None, "is_pcs": None, "english_variant": None}
        return {
            "pending_upload_id": state.get("conflicting_upload_id"),
            "pending_filename": state.get("conflicting_filename"),
            "pending_file_size_bytes": state.get("conflicting_file_size_bytes"),
            "pending_content_type": state.get("conflicting_content_type"),
            "intake_answers": fresh_answers,
            **_CLEAR_CONFLICT_FIELDS,
            "messages": [AIMessage(content=_intake_question_text(fresh_answers))],
        }

    if action == "keep":
        # C is discarded - it never became the pending upload, so it
        # needs its own cleanup (it's a genuinely separate staged
        # record from B, sitting there unused otherwise).
        await abandon_staged_upload(db, state.get("conflicting_upload_id"))
        return {
            **_CLEAR_CONFLICT_FIELDS,
            "messages": [AIMessage(content=(
                f"Okay, I'll keep working on {pending_filename}.\n\n"
                + _intake_question_text(state.get("intake_answers") or {})
            ))],
        }

    # Fresh conflict, or an unclear response to it - (re-)present the choice.
    return {
        "messages": [AIMessage(content=(
            f"You still have an unfinished submission for **{pending_filename}**.\n\n"
            f"Would you like to **replace** it with **{conflicting_filename}**, "
            f"or **keep** working on {pending_filename}?"
        ))],
    }