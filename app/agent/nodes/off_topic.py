"""app/agent/nodes/off_topic.py"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState


async def handle_off_topic_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    return {
        "messages": [AIMessage(content=(
            "I'm EditEdge - I review PwC pursuit documents for grammar, style, and "
            "risk-language compliance against the firm's style guide. I can also answer "
            "questions about specific style rules. Happy to help with either!"
        ))]
    }