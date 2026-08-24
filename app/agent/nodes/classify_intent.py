"""
app/agent/nodes/classify_intent.py

PHASE 2 CHANGES:

- DETERMINISTIC PRE-ROUTER: a new attachment now short-circuits to
  submit_document/new_document BEFORE any LLM call at all - not
  "call the classifier, then override the result" like Phase 1 did.
  Confirmed real requirement from the architecture doc's acceptance
  criteria ("File attachment uses zero intent-classifier calls").

- The old "pending_intake mid-flow" branch is REMOVED entirely - now
  handled structurally by interrupt()/resume (see submit_document.py):
  once inside that node's intake loop, resuming jumps straight back
  into it without re-running this node at all (confirmed via direct
  test against our real installed langgraph - classify_node ran
  exactly once across a full multi-turn interrupt sequence, not once
  per turn). So there's no "are we mid-intake" check needed here
  anymore - if we're mid-intake, this node simply doesn't run on
  that turn.

- consecutive_unclear_count replaces turn_count as the real circuit
  breaker (Phase 2, per the architecture doc). Resets to 0 whenever
  intent resolves to anything other than unclear - including the
  deterministic pre-router path, which never even reaches the
  "unclear" possibility.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.models import Intent, IntentClassification
from app.agent.state import ChatState

logger = logging.getLogger("app.agent.nodes.classify_intent")

_INTENT_SYSTEM_PROMPT = "Classify the user's latest message into exactly one intent."


def _build_genai_context(state: ChatState) -> str:
    recent = state["messages"][-6:]
    lines = []
    for m in recent:
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


async def classify_intent_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    turn_count = state.get("turn_count", 0) + 1

    # DETERMINISTIC PRE-ROUTER - zero LLM calls for this case.
    if state.get("pending_upload_id"):
        intent: Intent = "new_document" if state.get("active_job_id") else "submit_document"
        return {"turn_count": turn_count, "intent": intent, "consecutive_unclear_count": 0}

    genai_client = runtime.context["genai_client"]
    structured = genai_client.with_structured_output(IntentClassification)
    try:
        result: IntentClassification = await structured.ainvoke([
            SystemMessage(content=_INTENT_SYSTEM_PROMPT),
            HumanMessage(content=_build_genai_context(state)),
        ])
        intent = result.intent
    except Exception:
        logger.error("Intent classification failed", exc_info=True)
        intent = "unclear"

    consecutive_unclear = state.get("consecutive_unclear_count", 0)
    new_consecutive_unclear = consecutive_unclear + 1 if intent == "unclear" else 0

    return {"turn_count": turn_count, "intent": intent, "consecutive_unclear_count": new_consecutive_unclear}


def route_by_intent(state: ChatState) -> str:
    intent = state.get("intent") or "unclear"
    if intent == "new_document":
        return "handle_submit_document"  # reused, see submit_document.py's module docstring
    return f"handle_{intent}"