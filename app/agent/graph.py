"""
app/agent/graph.py

PHASE 3B/3C: handle_submit_document is now a PLAIN node (no
interrupt() - see app/agent/state.py's module docstring for why),
so it needs a NORMAL static edge to END for its common case (still
asking a question, turn ends) - which coexists correctly with its
occasional Command(goto="create_review_job") override once intake
completes. Confirmed via direct test against our real installed
langgraph that a static edge and an occasional Command(goto=...)
from the SAME node do not conflict: Command(goto=...) always wins
when returned; the static edge only applies when the node returns a
plain dict instead.
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

_TERMINAL_HANDLER_NODES = {
    "handle_social": handle_social_node, "handle_off_topic": handle_off_topic_node,
    "handle_knowledge_question": handle_knowledge_question_node,
    "handle_check_status": handle_check_status_node, "handle_finding_followup": handle_finding_followup_node,
    "handle_scope_change": handle_scope_change_node, "handle_additional_output": handle_additional_output_node,
    "handle_unclear": handle_unclear_node,
    "handle_attachment_conflict": handle_attachment_conflict_node,
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

    # handle_submit_document routes to create_review_job via
    # Command(goto=...) when intake completes - no conditional edge
    # needed for that transition (see that module's docstring). It
    # otherwise ends the turn normally (implicit - a plain dict
    # return with no goto falls through to this edge):
    builder.add_edge("handle_submit_document", END)
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