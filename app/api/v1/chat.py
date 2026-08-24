"""
app/api/v1/chat.py

Thin channel adapter. PHASE 2: uses the pre-built, once-compiled
graph (app/agent/graph.py's get_chat_graph()) instead of a
checkpointer to rebuild a graph from on every request.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from langchain_openai import ChatOpenAI
from pymongo.asynchronous.database import AsyncDatabase

from app.schema.chat import AttachmentInput, ChatTurnRequest, ChatTurnResponse
from app.agent.graph import get_chat_graph
from app.auth.dependencies import get_current_user_id
from app.config import settings
from app.database import get_database
from app.documents.dispatcher import _extension_of, supported_extensions
from app.llm import get_genai_client
from app.services.chat_service import send_message

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/message", response_model=ChatTurnResponse)
async def send_chat_message(
    message: str = Form(...),
    session_id: str | None = Form(None),
    file: UploadFile | None = File(None),
    user_id: str = Depends(get_current_user_id),
    db: AsyncDatabase = Depends(get_database),
    graph=Depends(get_chat_graph),
    genai_client: ChatOpenAI = Depends(get_genai_client),
) -> ChatTurnResponse:
    attachment = None

    if file is not None:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")

        ext = _extension_of(file.filename)
        if ext not in supported_extensions():
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{ext}' - supported: {', '.join(supported_extensions())}",
            )

        if file.size is not None and file.size > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {settings.MAX_FILE_SIZE_MB}MB limit ({file.size / 1024 / 1024:.1f}MB)",
            )

        file_bytes = await file.read()
        attachment = AttachmentInput(
            filename=file.filename, content_type=file.content_type,
            size_bytes=len(file_bytes), file_bytes=file_bytes,
        )

    request = ChatTurnRequest(
        conversation_id=session_id, message_text=message, attachment=attachment, channel="rest",
    )

    return await send_message(graph=graph, db=db, genai_client=genai_client, user_id=user_id, request=request)