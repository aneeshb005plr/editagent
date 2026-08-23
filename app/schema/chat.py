"""
app/schema/chat.py

Request/response models for the chat endpoint. Same convention as
app/schema/document.py - previously app/api/v1/chat_schemas.py.
"""

from __future__ import annotations

from pydantic import BaseModel


class ChatMessageResponse(BaseModel):
    session_id: str
    reply: str