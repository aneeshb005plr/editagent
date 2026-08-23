"""
app/agent/nodes/scope_change.py

HONEST PARTIAL HANDLER, not a silent stub - the real semantics
question (does changing scope restart an already-submitted job, or
only apply going forward?) was explicitly left open in design
discussion and hasn't been resolved. This handler doesn't crash or
pretend to apply the change - it clearly explains the real
limitation and offers the one thing that IS safe to do (apply to
the next submission).
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState


async def handle_scope_change_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    return {
        "messages": [AIMessage(content=(
            "I can't change the scope of a review that's already been submitted or "
            "completed - it was processed against the answers given at the time. If "
            "you'd like, I can apply different settings to your next document instead."
        ))]
    }