from __future__ import annotations
from typing import TypedDict
from langchain_openai import ChatOpenAI
from pymongo.asynchronous.database import AsyncDatabase

class ChatContext(TypedDict):
    user_id: str
    db: AsyncDatabase
    genai_client: ChatOpenAI
    conversation_id: str
    # Phase 3B: needed so create_review_job_node can stamp
    # origin_conversation_id on chat-created jobs. Belongs in
    # context (immutable per-invocation, not checkpointed), not
    # state - it's a property of WHERE this invocation is happening,
    # the same kind of thing user_id already is here, not evolving
    # conversational content.