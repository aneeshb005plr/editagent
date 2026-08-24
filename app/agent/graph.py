from __future__ import annotations
from fastapi import Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from app.agent.nodes import (classify_intent_node, handle_additional_output_node, handle_check_status_node,
    handle_finding_followup_node, handle_knowledge_question_node, handle_off_topic_node,
    handle_scope_change_node, handle_social_node, handle_submit_document_node, handle_unclear_node, route_by_intent)
from app.agent.context import ChatContext
from app.agent.state import ChatState

_HANDLER_NODES = {
    "handle_social": handle_social_node, "handle_off_topic": handle_off_topic_node,
    "handle_knowledge_question": handle_knowledge_question_node, "handle_submit_document": handle_submit_document_node,
    "handle_check_status": handle_check_status_node, "handle_finding_followup": handle_finding_followup_node,
    "handle_scope_change": handle_scope_change_node, "handle_additional_output": handle_additional_output_node,
    "handle_unclear": handle_unclear_node,
}

def build_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(state_schema=ChatState, context_schema=ChatContext)
    builder.add_node("classify_intent", classify_intent_node)
    for name, fn in _HANDLER_NODES.items():
        builder.add_node(name, fn)
    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges("classify_intent", route_by_intent, [*_HANDLER_NODES.keys(), END])
    for name in _HANDLER_NODES:
        builder.add_edge(name, END)
    return builder.compile(checkpointer=checkpointer)


def get_chat_graph(request: Request):
    """FastAPI dependency - PHASE 2: the compiled graph is built
    ONCE at startup (main.py's lifespan: app.state.chat_graph =
    build_graph(app.state.checkpointer), after connect_checkpointer()
    runs) and reused across every request, rather than rebuilt on
    every single turn like Phase 1 did. Signature matches this
    project's other app.state-reading dependencies (get_database(),
    get_checkpointer(), get_genai_client())."""

    graph = getattr(request.app.state, "chat_graph", None)
    if graph is None:
        raise RuntimeError(
            "Chat graph not initialized. app.state.chat_graph = build_graph(...) "
            "must run during app startup."
        )
    return graph