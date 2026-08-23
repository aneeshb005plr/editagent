"""
app/repository/message_repository.py

Persists chat messages OUTSIDE the LangGraph checkpoint - matches the
confirmed pattern from the sibling RFP Analyzer codebase's
MessageRepository. The checkpointer is for LangGraph's own state
resumption; this is for "show this user their chat history" in a
UI, which isn't necessarily a convenient shape to reconstruct from
checkpoint internals.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pymongo.asynchronous.database import AsyncDatabase

_COLLECTION = "chat_messages"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def add_message(
    db: AsyncDatabase, session_id: str, user_id: str, role: str, content: str
) -> None:
    await db[_COLLECTION].insert_one({
        "session_id": session_id,
        "user_id": user_id,
        "role": role,
        "content": content,
        "timestamp": _utcnow(),
    })


async def get_messages_for_session(db: AsyncDatabase, session_id: str) -> list[dict]:
    cursor = db[_COLLECTION].find({"session_id": session_id}).sort("timestamp", 1)
    return await cursor.to_list(length=None)