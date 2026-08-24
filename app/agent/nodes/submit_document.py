"""
app/agent/nodes/submit_document.py

REBUILT twice in this session - first per external review points 1-4
(one interrupt() per invocation, state-persisted answers), then a
SECOND real fix found via direct testing of THAT rebuild: routing
was decided by a separate conditional-edge function (_route_intake
in graph.py) inferring "should we loop back?" from whether
intake_answers looked complete - but this couldn't distinguish
"cancelled, stop" from "still incomplete, keep asking", since both
states have intake_answers that isn't "complete". Reproduced directly:
a cancel correctly cleared intake_answers, but the routing function
then looped back to this SAME node forever (confirmed hang via a
call-count safety limit in testing, not just theorized).

FIXED by having this node return Command(goto=...) directly - node-
controlled routing, confirmed via isolated test against our real
installed langgraph to require no separate conditional-edge function
at all. Now there is no ambiguous state to misinterpret: cancel goes
to END explicitly, completion goes to create_review_job explicitly,
"still incomplete" goes back to this node explicitly - three
genuinely distinct routing decisions, not one heuristic inferring
between them.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from app.agent.context import ChatContext
from app.agent.models import IntakeInterpretation
from app.agent.state import ChatState
from app.services.upload_service import abandon_staged_upload

logger = logging.getLogger("app.agent.nodes.submit_document")


def _intake_question_text(answers: dict) -> str:
    missing = []
    if answers.get("applies_to") is None:
        missing.append("Is this a **general** proposal or an **audit/assurance** proposal?")
    if answers.get("applies_to") == "audit" and answers.get("is_pcs") is None:
        missing.append("Is it specifically a **PCS** (Private Company Services) audit?")
    if answers.get("english_variant") is None:
        missing.append("Should I review for **US English** or **Global (UK) English**?")
    return "Before I start the review, a couple of quick questions:\n\n" + "\n".join(
        f"- {q}" for q in missing
    )


def _intake_complete(answers: dict) -> bool:
    if answers.get("applies_to") is None or answers.get("english_variant") is None:
        return False
    if answers.get("applies_to") == "audit" and answers.get("is_pcs") is None:
        return False
    return True


async def handle_submit_document_node(state: ChatState, runtime: Runtime[ChatContext]) -> Command:
    db = runtime.context["db"]
    genai_client = runtime.context["genai_client"]

    upload_id = state.get("pending_upload_id")
    if not upload_id:
        return Command(
            goto=END,
            update={"messages": [AIMessage(content="I don't see a file attached yet - please upload a document to review.")]},
        )

    answers = state.get("intake_answers") or {"applies_to": None, "is_pcs": None, "english_variant": None}

    resume = interrupt({"text": _intake_question_text(answers)})

    new_upload_id = resume.get("new_upload_id") if isinstance(resume, dict) else None
    if new_upload_id and new_upload_id != upload_id:
        await abandon_staged_upload(db, upload_id)
        return Command(
            goto="handle_submit_document",
            update={
                "pending_upload_id": new_upload_id,
                "pending_filename": resume.get("new_filename"),
                "pending_file_size_bytes": resume.get("new_size_bytes"),
                "pending_content_type": resume.get("new_content_type"),
                "intake_answers": {"applies_to": None, "is_pcs": None, "english_variant": None},
            },
        )

    text = resume.get("text", "") if isinstance(resume, dict) else str(resume)

    structured = genai_client.with_structured_output(IntakeInterpretation)
    try:
        interpretation: IntakeInterpretation = await structured.ainvoke([
            SystemMessage(content=(
                f"The user is in the middle of submitting a document for review. "
                f"Current question being asked: {_intake_question_text(answers)}\n"
                f"Already known - applies_to: {answers.get('applies_to')}, "
                f"is_pcs: {answers.get('is_pcs')}, english_variant: {answers.get('english_variant')}\n\n"
                "Determine whether the user's message answers the current question "
                "(even partially), asks to cancel this submission, or is unrelated to "
                "either."
            )),
            HumanMessage(content=text),
        ])
    except Exception:
        logger.error("Intake interpretation failed", exc_info=True)
        interpretation = IntakeInterpretation(action="unrelated")

    if interpretation.action == "cancel":
        await abandon_staged_upload(db, upload_id)
        return Command(
            goto=END,
            update={
                "pending_upload_id": None, "pending_filename": None,
                "pending_file_size_bytes": None, "pending_content_type": None,
                "intake_answers": None,
                "messages": [AIMessage(content="No problem - I've cancelled that. Let me know if you'd like to submit something else.")],
            },
        )

    if interpretation.action == "unrelated":
        return Command(
            goto="handle_submit_document",
            update={
                "intake_answers": answers,
                "messages": [AIMessage(content=(
                    "I didn't quite catch an answer there.\n\n" + _intake_question_text(answers) +
                    "\n\n(Or let me know if you'd like to cancel this review instead.)"
                ))],
            },
        )

    if interpretation.applies_to is not None:
        answers["applies_to"] = interpretation.applies_to
    if interpretation.is_pcs is not None:
        answers["is_pcs"] = interpretation.is_pcs
    if interpretation.english_variant is not None:
        answers["english_variant"] = interpretation.english_variant

    if _intake_complete(answers):
        return Command(goto="create_review_job", update={"intake_answers": answers})

    return Command(goto="handle_submit_document", update={"intake_answers": answers})