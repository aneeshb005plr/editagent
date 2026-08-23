"""app/agent/nodes/finding_followup.py"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agent.context import ChatContext
from app.agent.state import ChatState
from app.jobs import repository
from app.jobs.findings_repository import get_findings_for_job


async def handle_finding_followup_node(state: ChatState, runtime: Runtime[ChatContext]) -> dict:
    db = runtime.context["db"]
    genai_client = runtime.context["genai_client"]
    job_id = state.get("active_job_id")

    if not job_id:
        return {"messages": [AIMessage(content="I don't have a completed review to discuss yet - submit a document first.")]}

    job = await repository.get_job(db, job_id)
    if job is None or job.status.value != "succeeded":
        return {"messages": [AIMessage(content="That review isn't complete yet, so I don't have findings to discuss.")]}

    findings = await get_findings_for_job(db, job_id)
    if not findings:
        return {"messages": [AIMessage(content="That review came back clean - no findings to discuss.")]}

    findings_context = "\n\n".join(
        f"[{i+1}] {f.rule_id} ({f.category.value}) at {f.location_display}: {f.original_text!r}\n"
        f"Explanation: {f.explanation}\nSuggested: {f.suggested_rewrite or 'N/A'}"
        for i, f in enumerate(findings)
    )

    response = await genai_client.ainvoke([
        SystemMessage(content=(
            "Answer the user's question about these specific review findings. Reference "
            "them by number if helpful. Don't invent findings not listed here."
        )),
        HumanMessage(content=f"Findings:\n{findings_context}\n\nQuestion: {state['messages'][-1].content}"),
    ])
    return {"messages": [AIMessage(content=response.content)]}