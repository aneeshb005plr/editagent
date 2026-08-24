"""
app/schema/chat.py

Channel-neutral chat contracts, per the architecture doc's Section
4. ChatTurnRequest deliberately does NOT include user_id, unlike the
doc's own illustrative example - identity comes from each channel's
authenticated context (Depends(get_current_user_id) today, whatever
Teams/M365 SSO resolves to later), never trusted as client-supplied
request content. This is a deliberate, security-motivated deviation
from a literal reading of the doc, not an oversight - every channel
should resolve identity BEFORE constructing a ChatTurnRequest, so
send_message() takes user_id as a separate, explicit parameter.

attachment.file_bytes is the ONE place raw bytes exist in this
contract, and only transiently - app/services/chat_service.py stages
them into GridFS immediately and never lets them reach checkpointed
LangGraph state (see app/agent/state.py's docstring for why that
matters).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AttachmentInput(BaseModel):
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None
    file_bytes: bytes | None = None
    # Named file_bytes, not "bytes" - a field literally named `bytes`
    # inside a class that also annotates a field as type `bytes`
    # causes a real name collision during Pydantic's forward-
    # reference type resolution (confirmed by hitting the actual
    # TypeError: "unsupported operand type(s) for |: 'NoneType' and
    # 'NoneType'" when this was first named `bytes`) - the field name
    # shadows the builtin type within the class's own namespace.


class ChatTurnRequest(BaseModel):
    conversation_id: str | None = None
    message_text: str
    attachment: AttachmentInput | None = None
    channel: Literal["streamlit", "rest", "m365"] = "rest"


class ChatTurnResponse(BaseModel):
    conversation_id: str
    text: str
    status: Literal["ok", "needs_input", "job_submitted", "job_running", "job_complete", "error"] = "ok"