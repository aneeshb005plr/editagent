"""
app/agent/nodes/unclear.py

PHASE 2: uses consecutive_unclear_count (see classify_intent.py) to
offer a genuine reset/options message once the SAME failure pattern
repeats a few times in a row, rather than a normal clarifying
question every time - and never hard-stops a conversation just
because it's long, only because it's genuinely stuck. Threshold of
3 matches the architecture doc's own example.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState

_RESET_THRESHOLD = 3


async def handle_unclear_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    consecutive_unclear = state.get("consecutive_unclear_count", 0)

    if consecutive_unclear >= _RESET_THRESHOLD:
        text = (
            "I'm having trouble following - let's reset. I can help you:\n\n"
            "- **Review a document** - attach a file\n"
            "- **Check on a review** already in progress\n"
            "- **Answer a style question** - ask about a specific rule\n\n"
            "What would you like to do?"
        )
    else:
        text = (
            "I want to make sure I help with the right thing - are you looking to submit "
            "a document for review, check on one already in progress, or ask about a "
            "specific style rule?"
        )

    return {"messages": [AIMessage(content=text)]}