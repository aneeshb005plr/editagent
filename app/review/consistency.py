"""
app/review/consistency.py

CONSISTENCY-category rules (terminology drift, capitalization drift
across the document) cannot be answered by looking at one block at a
time - "is this term capitalized the same way everywhere" is a
whole-document question. This is a structurally different pass from
deterministic/judgment, not a variant of either - see architecture
doc Section 6 and the taxonomy fix that moved
gram-capitalization-consistency out of a per-block category once
this distinction became concrete while building the engine.

NOT YET CHUNKED/MAP-REDUCED FOR LARGE DOCUMENTS - this sends the
full document's text in one call, same "not yet load-tested at
100MB" caveat as every other unverified-at-scale piece of this
build. A real large document will need this pass redesigned around
extracting a compact representation (e.g. all distinct terms/
capitalization variants found) rather than sending raw block text,
before it can run affordably at 100MB scale. Flagged explicitly
rather than silently assumed to scale.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import Runnable

from app.documents.base import ContentBlock
from app.review.models import Finding, LLMJudgmentBatchResponse
from app.rules.schema import Rule

logger = logging.getLogger("app.review.consistency")

_SYSTEM_PROMPT = """You are reviewing an ENTIRE document (all blocks below) for \
DOCUMENT-LEVEL consistency issues per the rules given - things like the same term, \
role, or concept being referred to differently across DIFFERENT blocks, or the same \
term capitalized inconsistently in different places. Only report a genuine \
inconsistency where the SAME thing is referred to differently in at least two \
locations - do not flag a single block in isolation. Reference the block_id of ONE \
representative occurrence in your finding (the clearest example), and mention the \
other locations/variants in your explanation."""


def _build_document_prompt(
    blocks: list[tuple[str, ContentBlock]],
    rules: tuple[Rule, ...],
) -> str:
    rule_lines = "\n".join(f"  - rule_id: {r.rule_id} | {r.description}" for r in rules)
    block_lines = "\n".join(
        f'[Block: {block_id}] (location: {block.location.display()}): "{block.text}"'
        for block_id, block in blocks
    )
    return f"Rules to check across the whole document:\n{rule_lines}\n\nDocument blocks:\n{block_lines}"


async def run_consistency_pass(
    blocks: list[ContentBlock],
    rules: tuple[Rule, ...],
    base_model: Runnable,
    max_blocks: int = 200,
) -> list[Finding]:
    """rules should already be filtered to CONSISTENCY category and
    the applicable AppliesTo set (caller's responsibility).

    max_blocks is a crude safety cap, not a real solution - see
    module docstring on why this pass isn't yet suitable for a real
    100MB document. Silently truncating past this cap rather than
    failing is a deliberate short-term choice: a partial consistency
    check on the first N blocks is more useful than none, but this
    MUST be revisited before being trusted on large documents."""

    if not rules or not blocks:
        return []

    working_blocks = blocks[:max_blocks]
    if len(blocks) > max_blocks:
        logger.warning(
            "Consistency pass: document has %d blocks, truncating to first %d - "
            "this pass is not yet designed for full-document scale, see module docstring",
            len(blocks),
            max_blocks,
        )

    indexed = [(f"b{i}", block) for i, block in enumerate(working_blocks)]
    block_by_id = {block_id: block for block_id, block in indexed}
    rule_by_id = {r.rule_id: r for r in rules}

    structured_model = base_model.with_structured_output(LLMJudgmentBatchResponse)
    prompt = _build_document_prompt(indexed, rules)

    try:
        response = await structured_model.ainvoke(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )
    except Exception:
        logger.error("Consistency pass failed - skipping", exc_info=True)
        return []

    if not isinstance(response, LLMJudgmentBatchResponse):
        logger.warning("Consistency pass returned unexpected type %s", type(response))
        return []

    findings: list[Finding] = []
    for item in response.findings:
        block = block_by_id.get(item.block_id)
        rule = rule_by_id.get(item.rule_id)
        if block is None or rule is None:
            logger.warning(
                "Consistency finding referenced unknown block_id=%r or rule_id=%r",
                item.block_id,
                item.rule_id,
            )
            continue

        findings.append(
            Finding(
                rule_id=rule.rule_id,
                category=rule.category,
                detection_type=rule.detection_type,
                location_display=block.location.display(),
                original_text=item.original_text,
                explanation=item.explanation,
                suggested_rewrite=item.suggested_rewrite or rule.alternative,
                source_reference=rule.source_reference,
            )
        )

    logger.info("Consistency pass: %d blocks -> %d findings", len(working_blocks), len(findings))
    return findings