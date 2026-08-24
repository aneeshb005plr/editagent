"""
app/agent/graph.py

Intake routing uses NODE-CONTROLLED routing (Command(goto=...),
returned directly from handle_submit_document_node) rather than a
separate conditional-edge function inferring intent from state.
REAL BUG this fixes, found via direct testing: a conditional-edge
function reading "is intake_answers complete?" cannot distinguish
"cancelled, stop" from "still incomplete, keep asking" - both look
like "not complete" to it. Reproduced directly: a cancellation
correctly cleared intake_answers, then the old conditional edge
looped back to handle_submit_document forever (confirmed hung via a
call-count safety limit in testing). Command(goto=...) has no such
ambiguity - the node itself says explicitly where to go next.
Confirmed via isolated test against our real installed langgraph
that this requires no add_conditional_edges declaration at all for
that node.
"""

from __future__ import annotations
from fastapi import Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from app.agent.nodes import (classify_intent_node, handle_additional_output_node, handle_check_status_node,
    handle_finding_followup_node, handle_knowledge_question_node, handle_off_topic_node,
    handle_scope_change_node, handle_social_node, handle_submit_document_node, handle_unclear_node, route_by_intent)
from app.agent.nodes.create_review_job import create_review_job_node
from app.agent.context import ChatContext
from app.agent.state import ChatState

_TERMINAL_HANDLER_NODES = {
    "handle_social": handle_social_node, "handle_off_topic": handle_off_topic_node,
    "handle_knowledge_question": handle_knowledge_question_node,
    "handle_check_status": handle_check_status_node, "handle_finding_followup": handle_finding_followup_node,
    "handle_scope_change": handle_scope_change_node, "handle_additional_output": handle_additional_output_node,
    "handle_unclear": handle_unclear_node,
}


def build_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(state_schema=ChatState, context_schema=ChatContext)

    builder.add_node("classify_intent", classify_intent_node)
    builder.add_node("handle_submit_document", handle_submit_document_node)
    builder.add_node("create_review_job", create_review_job_node)
    for name, fn in _TERMINAL_HANDLER_NODES.items():
        builder.add_node(name, fn)

    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges(
        "classify_intent", route_by_intent,
        [*_TERMINAL_HANDLER_NODES.keys(), "handle_submit_document", END],
    )

    # No conditional edge for handle_submit_document - it routes
    # itself via Command(goto=...), see that module's docstring.
    builder.add_edge("create_review_job", END)

    for name in _TERMINAL_HANDLER_NODES:
        builder.add_edge(name, END)

    return builder.compile(checkpointer=checkpointer)


def get_chat_graph(request: Request):
    graph = getattr(request.app.state, "chat_graph", None)
    if graph is None:
        raise RuntimeError(
            "Chat graph not initialized. app.state.chat_graph = build_graph(...) "
            "must run during app startup."
        )
    return graph