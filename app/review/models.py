"""
app/review/models.py

Two distinct model tiers, deliberately:

1. Finding (Pydantic) - the FULL internal representation of a single
   review finding. This crosses real boundaries (will be stored in
   Mongo, returned via API/chat output) - Pydantic is the right tool
   here, same reasoning as config.py, unlike app/documents/base.py's
   hot-path dataclasses.

2. LLMJudgmentFinding / LLMJudgmentBatchResponse (Pydantic) - the
   MINIMAL shape the LLM is asked to return for judgment-rule checks.
   Deliberately does NOT include category, source_reference, or any
   field our own code already knows deterministically from the Rule
   being checked - the LLM only judges violates/explanation/rewrite,
   and our code enriches the rest afterward. This reduces tokens,
   reduces hallucination surface (the model can't get a category
   name wrong if it's never asked to produce one), and keeps the
   taxonomy as the single source of truth for anything we already
   know without asking.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.rules.schema import DetectionType, RuleCategory


class Finding(BaseModel):
    """A single review finding - the shape returned to the user,
    matching the confirmed MVP output requirement: category,
    location, original text, explanation, suggested rewrite."""

    rule_id: str
    category: RuleCategory
    detection_type: DetectionType
    location_display: str
    # Plain display string (Location.display()), not the Location
    # dataclass itself - Finding is a boundary type (Mongo/API), the
    # dataclass is a hot-path internal type (see app/documents/base.py)
    # - deliberately not mixed.
    original_text: str
    explanation: str
    suggested_rewrite: str | None = None
    source_reference: str = ""
    # Rule's own citation (e.g. "Style Guide p.89") - carried through
    # for traceability/audit, not shown prominently to the end user
    # necessarily, but available.


class LLMJudgmentFinding(BaseModel):
    """Minimal per-item shape the LLM returns for ONE judged
    violation. block_id and rule_id are both round-tripped values we
    supplied in the prompt (block identifiers, rule_ids from the
    candidate set) - the model selects among them, it doesn't
    generate new ones, which keeps them reliably joinable back to our
    own ContentBlock/Rule objects afterward."""

    block_id: str = Field(description="The id of the block this finding applies to, from the provided list")
    rule_id: str = Field(description="The id of the rule this finding violates, from the provided candidate rules")
    original_text: str = Field(description="The exact excerpt from the block that violates the rule")
    explanation: str = Field(description="Brief explanation of why this violates the rule, in this specific context")
    suggested_rewrite: str | None = Field(
        default=None,
        description="A suggested rewrite fixing the issue, preserving original meaning - omit if no clear rewrite applies",
    )


class LLMJudgmentBatchResponse(BaseModel):
    """Wraps a list of findings for one batched LLM call - a batch
    covers multiple blocks and multiple candidate rules at once, so
    the response is a flat list rather than one call per block/rule
    pair (which would be prohibitively slow/expensive at 100MB scale
    - see review engine design discussion)."""

    findings: list[LLMJudgmentFinding] = Field(default_factory=list)