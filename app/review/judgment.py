"""
app/review/judgment.py

Runs every JUDGMENT rule via batched, structured-output LLM calls.

CANDIDATE SELECTION, per block: rules with trigger_terms are
candidates only if a term appears (case-insensitive substring) in
the block; rules with NO trigger_terms are candidates for every
block (most grammar rules - no useful keyword pre-filter exists).

BATCHING: blocks are grouped so multiple blocks go into ONE LLM call.

RESILIENCE: one batch failing is logged and skipped, not allowed to
kill the whole review.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import Runnable

from app.documents.base import ContentBlock
from app.review.models import Finding, LLMJudgmentBatchResponse
from app.rules.schema import Rule

logger = logging.getLogger("app.review.judgment")

_DEFAULT_BATCH_SIZE = 15

_SYSTEM_PROMPT = """You are reviewing excerpts from a PwC pursuit/proposal document \
against a specific set of writing-quality and compliance rules. For each block below, \
check ONLY the candidate rules listed for that block. Only report a finding when the \
rule is genuinely violated in context - the presence of a trigger word alone is NOT \
a violation; judge the actual sense/usage. Do not invent rule_ids or block_ids beyond \
what is given. If a block has no genuine violations among its candidate rules, simply \
report no findings for it. Quote the exact violating text in original_text."""


def _select_candidate_rules(
    block_text: str,
    judgment_rules: tuple[Rule, ...],
) -> list[Rule]:
    text_lower = block_text.lower()
    candidates: list[Rule] = []

    for rule in judgment_rules:
        if not rule.trigger_terms:
            candidates.append(rule)
            continue
        if any(term.lower() in text_lower for term in rule.trigger_terms):
            candidates.append(rule)

    return candidates


def _build_batch_prompt(
    batch: list[tuple[str, ContentBlock, list[Rule]]],
) -> str:
    sections = []
    for block_id, block, candidate_rules in batch:
        rule_lines = "\n".join(
            f"  - rule_id: {r.rule_id} | {r.description}"
            + (f" | Example of a violation: {r.example_before!r}" if r.example_before else "")
            for r in candidate_rules
        )
        sections.append(
            f"[Block: {block_id}] (location: {block.location.display()})\n"
            f'Text: "{block.text}"\n'
            f"Candidate rules for this block:\n{rule_lines}"
        )

    return "\n\n".join(sections)


async def run_judgment_rules(
    blocks: list[ContentBlock],
    rules: tuple[Rule, ...],
    base_model: Runnable,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> list[Finding]:
    structured_model = base_model.with_structured_output(LLMJudgmentBatchResponse)

    prepared: list[tuple[str, ContentBlock, list[Rule]]] = []
    for i, block in enumerate(blocks):
        candidates = _select_candidate_rules(block.text, rules)
        if candidates:
            prepared.append((f"b{i}", block, candidates))

    if not prepared:
        return []

    block_by_id = {block_id: block for block_id, block, _ in prepared}
    rule_by_id = {r.rule_id: r for r in rules}

    findings: list[Finding] = []
    batches = [prepared[i : i + batch_size] for i in range(0, len(prepared), batch_size)]

    for batch_num, batch in enumerate(batches):
        prompt = _build_batch_prompt(batch)
        try:
            response = await structured_model.ainvoke(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            )
        except Exception:
            logger.error(
                "Judgment batch %d/%d failed (%d blocks) - skipping this batch, "
                "continuing with the rest of the review",
                batch_num + 1, len(batches), len(batch), exc_info=True,
            )
            continue

        if not isinstance(response, LLMJudgmentBatchResponse):
            logger.warning(
                "Judgment batch %d/%d returned unexpected type %s - skipping",
                batch_num + 1, len(batches), type(response),
            )
            continue

        for item in response.findings:
            block = block_by_id.get(item.block_id)
            rule = rule_by_id.get(item.rule_id)

            if block is None or rule is None:
                logger.warning(
                    "Judgment finding referenced unknown block_id=%r or "
                    "rule_id=%r - dropping this finding",
                    item.block_id, item.rule_id,
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

    logger.info(
        "Judgment pass: %d batches, %d blocks with candidates -> %d findings",
        len(batches), len(prepared), len(findings),
    )

    return findings