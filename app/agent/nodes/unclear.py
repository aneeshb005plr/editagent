"""app/agent/nodes/unclear.py"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState


async def handle_unclear_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    return {
        "messages": [AIMessage(content=(
            "I want to make sure I help with the right thing - are you looking to submit "
            "a document for review, check on one already in progress, or ask about a "
            "specific style rule?"
        ))]
    }