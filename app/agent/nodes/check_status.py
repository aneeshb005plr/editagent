"""
app/agent/nodes/check_status.py

Uses the real job resolver instead of a bare focused_job_id-only
lookup - supports multiple concurrent jobs correctly, asks for
clarification when ambiguous rather than guessing (Scenario 7), and
never leaks ownership information for a malformed/non-owned
reference (Scenario 8).

FIX (final Phase 3B/3C correction pass, items D/J): explicitly sets
requires_user_input=False on every path (a status check is a normal
conversational exchange, not a blocking intake/conflict workflow,
even when it ends by asking which review the user meant - see
app/agent/state.py's docstring for what this signal is actually
for), and clears focused_finding_id whenever focus resolves to a
job (a finding from whatever was PREVIOUSLY focused is job-scoped -
carrying it into a newly-focused job would be wrong, see item J).
"""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.documents.dispatcher import supported_extensions
from app.schemas.job_resolution import ResolutionStatus
from app.services.job_resolver import resolve_job_reference

_FILENAME_PATTERN = re.compile(
    r"\S+\.(?:" + "|".join(ext.lstrip(".") for ext in supported_extensions()) + r")",
    re.IGNORECASE,
)


def _extract_filename_reference(text: str) -> str | None:
    """Simple, deterministic heuristic - no LLM needed for this: a
    token that looks like a filename with a supported extension.
    Not full NLU; good enough for "status of Proposal.pptx"-style
    references, and correctly returns None otherwise rather than
    guessing."""

    match = _FILENAME_PATTERN.search(text)
    return match.group(0) if match else None


def _format_candidate(job) -> str:
    return f"- {job.filename}, submitted {job.created_at.strftime('%b %d, %I:%M %p')}"


async def handle_check_status_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]
    user_id = runtime.context["user_id"]
    conversation_id = runtime.context["conversation_id"]

    text = state["messages"][-1].content if state["messages"] else ""
    filename_reference = _extract_filename_reference(text)

    result = await resolve_job_reference(
        db, user_id, conversation_id,
        filename_reference=filename_reference,
        focused_job_id=state.get("focused_job_id"),
    )

    if result.status == ResolutionStatus.AMBIGUOUS:
        lines = "\n".join(_format_candidate(job) for _, job in result.candidates)
        return {"requires_user_input": False,
                "messages": [AIMessage(content=f"I found multiple matching reviews. Which one do you mean?\n\n{lines}")]}

    if result.status == ResolutionStatus.NOT_FOUND:
        return {"requires_user_input": False, "messages": [AIMessage(content="I couldn't find that review.")]}

    if result.status == ResolutionStatus.NO_CONTEXT:
        return {"requires_user_input": False,
                "messages": [AIMessage(content="I don't see any reviews to check on yet - which document did you mean?")]}

    job = result.job
    if job.status.value == "succeeded":
        text = f"Your review of {job.filename} is complete - {job.finding_count} finding(s)."
    elif job.status.value == "failed":
        text = f"Your review of {job.filename} didn't complete: {job.error_message or 'unknown error'}."
    elif job.status.value == "running":
        text = f"Still working on {job.filename}."
    else:
        text = f"{job.filename} is queued and will start shortly."

    return {
        "focused_job_id": result.job_id, "focused_finding_id": None,
        "requires_user_input": False,
        "messages": [AIMessage(content=text)],
    }