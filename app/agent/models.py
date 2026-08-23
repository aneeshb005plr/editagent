"""
app/agent/models.py

Structured-output schemas for LLM calls in the conversational shell.
Pydantic here (not TypedDict, unlike state.py) is deliberate and
correct - these ARE the real boundary where untrusted LLM output
needs actual validation, matching every other structured-output
schema already built in this project (app/review/models.py's
LLMJudgmentFinding).

IntentClassification.intent is Literal-typed - a direct, confirmed
current LangGraph best practice: "never route on raw strings from
LLM output." Verified via direct test that Pydantic rejects an
invalid/hallucinated intent value at construction, not downstream in
a routing function that might silently mishandle an unrecognized
string.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "social",
    "off_topic",
    "knowledge_question",
    "submit_document",
    "check_status",
    "finding_followup",
    "scope_change",
    "new_document",
    "additional_output",
    "unclear",
]


class IntentClassification(BaseModel):
    intent: Intent
    reasoning: str = Field(
        description="Brief reasoning for this classification - not shown to the user, "
        "aids debugging and can improve classification quality as a reasoning step."
    )


class IntakeAnswers(BaseModel):
    """Parses the user's reply to the intake questions. Every field
    is Optional/None-default - a partial or unclear reply should
    leave the corresponding field None, NOT guess, so the intake node
    can correctly re-ask only what's still missing rather than
    silently assuming a default."""

    applies_to: Literal["general", "audit"] | None = Field(
        default=None,
        description="Whether this is a general proposal or an audit/assurance proposal - "
        "None if not clearly answered.",
    )
    is_pcs: bool | None = Field(
        default=None,
        description="Whether this is specifically a PCS (Private Company Services) audit - "
        "only meaningful if applies_to is 'audit'. None if not answered or not applicable.",
    )
    english_variant: Literal["us", "global"] | None = Field(
        default=None,
        description="Whether to review for US English or Global (UK) English conventions - "
        "None if not clearly answered.",
    )