"""
app/agent/models.py

Intent classification schemas. PendingIntakeTurnClassification
(Phase 3B/3C) replaces the earlier IntakeInterpretation model -
combines intake-answer interpretation with detour detection in one
schema/call, since a pending intake turn now needs to distinguish
answering/continuing/cancelling from being about something else
entirely (a detour), not just answer/cancel/unrelated. See that
class's own docstring and app/agent/nodes/classify_intent.py for the
full reasoning.
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
    "attachment_conflict",
    "unclear",
]


class IntentClassification(BaseModel):
    intent: Intent
    reasoning: str = Field(
        description="Brief reasoning for this classification - not shown to the user, "
        "aids debugging and can improve classification quality as a reasoning step."
    )


class PendingIntakeTurnClassification(BaseModel):
    """Phase 3C: classifies a turn arriving WHILE a document intake
    is pending, when the turn is NOT a structurally-obvious
    deterministic case (a fresh attachment, or an exact "continue"/
    "cancel" phrase - those are recognized without any LLM call, see
    classify_intent_node). One combined call replaces what would
    otherwise be two separate calls (intake interpretation + general
    intent classification) - it either says this turn IS about the
    pending intake (answer/continue/cancel), or says it's a DETOUR
    and classifies what the user actually wants using the SAME
    Intent taxonomy used everywhere else, so the pending intake can
    be left untouched while the detour is handled normally."""

    action: Literal["intake_answer", "continue_intake", "cancel_intake", "detour"] = Field(
        description="'intake_answer' if this responds to the pending intake question, even "
        "partially; 'continue_intake' if the user wants to resume/continue without giving new "
        "information right now; 'cancel_intake' if they want to abandon the pending submission; "
        "'detour' if this is about something else entirely - the pending intake must be left "
        "completely untouched in that case."
    )
    applies_to: Literal["general", "audit"] | None = None
    is_pcs: bool | None = None
    english_variant: Literal["us", "global"] | None = None
    detour_intent: Intent | None = Field(
        default=None,
        description="Only set when action=='detour' - classify what the user actually wants "
        "using the same intent taxonomy used for normal classification.",
    )