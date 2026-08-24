"""
app/api/v1/chat.py

The HTTP surface for the conversational shell - a thin route calling
app/services/chat_service.py's send_message() directly, same
"thin route, real logic lives in the service layer" pattern already
used by app/api/v1/documents.py. Like that file, this is a dev/
testing surface (for Streamlit and direct API testing) - the future
Teams integration calls send_message() directly, in-process, not
through this HTTP route.

DEPENDENCIES: db/checkpointer/genai_client all come from the real,
existing app/database.py, app/checkpointer.py, and app/llm.py
respectively - not reimplemented here. See app/checkpointer.py in
particular for why MongoDBSaver needs its own dedicated sync
MongoClient (maxPoolSize=5) separate from database.py's own sync
client (which exists only for vector-search construction) - two
different sanctioned sync-client exceptions in this codebase, each
narrowly scoped to its own purpose.

session_id is auto-generated on the first call if not provided
(returned in the response for the caller to reuse on subsequent
turns) - matches how a real client (Streamlit, eventually Teams)
would naturally start a new conversation without needing to
pre-generate an ID itself.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo.asynchronous.database import AsyncDatabase

from app.schema.chat import ChatMessageResponse
from app.auth.dependencies import get_current_user_id
from app.checkpointer import get_checkpointer
from app.database import get_database
from app.llm import get_genai_client
from app.services.chat_service import send_message

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/message", response_model=ChatMessageResponse)
async def send_chat_message(
    message: str = Form(...),
    session_id: str | None = Form(None),
    file: UploadFile | None = File(None),
    user_id: str = Depends(get_current_user_id),
    db: AsyncDatabase = Depends(get_database),
    checkpointer: MongoDBSaver = Depends(get_checkpointer),
    genai_client: ChatOpenAI = Depends(get_genai_client),
) -> ChatMessageResponse:
    """One conversational turn. file is optional - when present, its
    bytes are passed through to the graph's submit_document flow
    exactly as designed (see app/agent/nodes/submit_document.py) -
    this route does NOT call the /documents/review submission path
    separately; the agent creates the job itself via
    submit_review_job(), same function, called from inside the graph
    rather than from this route directly.

    FIXED REAL DUPLICATION: this route previously defined its own
    local get_db()/get_checkpointer()/get_genai_client() functions,
    each independently reading request.app.state - duplicating logic
    that already exists correctly in app/database.py, app/
    checkpointer.py, and app/llm.py respectively. Now imports and
    uses the real ones directly, avoiding two implementations of the
    same dependency drifting apart over time."""

    resolved_session_id = session_id or str(uuid.uuid4())

    file_bytes = await file.read() if file is not None else None
    filename = file.filename if file is not None else None

    reply = await send_message(
        db=db,
        checkpointer=checkpointer,
        genai_client=genai_client,
        session_id=resolved_session_id,
        user_id=user_id,
        message_text=message,
        file_bytes=file_bytes,
        filename=filename,
    )

    return ChatMessageResponse(session_id=resolved_session_id, reply=reply)