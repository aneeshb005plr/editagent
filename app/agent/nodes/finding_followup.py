"""
app/agent/nodes/finding_followup.py

Job SELECTION goes through the real resolver instead of a bare
focused_job_id-only lookup - supports Scenario 6 (two jobs both
containing F-0012, resolved correctly via focused_job_id) and
Scenario 7 (ambiguous filename references). The actual finding
explanation logic (get_findings_for_job, the LLM call) is
DELIBERATELY UNCHANGED - rebuilding that into the real, bounded,
stable-finding-ID-aware conversation workflow is explicitly Phase
3D's job, not this slice's.

FIX (final Phase 3B/3C correction pass, items D/J): explicitly sets
requires_user_input=False on every path, and clears
focused_finding_id whenever focus resolves/changes to a job -
consistent with check_status.py's same fix.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.nodes.check_status import _extract_filename_reference
from app.agent.state import ChatState
from app.repositories.findings_repository import get_findings_for_job
from app.schemas.job_resolution import ResolutionStatus
from app.services.job_resolver import resolve_job_reference

logger = logging.getLogger("app.agent.nodes.finding_followup")


async def handle_finding_followup_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]
    user_id = runtime.context["user_id"]
    conversation_id = runtime.context["conversation_id"]
    genai_client = runtime.context["genai_client"]

    text = state["messages"][-1].content if state["messages"] else ""
    filename_reference = _extract_filename_reference(text)

    result = await resolve_job_reference(
        db, user_id, conversation_id,
        filename_reference=filename_reference,
        focused_job_id=state.get("focused_job_id"),
    )

    if result.status == ResolutionStatus.AMBIGUOUS:
        lines = "\n".join(f"- {j.filename}, submitted {j.created_at.strftime('%b %d, %I:%M %p')}" for _, j in result.candidates)
        return {"requires_user_input": False,
                "messages": [AIMessage(content=f"I found multiple matching reviews. Which one do you mean?\n\n{lines}")]}

    if result.status in (ResolutionStatus.NOT_FOUND, ResolutionStatus.NO_CONTEXT):
        return {"requires_user_input": False,
                "messages": [AIMessage(content="I don't have a completed review to discuss yet - submit a document first, or let me know which review you mean.")]}

    job_id, job = result.job_id, result.job

    if job.status.value != "succeeded":
        return {"requires_user_input": False, "messages": [AIMessage(content="That review isn't complete yet, so I don't have findings to discuss.")]}

    findings = await get_findings_for_job(db, job_id)
    if not findings:
        return {"focused_job_id": job_id, "focused_finding_id": None, "requires_user_input": False,
                "messages": [AIMessage(content="That review came back clean - no findings to discuss.")]}

    findings_context = "\n\n".join(
        f"[{i+1}] {f.rule_id} ({f.category.value}) at {f.location_display}: {f.original_text!r}\n"
        f"Explanation: {f.explanation}\nSuggested: {f.suggested_rewrite or 'N/A'}"
        for i, f in enumerate(findings)
    )

    try:
        response = await genai_client.ainvoke([
            SystemMessage(content=(
                "Answer the user's question about these specific review findings. "
                "Don't invent findings not listed here."
            )),
            HumanMessage(content=f"Findings:\n{findings_context}\n\nQuestion: {text}"),
        ])
        reply_text = response.content
    except Exception:
        logger.error("Finding followup response generation failed", exc_info=True)
        reply_text = f"I have {len(findings)} finding(s) from that review but couldn't generate a full answer right now - please try again in a moment."

    return {"focused_job_id": job_id, "focused_finding_id": None, "requires_user_input": False,
            "messages": [AIMessage(content=reply_text)]}