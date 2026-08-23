"""
app/services/chat_service.py

Coordinates one chat turn - persist the user's message, invoke the
graph, persist the assistant's reply. Directly adopts the confirmed
is_new_thread pattern from the sibling RFP Analyzer codebase's
chat_service.py: graph.aget_state(config) then check
snapshot.values == {} - verified via direct test against our real
installed langgraph before use, not copied on trust alone.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from pymongo.asynchronous.database import AsyncDatabase

from app.repository import message_repository
from app.agent.graph import build_graph
from app.agent.context import ChatContext

_DEFAULT_STATE_FIELDS = {
    "intent": None,
    "active_job_id": None,
    "pending_intake": None,
    "pending_file_bytes": None,
    "pending_filename": None,
    "turn_count": 0,
}
# Only ever applied on a genuinely NEW thread - applying these on
# every turn would silently overwrite real checkpointed values
# (active_job_id, pending_intake mid-flow, etc.) with their defaults,
# the exact confirmed bug this pattern exists to avoid.


async def send_message(
    db: AsyncDatabase,
    checkpointer: BaseCheckpointSaver,
    genai_client: object,
    session_id: str,
    user_id: str,
    message_text: str,
    file_bytes: bytes | None = None,
    filename: str | None = None,
) -> str:
    await message_repository.add_message(db, session_id, user_id, "user", message_text)

    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": session_id}}

    snapshot = await graph.aget_state(config)
    is_new_thread = not snapshot.values

    turn_input = {
        "messages": [HumanMessage(content=message_text)],
    }
    if is_new_thread:
        turn_input.update(_DEFAULT_STATE_FIELDS)
    if file_bytes is not None:
        # FIXED REAL BUG, confirmed by direct test: pending_file_bytes
        # has no special reducer (default "last write wins"), so
        # unconditionally including it in every turn's update - even
        # as None when no file was attached THIS turn - silently
        # overwrote a real file's bytes from a PRIOR turn still mid-
        # intake-flow. Exactly the class of bug the sibling RFP
        # Analyzer codebase's own postmortem describes for its
        # overwrite fields - only include this key in the update
        # dict when there's an ACTUAL new value to set, so LangGraph
        # preserves whatever the checkpoint already has otherwise.
        turn_input["pending_file_bytes"] = file_bytes
        turn_input["pending_filename"] = filename

    context: ChatContext = {"user_id": user_id, "db": db, "genai_client": genai_client}
    result = await graph.ainvoke(turn_input, config=config, context=context)

    assistant_messages = result.get("messages", [])
    assistant_text = assistant_messages[-1].content if assistant_messages else ""

    await message_repository.add_message(db, session_id, user_id, "assistant", assistant_text)
    return assistant_text