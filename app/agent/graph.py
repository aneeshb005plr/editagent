"""
app/agent/graph.py

FIX (final Phase 3B/3C correction pass, item A): handle_submit_document
and handle_attachment_conflict now return Command(goto=...) for
EVERY path (no plain dict returns) - so NEITHER has a static edge to
END mixed in anymore. Only ONE routing mechanism per node, as
required. Confirmed via direct test against our real installed
langgraph that Command(goto=END) works correctly with zero static
edges present for that node at all.
"""

from __future__ import annotations
from fastapi import Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from app.agent.nodes import (classify_intent_node, handle_additional_output_node, handle_check_status_node,
    handle_finding_followup_node, handle_knowledge_question_node, handle_off_topic_node,
    handle_scope_change_node, handle_social_node, handle_submit_document_node, handle_unclear_node, route_by_intent)
from app.agent.nodes.create_review_job import create_review_job_node
from app.agent.nodes.attachment_conflict import handle_attachment_conflict_node
from app.agent.context import ChatContext
from app.agent.state import ChatState

# Nodes that always return a plain dict (never Command) - these get a
# normal static edge to END. handle_submit_document and
# handle_attachment_conflict are DELIBERATELY excluded - they return
# Command(goto=...) exclusively, so they get no static edge at all
# (see each module's own docstring).
_STATIC_EDGE_NODES = {
    "handle_social": handle_social_node, "handle_off_topic": handle_off_topic_node,
    "handle_knowledge_question": handle_knowledge_question_node,
    "handle_check_status": handle_check_status_node, "handle_finding_followup": handle_finding_followup_node,
    "handle_scope_change": handle_scope_change_node, "handle_additional_output": handle_additional_output_node,
    "handle_unclear": handle_unclear_node,
}

_COMMAND_ONLY_NODES = {
    "handle_submit_document": handle_submit_document_node,
    "handle_attachment_conflict": handle_attachment_conflict_node,
}


def build_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(state_schema=ChatState, context_schema=ChatContext)

    builder.add_node("classify_intent", classify_intent_node)
    builder.add_node("create_review_job", create_review_job_node)
    for name, fn in _STATIC_EDGE_NODES.items():
        builder.add_node(name, fn)
    for name, fn in _COMMAND_ONLY_NODES.items():
        builder.add_node(name, fn)

    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges(
        "classify_intent", route_by_intent,
        [*_STATIC_EDGE_NODES.keys(), *_COMMAND_ONLY_NODES.keys(), END],
    )

    builder.add_edge("create_review_job", END)
    for name in _STATIC_EDGE_NODES:
        builder.add_edge(name, END)
    # No static edges for _COMMAND_ONLY_NODES - they route themselves.

    return builder.compile(checkpointer=checkpointer)


def get_chat_graph(request: Request):
    graph = getattr(request.app.state, "chat_graph", None)
    if graph is None:
        raise RuntimeError(
            "Chat graph not initialized. app.state.chat_graph = build_graph(...) "
            "must run during app startup."
        )
    return graph