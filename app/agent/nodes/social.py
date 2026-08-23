"""app/agent/nodes/social.py"""

from __future__ import annotations

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState


async def handle_social_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    genai_client = runtime.context["genai_client"]
    response = await genai_client.ainvoke([
        SystemMessage(content=(
            "You are EditEdge, a PwC pursuit document review assistant. Respond briefly "
            "and warmly to this social message (greeting/thanks/farewell). Keep it to one "
            "or two sentences."
        )),
        state["messages"][-1],
    ])
    return {"messages": [AIMessage(content=response.content)]}