"""
app/agent/context.py

Per-invocation Runtime context for the graph - NOT checkpointed,
matches the confirmed pattern from the sibling RFP Analyzer codebase's
AgentContext, split into its own file (not bundled with state.py) to
match that same established convention exactly.
"""

from __future__ import annotations

from typing import TypedDict

from langchain_openai import ChatOpenAI
from pymongo.asynchronous.database import AsyncDatabase


class ChatContext(TypedDict):
    """Per-invocation, immutable, NOT checkpointed."""

    user_id: str
    db: AsyncDatabase
    genai_client: ChatOpenAI
    # Tightened from a generic `object` placeholder now that the real
    # app/llm.py is available - get_genai_client() there returns
    # ChatOpenAI specifically, not just any Runnable.