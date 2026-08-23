"""
app/agent/nodes/additional_output.py

HONEST PARTIAL HANDLER - chat-based export (e.g. generating a
downloadable artifact from within a conversation turn) isn't built
yet. The Streamlit dev client already has this via its own CSV
download button - pointing there rather than pretending this works
through chat.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState


async def handle_additional_output_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    return {
        "messages": [AIMessage(content=(
            "I can't export findings directly through chat yet - if you're using the "
            "Streamlit client, there's a CSV download button once your review completes."
        ))]
    }