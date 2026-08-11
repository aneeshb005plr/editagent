"""
app/review/engine.py

The single entry point tying together app/documents/ (parsing),
app/rules/ (taxonomy), and this package's three rule runners into
one function: review_document(). This is the "review engine" as a
standalone capability, callable independently of the conversational
LangGraph shell - per the architecture doc's Section 2 principle.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import Runnable

from app.documents.base import ParsedDocument
from app.review.consistency import run_consistency_pass
from app.review.deterministic import run_deterministic_rules
from app.review.judgment import run_judgment_rules
from app.review.models import Finding
from app.rules.schema import AppliesTo, DetectionType, RuleCategory, RuleSet

logger = logging.getLogger("app.review.engine")


async def review_document(
    parsed: ParsedDocument,
    rule_set: RuleSet,
    applies_to: AppliesTo,
    judgment_model: Runnable,
) -> list[Finding]:
    """Runs the full review pipeline against an already-parsed
    document (see app.documents.pipeline.parse_and_extract - image
    text should already be extracted into parsed.blocks before this
    is called, so it gets reviewed like any other text).

    judgment_model is the SHARED, UNBOUND GenAI client (from
    app.llm.get_genai_client) - both the judgment and consistency
    passes apply their own with_structured_output() on top of it
    independently; this function does not pre-bind anything, so the
    same underlying connection pool is reused across all LLM calls
    in a review (see app/llm.py's reasoning on why a shared client
    matters).

    applies_to determines which rule subset runs - GENERAL always;
    GENERAL+AUDIT only when the document was confirmed as an audit/
    assurance proposal at intake (see architecture doc Section 6 -
    this is a real, user-answered decision, never auto-detected).
    """

    applicable_rules = rule_set.for_applies_to(applies_to)

    # CONSISTENCY-category rules need the whole document, not one
    # block at a time - routed to a structurally separate pass, not
    # folded into the per-block deterministic/judgment split. See
    # app/review/consistency.py and the taxonomy fix that moved
    # gram-capitalization-consistency out of a per-block category.
    consistency_rules = tuple(
        r for r in applicable_rules if r.category == RuleCategory.CONSISTENCY
    )
    per_block_rules = tuple(
        r for r in applicable_rules if r.category != RuleCategory.CONSISTENCY
    )

    deterministic_rules = tuple(
        r for r in per_block_rules if r.detection_type == DetectionType.DETERMINISTIC
    )
    judgment_rules = tuple(
        r for r in per_block_rules if r.detection_type == DetectionType.JUDGMENT
    )

    logger.info(
        "Reviewing %s: %d blocks, applies_to=%s (%d deterministic, %d judgment, "
        "%d consistency rules active)",
        parsed.source_filename,
        len(parsed.blocks),
        applies_to.value,
        len(deterministic_rules),
        len(judgment_rules),
        len(consistency_rules),
    )

    findings: list[Finding] = []

    # Deterministic FIRST, always, cheap - runs regardless of what
    # else happens, no LLM dependency, no failure mode beyond a bad
    # regex (already guarded in run_deterministic_rules).
    findings.extend(run_deterministic_rules(parsed.blocks, deterministic_rules))

    # Judgment SECOND - the expensive pass. A failure here (see
    # run_judgment_rules' per-batch resilience) degrades the review,
    # it doesn't abort it - deterministic findings above are already
    # locked in regardless of what happens next.
    findings.extend(
        await run_judgment_rules(parsed.blocks, judgment_rules, judgment_model)
    )

    # Consistency THIRD - needs the whole document; also the pass
    # least ready for 100MB scale (see consistency.py's own caveat).
    findings.extend(
        await run_consistency_pass(parsed.blocks, consistency_rules, judgment_model)
    )

    logger.info(
        "Review complete for %s: %d total findings",
        parsed.source_filename,
        len(findings),
    )

    return findings