"""app/agent/nodes/check_status.py"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.jobs import repository


async def handle_check_status_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]
    user_id = runtime.context["user_id"]

    job_id = state.get("active_job_id")
    job = await repository.get_job(db, job_id) if job_id else None

    if job is None:
        recent = await repository.list_jobs_for_user(db, user_id, limit=1)
        job = recent[0] if recent else None

    if job is None:
        return {"messages": [AIMessage(content="I don't see any reviews from you yet - upload a document to get started.")]}

    if job.status.value == "succeeded":
        text = f"Your review of {job.filename} is complete - {job.finding_count} finding(s). Want me to walk through them?"
    elif job.status.value == "failed":
        text = f"Your review of {job.filename} didn't complete: {job.error_message or 'an unknown error occurred'}."
    elif job.status.value == "running":
        text = f"Still working on {job.filename} - I'll let you know as soon as it's done."
    else:
        text = f"{job.filename} is queued and will start shortly."

    return {"messages": [AIMessage(content=text)]}