"""
app/agent/nodes/submit_document.py

FIX (final Phase 3B/3C correction pass, item A): this node now
returns Command(goto=...) EXCLUSIVELY for every path - no plain dict
returns mixed with Command anymore. Confirmed via direct test against
our real installed langgraph that Command(goto=END) works correctly
with no static edge present at all; graph.py's static edge to END
for this node has been removed to match (one routing mechanism only,
not two).

FIX (item D): sets requires_user_input explicitly on every path -
True only when actually asking an intake question (a fresh
attachment's first question, or re-asking what's still missing);
False for cancel, completion (routes elsewhere anyway), and the
"nothing attached" fallback message. This is the transient,
per-turn signal app/services/chat_service.py uses for
ChatTurnResponse.status - NOT derived from whether pending_upload_id
persists in state (that would be wrong, see chat_service.py's
docstring for the exact bug this replaces).
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.graph import END
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


async def handle_submit_document_node(state: ChatState, runtime: Runtime[ChatContext]) -> Command:
    db = runtime.context["db"]

    new_upload_id = state.get("new_upload_id")
    pending_upload_id = state.get("pending_upload_id")

    if new_upload_id and (not pending_upload_id or new_upload_id == pending_upload_id):
        fresh_answers = {"applies_to": None, "is_pcs": None, "english_variant": None}
        return Command(goto=END, update={
            "pending_upload_id": new_upload_id,
            "pending_filename": state.get("new_filename"),
            "pending_file_size_bytes": state.get("new_file_size_bytes"),
            "pending_content_type": state.get("new_content_type"),
            "intake_answers": fresh_answers,
            "requires_user_input": True,
            "messages": [AIMessage(content=_intake_question_text(fresh_answers))],
        })

    if not pending_upload_id:
        return Command(goto=END, update={
            "requires_user_input": False,
            "messages": [AIMessage(content="I don't see a file attached yet - please upload a document to review.")],
        })

    signal = state.get("pending_action_signal") or {}
    action = signal.get("action")

    if action == "cancel_intake":
        await abandon_staged_upload(db, pending_upload_id)
        return Command(goto=END, update={
            **_CLEAR_PENDING_FIELDS,
            "requires_user_input": False,
            "messages": [AIMessage(content="No problem - I've cancelled that. Let me know if you'd like to submit something else.")],
        })

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
        return Command(goto="create_review_job", update={"intake_answers": answers, "requires_user_input": False})

    return Command(goto=END, update={
        "intake_answers": answers,
        "requires_user_input": True,
        "messages": [AIMessage(content=_intake_question_text(answers))],
    })