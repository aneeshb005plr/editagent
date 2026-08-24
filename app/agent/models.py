"""
app/agent/models.py

FIX for external review point 4 (mid-intake cancellation regression):
IntakeInterpretation replaces the old IntakeAnswers-only parsing with
a genuine three-way classification (answer/cancel/unrelated) - the
previous design silently swallowed anything that wasn't a parseable
answer into an empty dict and just asked again forever, trapping the
user. Now cancellation is a real, detected action, and "unrelated"
gets an honest acknowledgment rather than silent re-asking.
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


class IntakeInterpretation(BaseModel):
    """Interprets a reply given DURING document-review intake -
    distinguishes answering the current question from asking to
    cancel, from saying something the model can't relate to either.
    Every answer field is Optional/None-default - a partial or
    unclear answer should leave the corresponding field None, NOT
    guess."""

    action: Literal["answer", "cancel", "unrelated"] = Field(
        description="'answer' if this responds to the current intake question (even partially), "
        "'cancel' if the user wants to stop/abandon this review submission, "
        "'unrelated' if it's neither."
    )
    applies_to: Literal["general", "audit"] | None = None
    is_pcs: bool | None = None
    english_variant: Literal["us", "global"] | None = None