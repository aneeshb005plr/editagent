"""
app/services/chat_service.py

REBUILT per external review point 3: this module is now genuinely
CHANNEL/DOMAIN-NEUTRAL - it no longer knows anything about
"IntakeAnswers" or EditEdge-specific intake semantics. It detects an
interrupted thread and resumes with a generic payload (raw user
text + any newly-staged attachment reference); interpreting what
that means is entirely the graph/node's job (see app/agent/nodes/
submit_document.py). This is what lets future interrupt types
(finding clarification, document selection, approval flows, etc.)
get added without this file ever needing to change - exactly the
concern the external review raised: "That will become a problem
when you later have interrupts for [other things]."

This was SAFE to do only because of the graph.py redesign
(one interrupt() per node invocation, looping via a graph-level
conditional edge) - moving parsing back to the node layer would have
reintroduced the redundant-execution bug otherwise (confirmed via
direct measurement earlier in this build).
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.repository import message_repository
from app.schema.chat import ChatTurnRequest, ChatTurnResponse
from app.services.upload_service import stage_upload

logger = logging.getLogger("app.services.chat_service")

_DEFAULT_STATE_FIELDS = {
    "intent": None,
    "active_job_id": None,
    "pending_upload_id": None,
    "pending_filename": None,
    "pending_file_size_bytes": None,
    "pending_content_type": None,
    "intake_answers": None,
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
    context = {"user_id": user_id, "db": db, "genai_client": genai_client}

    snapshot = await graph.aget_state(config)
    is_new_thread = not snapshot.values
    # snapshot.interrupts (not .next) is the reliable signal across a
    # multi-turn interrupt sequence - confirmed via direct, isolated
    # test against our real installed langgraph (.next only reflects
    # the FIRST pause in a sequence, going back to empty afterward
    # even while genuinely still paused).
    is_interrupted = bool(snapshot.interrupts)
    previous_active_job_id = snapshot.values.get("active_job_id") if snapshot.values else None

    staged_upload_id = staged_filename = staged_size = staged_content_type = None
    if request.attachment is not None and request.attachment.file_bytes is not None:
        staged_upload_id, staged = await stage_upload(
            db, user_id, request.attachment.file_bytes, request.attachment.filename,
            request.attachment.content_type,
        )
        staged_filename = staged.filename
        staged_size = staged.size_bytes
        staged_content_type = staged.content_type

    try:
        if is_interrupted:
            # GENERIC resume payload - raw text plus any newly-staged
            # attachment reference. No EditEdge-specific parsing here.
            resume_payload = {
                "text": request.message_text,
                "new_upload_id": staged_upload_id,
                "new_filename": staged_filename,
                "new_size_bytes": staged_size,
                "new_content_type": staged_content_type,
            }
            result = await graph.ainvoke(Command(resume=resume_payload), config=config, context=context)
        else:
            turn_input = {"messages": [HumanMessage(content=request.message_text)]}
            if is_new_thread:
                turn_input.update(_DEFAULT_STATE_FIELDS)
            if staged_upload_id:
                turn_input["pending_upload_id"] = staged_upload_id
                turn_input["pending_filename"] = staged_filename
                turn_input["pending_file_size_bytes"] = staged_size
                turn_input["pending_content_type"] = staged_content_type
            result = await graph.ainvoke(turn_input, config=config, context=context)
    except Exception:
        logger.error("Graph invocation failed for session %s", session_id, exc_info=True)
        error_text = "I ran into an unexpected issue processing that - please try again."
        await message_repository.add_message(db, session_id, user_id, "assistant", error_text)
        return ChatTurnResponse(conversation_id=session_id, text=error_text, status="error")

    interrupts = result.get("__interrupt__")
    if interrupts:
        assistant_text = interrupts[0].value.get("text", "I need a bit more information to continue.")
        await message_repository.add_message(db, session_id, user_id, "assistant", assistant_text)
        return ChatTurnResponse(conversation_id=session_id, text=assistant_text, status="needs_input")

    assistant_messages = result.get("messages", [])
    assistant_text = assistant_messages[-1].content if assistant_messages else ""

    await message_repository.add_message(db, session_id, user_id, "assistant", assistant_text)

    new_active_job_id = result.get("active_job_id")
    status = "job_submitted" if new_active_job_id and new_active_job_id != previous_active_job_id else "ok"

    return ChatTurnResponse(conversation_id=session_id, text=assistant_text, status=status)