"""
app/agent/graph.py

Builds the compiled LangGraph. Confirmed against our real installed
langgraph==1.2.10 directly (not assumed): StateGraph(state_schema=...,
context_schema=...) + nodes receiving runtime: Runtime[Context] is
the complete, current pattern - verified with an actual compiled
graph before building this file, not just read about.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    classify_intent_node,
    handle_additional_output_node,
    handle_check_status_node,
    handle_finding_followup_node,
    handle_knowledge_question_node,
    handle_off_topic_node,
    handle_scope_change_node,
    handle_social_node,
    handle_submit_document_node,
    handle_unclear_node,
    route_by_intent,
)
from app.agent.context import ChatContext
from app.agent.state import ChatState

_HANDLER_NODES = {
    "handle_social": handle_social_node,
    "handle_off_topic": handle_off_topic_node,
    "handle_knowledge_question": handle_knowledge_question_node,
    "handle_submit_document": handle_submit_document_node,
    "handle_check_status": handle_check_status_node,
    "handle_finding_followup": handle_finding_followup_node,
    "handle_scope_change": handle_scope_change_node,
    "handle_additional_output": handle_additional_output_node,
    "handle_unclear": handle_unclear_node,
    # Deliberately no "handle_new_document" entry - route_by_intent()
    # maps new_document -> handle_submit_document directly, see
    # app/agent/nodes/submit_document.py's module docstring for why
    # that reuse is correct.
}


def build_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(state_schema=ChatState, context_schema=ChatContext)

    builder.add_node("classify_intent", classify_intent_node)
    for name, fn in _HANDLER_NODES.items():
        builder.add_node(name, fn)

    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges(
        "classify_intent", route_by_intent, [*_HANDLER_NODES.keys(), END]
    )
    # END included as a valid destination - confirmed via direct test
    # this is required (not automatic) for a conditional-edge function
    # to be allowed to return END directly, which route_by_intent()
    # now does when the circuit breaker has fired (see its docstring
    # for the real bug this fixes).
    for name in _HANDLER_NODES:
        builder.add_edge(name, END)

    return builder.compile(checkpointer=checkpointer)