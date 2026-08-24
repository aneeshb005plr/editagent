"""
app/services/chat_service.py

PHASE 2 CHANGES:

- Accepts a PRE-BUILT graph now, not a checkpointer to build one
  from on every call. "Compile graph once" per the architecture doc
  - build_graph(checkpointer) should run ONCE at startup (main.py's
  lifespan, stored on app.state.chat_graph), not on every turn.

- Detects an INTERRUPTED thread via graph.aget_state(config).
  interrupts (confirmed via direct, isolated test against our real
  installed langgraph: snapshot.next only reliably reflects the
  FIRST interrupt in a sequence - it goes back to empty after the
  second and every subsequent pause within the same node's loop,
  even though the thread is genuinely still waiting. snapshot.
  interrupts stays non-empty for every pause in the sequence, empty
  exactly when actually complete - the reliable signal) and resumes
  via Command(resume=...) instead of a normal state update.

- Does the ONE-TIME IntakeAnswers parsing HERE, not inside the
  intake node's loop - confirmed necessary by direct measurement
  (see app/agent/nodes/submit_document.py's module docstring for the
  full reasoning: code between interrupt() calls re-executes on
  every replay, so an LLM call there would silently multiply). This
  function runs exactly once per real HTTP request, so parsing here
  is genuinely "once per reply," which is what the architecture
  doc's acceptance criterion requires.

- Stages any attachment BEFORE resuming too, not just on a fresh
  turn - a file attached mid-intake needs to flow into the resume
  payload's new_upload_id, not the normal turn_input path.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.agent.models import IntakeAnswers
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
    "turn_count": 0,
    "consecutive_unclear_count": 0,
}


async def _parse_intake_answer(genai_client, text: str) -> dict:
    """The ONE real place intake-answer LLM parsing happens - see
    module docstring. Returns a plain dict, never raises (falls back
    to an all-None dict on failure, same graceful-degradation
    pattern used throughout this codebase)."""

    structured = genai_client.with_structured_output(IntakeAnswers)
    try:
        parsed: IntakeAnswers = await structured.ainvoke([HumanMessage(content=text)])
    except Exception:
        logger.error("Intake answer parsing failed", exc_info=True)
        return {}
    return parsed.model_dump()


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
    # FIXED REAL BUG, confirmed by direct, isolated test against our
    # real installed langgraph: snapshot.next goes back to empty
    # after the SECOND (and every subsequent) interrupt within the
    # same node's loop, even though the thread is genuinely still
    # paused - only the FIRST interrupt in a sequence is reflected
    # in .next. snapshot.interrupts is the reliable signal - stays
    # non-empty for every pause in the sequence, confirmed via direct
    # test (3-call sequence: non-empty, non-empty, empty exactly when
    # actually complete).
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
            parsed_answers = await _parse_intake_answer(genai_client, request.message_text)
            resume_payload = {
                "parsed_answers": parsed_answers,
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