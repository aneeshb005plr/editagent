"""
app/services/chat_service.py

Every turn is a plain graph.ainvoke() with normal input -
classify_intent_node runs fresh on every single turn and makes the
routing decision, including whether a pending intake should even be
touched this turn (interrupt()-based intake was removed in the
original Phase 3B/3C pass - see app/agent/state.py's docstring for
the full empirical reasoning).

FIX (final Phase 3B/3C correction pass, item D): ChatTurnResponse.
status="needs_input" is now derived from requires_user_input - a
TRANSIENT, per-turn signal explicitly reset to False here on EVERY
turn before the graph runs, then set True only by whichever node
actually asks something this turn (an intake question, a replace/
keep choice). Previously this was derived from whether
pending_upload_id/conflicting_upload_id were PRESENT in state - which
was wrong: a suspended intake can legitimately persist across many
turns that have nothing to do with it (status checks, other reviews),
and none of those responses were "asking the user for input" just
because some OTHER unrelated workflow happened to still be pending.
Confirmed via direct test against our real installed langgraph that
this reset-then-selectively-set pattern works correctly - a node
that doesn't touch the field doesn't inherit a stale True from an
earlier turn.

new_upload_id/new_filename/new_file_size_bytes/new_content_type are
explicitly set on EVERY turn - to a freshly-staged upload's info if
one was attached this turn, or to None if not. This is the
per-turn-only signal classify_intent_node uses to distinguish "a new
attachment arrived just now" from "pending_upload_id is just sitting
there from an earlier, still-unfinished turn" - never left stale.

conversation_id is passed into ChatContext (immutable, not
checkpointed) so create_review_job_node can stamp
origin_conversation_id on chat-created jobs, and check_status/
finding_followup can use it for conversation-scoped job resolution.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage

from app.repositories import message_repository
from app.schemas.chat import ChatTurnRequest, ChatTurnResponse
from app.services.upload_service import stage_upload

logger = logging.getLogger("app.services.chat_service")

_DEFAULT_STATE_FIELDS = {
    "intent": None,
    "focused_job_id": None,
    "focused_finding_id": None,
    "last_submitted_job_id": None,
    "last_completed_job_id": None,
    "new_upload_id": None,
    "new_filename": None,
    "new_file_size_bytes": None,
    "new_content_type": None,
    "pending_upload_id": None,
    "pending_filename": None,
    "pending_file_size_bytes": None,
    "pending_content_type": None,
    "intake_answers": None,
    "conflicting_upload_id": None,
    "conflicting_filename": None,
    "conflicting_file_size_bytes": None,
    "conflicting_content_type": None,
    "pending_action_signal": None,
    "requires_user_input": False,
    "turn_count": 0,
    "consecutive_unclear_count": 0,
}


async def send_message(
    graph,
    db,
    genai_client,
    user_id: str,
    request: ChatTurnRequest,
) -> ChatTurnResponse:
    session_id = request.conversation_id or str(uuid.uuid4())

    await message_repository.add_message(db, session_id, user_id, "user", request.message_text)

    config = {"configurable": {"thread_id": session_id}}
    context = {"user_id": user_id, "db": db, "genai_client": genai_client, "conversation_id": session_id}

    snapshot = await graph.aget_state(config)
    is_new_thread = not snapshot.values
    previous_last_submitted_job_id = snapshot.values.get("last_submitted_job_id") if snapshot.values else None

    turn_input = {"messages": [HumanMessage(content=request.message_text)]}
    if is_new_thread:
        turn_input.update(_DEFAULT_STATE_FIELDS)

    # ALWAYS explicitly set, every turn - never left stale from a
    # previous turn (see module docstring for why this matters).
    turn_input["requires_user_input"] = False

    if request.attachment is not None and request.attachment.file_bytes is not None:
        staged_upload_id, staged = await stage_upload(
            db, user_id, request.attachment.file_bytes, request.attachment.filename,
            request.attachment.content_type,
        )
        turn_input["new_upload_id"] = staged_upload_id
        turn_input["new_filename"] = staged.filename
        turn_input["new_file_size_bytes"] = staged.size_bytes
        turn_input["new_content_type"] = staged.content_type
    else:
        turn_input["new_upload_id"] = None
        turn_input["new_filename"] = None
        turn_input["new_file_size_bytes"] = None
        turn_input["new_content_type"] = None

    try:
        result = await graph.ainvoke(turn_input, config=config, context=context)
    except Exception:
        logger.error("Graph invocation failed for session %s", session_id, exc_info=True)
        error_text = "I ran into an unexpected issue processing that - please try again."
        await message_repository.add_message(db, session_id, user_id, "assistant", error_text)
        return ChatTurnResponse(conversation_id=session_id, text=error_text, status="error")

    assistant_messages = result.get("messages", [])
    assistant_text = assistant_messages[-1].content if assistant_messages else ""

    await message_repository.add_message(db, session_id, user_id, "assistant", assistant_text)

    new_last_submitted_job_id = result.get("last_submitted_job_id")
    if new_last_submitted_job_id and new_last_submitted_job_id != previous_last_submitted_job_id:
        status = "job_submitted"
    elif result.get("requires_user_input"):
        status = "needs_input"
    else:
        status = "ok"

    return ChatTurnResponse(conversation_id=session_id, text=assistant_text, status=status)