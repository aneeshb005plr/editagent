"""
app/agent/nodes/classify_intent.py

PHASE 3B/3C REWRITE - see app/agent/state.py's module docstring for
the full architectural reasoning (interrupt()-based intake removed,
replaced by this node running fresh, every turn, deciding routing).

Deterministic (zero-LLM-call) routing, in order:
1. A NEW attachment this turn (new_upload_id) conflicting with an
   ALREADY-pending, different upload -> attachment_conflict.
2. A NEW attachment this turn, no conflict -> submit_document/
   new_document, exactly as Phase 2.
3. An exact "continue"/"cancel" phrase while intake is pending ->
   deterministic, no LLM call - these are structurally obvious.
4. An exact "replace"/"keep" phrase while an attachment conflict is
   pending resolution -> deterministic, no LLM call.

Only when NONE of the above apply, and an intake or conflict IS
pending, is exactly ONE LLM call made - a COMBINED classification
(PendingIntakeTurnClassification) that either says "this is about
the pending intake" (answer/continue/cancel) or "this is a detour"
(classifying what the user actually wants using the same Intent
taxonomy used for normal turns). This is the key mechanism that
lets a pending intake be safely left untouched while a detour is
handled normally - see app/agent/state.py and app/agent/nodes/
submit_document.py for how the resulting signal is consumed.

With NO attachment and NO pending intake/conflict, this is
UNCHANGED from Phase 2 - normal intent classification.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.models import Intent, IntentClassification, PendingIntakeTurnClassification
from app.agent.state import ChatState

logger = logging.getLogger("app.agent.nodes.classify_intent")

_INTENT_SYSTEM_PROMPT = """You classify the user's latest message into exactly one intent for a document-review assistant (EditEdge) that reviews PwC pursuit/proposal documents for style, grammar, and risk-language compliance.

Intents:
- social: greetings, thanks, farewells, small talk
- off_topic: requests unrelated to document review or the style guide
- knowledge_question: asking ABOUT a style/grammar/risk-language rule, without submitting a document (e.g. "can I say customer in my proposal?")
- submit_document: a file is attached, or the user wants to submit one for review
- check_status: asking about the progress/result of a review already submitted
- finding_followup: asking about a SPECIFIC finding from a completed review
- scope_change: wants to change how an already-submitted review is being handled
- new_document: attaching or wanting to submit ANOTHER document mid-conversation
- additional_output: wants findings presented differently (export, table, etc.)
- unclear: genuinely ambiguous - don't guess"""

_CONTINUE_PHRASES = {"continue", "continue intake", "continue_intake", "continue my document", "resume"}
_CANCEL_PHRASES = {"cancel", "cancel intake", "cancel_intake", "never mind", "forget it", "abandon"}
_REPLACE_PHRASES = {"replace"}
_KEEP_PHRASES = {"keep"}


def _build_genai_context(state: ChatState) -> str:
    recent = state["messages"][-6:]
    lines = []
    for m in recent:
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


def _latest_text(state: ChatState) -> str:
    return state["messages"][-1].content.strip() if state["messages"] else ""


async def classify_intent_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    turn_count = state.get("turn_count", 0) + 1
    genai_client = runtime.context["genai_client"]

    new_upload_id = state.get("new_upload_id")
    pending_upload_id = state.get("pending_upload_id")
    conflicting_upload_id = state.get("conflicting_upload_id")

    # --- Deterministic: new attachment this turn ---
    if new_upload_id:
        if pending_upload_id and new_upload_id != pending_upload_id:
            return {
                "turn_count": turn_count, "intent": "attachment_conflict",
                "consecutive_unclear_count": 0, "pending_action_signal": None,
                "conflicting_upload_id": new_upload_id,
                "conflicting_filename": state.get("new_filename"),
                "conflicting_file_size_bytes": state.get("new_file_size_bytes"),
                "conflicting_content_type": state.get("new_content_type"),
            }
        intent: Intent = "new_document" if state.get("focused_job_id") else "submit_document"
        return {
            "turn_count": turn_count, "intent": intent,
            "consecutive_unclear_count": 0, "pending_action_signal": None,
        }

    # --- Deterministic: an attachment-conflict choice is pending ---
    if conflicting_upload_id:
        text = _latest_text(state).lower()
        if text in _REPLACE_PHRASES:
            return {"turn_count": turn_count, "intent": "attachment_conflict",
                    "pending_action_signal": {"action": "replace"}, "consecutive_unclear_count": 0}
        if text in _KEEP_PHRASES:
            return {"turn_count": turn_count, "intent": "attachment_conflict",
                    "pending_action_signal": {"action": "keep"}, "consecutive_unclear_count": 0}
        # Anything else while a conflict choice is pending re-presents
        # the same choice - deliberately narrow scope, no detours
        # allowed mid-choice (a small, blocking decision, not a full
        # suspendable workflow like intake itself).
        return {"turn_count": turn_count, "intent": "attachment_conflict",
                "pending_action_signal": {"action": "unclear"}, "consecutive_unclear_count": 0}

    # --- No new attachment, but intake IS pending/suspended ---
    if pending_upload_id:
        text = _latest_text(state).lower()
        if text in _CONTINUE_PHRASES:
            return {"turn_count": turn_count, "intent": "submit_document",
                    "pending_action_signal": {"action": "continue_intake"}, "consecutive_unclear_count": 0}
        if text in _CANCEL_PHRASES:
            return {"turn_count": turn_count, "intent": "submit_document",
                    "pending_action_signal": {"action": "cancel_intake"}, "consecutive_unclear_count": 0}

        structured = genai_client.with_structured_output(PendingIntakeTurnClassification)
        try:
            result: PendingIntakeTurnClassification = await structured.ainvoke([
                SystemMessage(content=(
                    "The user has an unfinished document submission pending (intake questions "
                    "not yet answered). Determine whether their latest message answers the "
                    "pending question (even partially), wants to continue/cancel it, or is "
                    "about something else entirely (a detour) - in which case classify what "
                    "they actually want using the normal intent taxonomy, and the pending "
                    "submission will be left untouched."
                )),
                HumanMessage(content=_build_genai_context(state)),
            ])
        except Exception:
            logger.error("Pending-intake turn classification failed", exc_info=True)
            result = PendingIntakeTurnClassification(action="detour", detour_intent="unclear")

        if result.action in ("intake_answer", "continue_intake", "cancel_intake"):
            return {
                "turn_count": turn_count, "intent": "submit_document",
                "pending_action_signal": result.model_dump(), "consecutive_unclear_count": 0,
            }

        # detour - pending intake state is NOT touched at all here.
        detour_intent = result.detour_intent or "unclear"
        consecutive_unclear = state.get("consecutive_unclear_count", 0)
        new_consecutive_unclear = consecutive_unclear + 1 if detour_intent == "unclear" else 0
        return {
            "turn_count": turn_count, "intent": detour_intent,
            "pending_action_signal": None, "consecutive_unclear_count": new_consecutive_unclear,
        }

    # --- No attachment, no pending intake/conflict - normal classification (unchanged) ---
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

    return {
        "turn_count": turn_count, "intent": intent,
        "pending_action_signal": None, "consecutive_unclear_count": new_consecutive_unclear,
    }


def route_by_intent(state: ChatState) -> str:
    intent = state.get("intent") or "unclear"
    if intent == "new_document":
        return "handle_submit_document"
    return f"handle_{intent}"