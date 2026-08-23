"""
app/agent/nodes/classify_intent.py

Entry node for every turn - classifies intent, and (tightly coupled,
kept in the same file deliberately) the conditional-edge routing
function that reads the classification to decide the next node.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.models import IntakeAnswers, Intent, IntentClassification
from app.agent.state import ChatState

logger = logging.getLogger("app.agent.nodes.classify_intent")

MAX_TURNS = 30
# Circuit breaker - confirmed necessary from current LangGraph
# production-practice research (an unbounded clarification loop is a
# documented real failure mode). Generous enough for a genuinely long
# real conversation, low enough to bound worst-case cost. Public
# (not _MAX_TURNS) since route_by_intent() in this same file also
# needs it.

_INTENT_SYSTEM_PROMPT = """You classify the user's latest message into exactly one \
intent for a document-review assistant (EditEdge) that reviews PwC pursuit/proposal \
documents for style, grammar, and risk-language compliance.

Intents:
- social: greetings, thanks, farewells, small talk
- off_topic: requests unrelated to document review or the style guide
- knowledge_question: asking ABOUT a style/grammar/risk-language rule, without submitting \
a document (e.g. "can I say customer in my proposal?")
- submit_document: a file is attached, or the user wants to submit one for review
- check_status: asking about the progress/result of a review already submitted
- finding_followup: asking about a SPECIFIC finding from a completed review
- scope_change: wants to change how an already-submitted review is being handled
- new_document: attaching or wanting to submit ANOTHER document mid-conversation
- additional_output: wants findings presented differently (export, table, etc.)
- unclear: genuinely ambiguous - don't guess"""


def _build_genai_context(state: ChatState) -> str:
    recent = state["messages"][-6:]
    lines = []
    for m in recent:
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


async def classify_intent_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    """If mid-intake, tries to parse the reply as intake answers
    FIRST (a dedicated, narrower extraction) rather than full intent
    classification - a reasonable heuristic given scope: if that
    extraction finds nothing at all, falls through to normal
    classification (handles "actually, forget it" mid-intake
    correctly)."""

    turn_count = state.get("turn_count", 0) + 1
    if turn_count > MAX_TURNS:
        return {
            "turn_count": turn_count,
            "intent": "unclear",
            "messages": [AIMessage(content=(
                "We've covered a lot of ground in this conversation - let's start a "
                "fresh thread so I can help you more effectively."
            ))],
        }

    genai_client = runtime.context["genai_client"]
    last_message = state["messages"][-1].content if state["messages"] else ""

    pending_intake = state.get("pending_intake")
    if pending_intake and pending_intake.get("stage") != "complete":
        structured = genai_client.with_structured_output(IntakeAnswers)
        try:
            parsed: IntakeAnswers = await structured.ainvoke([
                SystemMessage(content=(
                    "Extract any of the following the user just answered: whether this "
                    "is a general or audit proposal, whether it's specifically a PCS "
                    "audit, and whether to review for US or Global English. Leave a "
                    "field None if not clearly answered in this message."
                )),
                HumanMessage(content=last_message),
            ])
        except Exception:
            logger.error("Intake answer parsing failed", exc_info=True)
            parsed = IntakeAnswers()

        if parsed.applies_to or parsed.is_pcs is not None or parsed.english_variant:
            # Got at least something useful - stay in the intake flow,
            # don't fall through to full classification.
            return {"turn_count": turn_count, "intent": "submit_document"}
        # Parsed nothing at all - likely the user said something else
        # entirely ("actually, cancel that") - fall through below.

    structured = genai_client.with_structured_output(IntentClassification)
    try:
        result: IntentClassification = await structured.ainvoke([
            SystemMessage(content=_INTENT_SYSTEM_PROMPT),
            HumanMessage(content=_build_genai_context(state)),
        ])
        intent: Intent = result.intent
    except Exception:
        logger.error("Intent classification failed", exc_info=True)
        intent = "unclear"

    # A file being attached this turn overrides classification -
    # submitting a document is unambiguous regardless of what the
    # accompanying text says.
    if state.get("pending_file_bytes"):
        intent = "new_document" if state.get("active_job_id") else "submit_document"

    return {"turn_count": turn_count, "intent": intent}


def route_by_intent(state: ChatState):
    """FIXED REAL BUG, confirmed by direct test: the circuit breaker
    used to set its own message AND intent="unclear", assuming that
    would be the final response - but since messages uses an
    ACCUMULATING reducer, the graph still proceeded to
    handle_unclear_node afterward, which appended its OWN message
    after the circuit breaker's - silently discarding the real
    circuit-breaker text. Routing directly to END when the circuit
    breaker has fired stops the graph right after classify_intent_
    node, so its message is genuinely the last one."""

    if state.get("turn_count", 0) > MAX_TURNS:
        return END

    intent = state.get("intent") or "unclear"
    if intent == "new_document":
        return "handle_submit_document"  # reused, see submit_document.py's module docstring
    return f"handle_{intent}"