"""
app/review/judgment.py

Runs every JUDGMENT rule via batched, structured-output LLM calls.
This is the expensive pass - real design effort goes into keeping
it as cheap as possible without sacrificing the deterministic-first
principle (see architecture doc Section 6).

CANDIDATE SELECTION, per block, before any LLM call:
- Rules with trigger_terms: candidate only if a trigger term appears
  (case-insensitive substring) in the block's text. This does NOT
  decide whether the rule is actually violated - only whether it's
  even worth asking the model about this block. A block containing
  "ensure" is a CANDIDATE for the guarantee-language rule; whether
  it's used in the restricted sense is still the model's call.
- Rules with NO trigger_terms (most grammar rules - subject-verb
  agreement can appear anywhere) are candidates for EVERY block.
  This is intentional, not an inefficiency: grammar checks need to
  run broadly, and there's no useful keyword pre-filter for them.

BATCHING: blocks are grouped so multiple blocks' worth of candidate
checks go into ONE LLM call with structured output, rather than one
call per block or per rule - the latter would be prohibitively slow/
expensive at 100MB scale (potentially thousands of blocks).

BATCHES RUN CONCURRENTLY, bounded by a semaphore - FIXED A REAL,
CONFIRMED PRODUCTION BUG: this previously processed batches in a
strictly sequential for-loop (one full ainvoke() awaited before the
next began). Confirmed via direct math against real production
timing data (a single batch took ~30s on a real endpoint) that this
fully explains a real reported incident: an 8MB PPTX (plausibly
100-300+ text blocks once slide text, table cells, etc. are all
counted) stuck "processing" for 15+ minutes - at batch_size=15,
that's ~20 sequential batches, ~10 minutes for the judgment pass
alone before adding image extraction and the consistency pass on
top. Fixed via asyncio.Semaphore-bounded concurrent batches, matching
the exact same pattern already used in image_extraction.py for
images, and confirmed as current standard practice for concurrent
LangChain/OpenAI-compatible calls (bounded asyncio.gather + Semaphore,
not unbounded concurrency, to protect the shared GenAI service from
a burst of simultaneous requests).

RESILIENCE: one batch failing (timeout, malformed response) is
logged and skipped, not allowed to kill the whole review - same
per-unit isolation principle as knowledge-sync-worker's per-agent
try/except. This property is PRESERVED under concurrency - each
batch's own try/except still isolates its own failure, and
asyncio.gather collects every batch's result (or None on failure)
regardless of which batches succeeded or failed.

A SEPARATE, REAL RISK THIS FIX DOES NOT ADDRESS: langchain_openai's
ChatOpenAI defaults to multiple retries with exponential backoff on
a slow/flaky call - a single bad request can silently turn into a
multi-minute "phantom wait" before it even fails, independent of
this module's own concurrency. Confirmed as a documented real-world
failure mode in production LangChain usage (not just theoretical).
Whether app/llm.py's connect_genai() sets explicit `timeout`/
`max_retries` on the shared ChatOpenAI client has NOT been confirmed
by this agent (never seen that file's current content) - worth
checking directly; scripts/test_review_pipeline.py's own standalone
client construction sets timeout=60.0, max_retries=2 as a deliberate
safety measure, but that's this agent's own test script, not
necessarily what the real production client does.
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.runnables import Runnable

from app.documents.base import ContentBlock
from app.review.matching import term_matches
from app.review.models import Finding, LLMJudgmentBatchResponse
from app.rules.schema import Rule

logger = logging.getLogger("app.review.judgment")

_DEFAULT_BATCH_SIZE = 15
# Blocks per LLM call - untuned default, same status as
# image_extraction.py's max_concurrent guess. Needs real tuning once
# cost/latency data exists against the actual GenAI endpoint and
# actual document sizes.

_DEFAULT_MAX_CONCURRENT_BATCHES = 5
# How many batches can be in flight to the GenAI service at once -
# untuned default, same status as image_extraction.py's own
# max_concurrent=5. Bounds concurrent LLM load rather than firing
# every batch at once (unbounded concurrency risks overwhelming the
# shared GenAI service - confirmed as the standard concern behind
# every real concurrent-LLM-call pattern found via search).

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
    candidates: list[Rule] = []

    for rule in judgment_rules:
        if not rule.trigger_terms:
            # No keyword pre-filter available - always a candidate.
            candidates.append(rule)
            continue
        # Fixed real bug: was naive substring matching, which
        # candidate-matched "who" inside "whole", "less" inside
        # "unless", "trust" inside "Trust Solutions" - confirmed by
        # direct testing. term_matches() uses lookaround bounding
        # instead of \b, since \b itself fails for symbol-starting/
        # ending triggers (confirmed: "&" via \b doesn't match
        # "risk & capital" at all).
        if any(term_matches(term, block_text) for term in rule.trigger_terms):
            candidates.append(rule)

    return candidates


def _build_batch_prompt(
    batch: list[tuple[str, ContentBlock, list[Rule]]],
) -> str:
    """batch is [(block_id, block, candidate_rules), ...] - already
    filtered to blocks that have at least one candidate rule."""

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


async def _run_one_batch(
    structured_model: Runnable,
    batch: list[tuple[str, ContentBlock, list[Rule]]],
    batch_num: int,
    total_batches: int,
) -> LLMJudgmentBatchResponse | None:
    """One batch's full call, isolated - returns None on any failure
    (logged) rather than raising, so asyncio.gather's caller doesn't
    need exception-handling logic beyond checking for None. Same
    resilience contract as before the concurrency fix, just moved
    into its own function so it can be scheduled independently."""

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
            batch_num + 1,
            total_batches,
            len(batch),
            exc_info=True,
        )
        return None

    if not isinstance(response, LLMJudgmentBatchResponse):
        logger.warning(
            "Judgment batch %d/%d returned unexpected type %s - skipping",
            batch_num + 1,
            total_batches,
            type(response),
        )
        return None

    return response


async def run_judgment_rules(
    blocks: list[ContentBlock],
    rules: tuple[Rule, ...],
    base_model: Runnable,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    max_concurrent_batches: int = _DEFAULT_MAX_CONCURRENT_BATCHES,
) -> list[Finding]:
    """rules should already be filtered to JUDGMENT-only and to the
    applicable AppliesTo set (caller's responsibility, same as
    run_deterministic_rules). base_model is the shared, UNBOUND GenAI
    client (app.llm.get_genai_client) - with_structured_output() is
    applied here, not passed in pre-bound, since this module owns the
    specific response schema it needs."""

    structured_model = base_model.with_structured_output(LLMJudgmentBatchResponse)

    # Build (block_id, block, candidate_rules) for every block that
    # has at least one candidate - skip blocks with none, nothing to
    # send the LLM about.
    prepared: list[tuple[str, ContentBlock, list[Rule]]] = []
    for i, block in enumerate(blocks):
        candidates = _select_candidate_rules(block.text, rules)
        if candidates:
            prepared.append((f"b{i}", block, candidates))

    if not prepared:
        return []

    block_by_id = {block_id: block for block_id, block, _ in prepared}
    rule_by_id = {r.rule_id: r for r in rules}

    batches = [prepared[i : i + batch_size] for i in range(0, len(prepared), batch_size)]

    semaphore = asyncio.Semaphore(max_concurrent_batches)

    async def _bounded_run(batch_num: int, batch: list) -> LLMJudgmentBatchResponse | None:
        async with semaphore:
            return await _run_one_batch(structured_model, batch, batch_num, len(batches))

    # FIXED REAL BUG (see module docstring): previously a sequential
    # for-loop, one full ainvoke() awaited before the next batch even
    # started - confirmed via math against real timing data to fully
    # explain a real reported 15+ minute stall on an 8MB document.
    # Now runs up to max_concurrent_batches batches at once.
    responses = await asyncio.gather(
        *[_bounded_run(i, batch) for i, batch in enumerate(batches)]
    )

    findings: list[Finding] = []
    for response in responses:
        if response is None:
            continue

        for item in response.findings:
            block = block_by_id.get(item.block_id)
            rule = rule_by_id.get(item.rule_id)

            if block is None or rule is None:
                # Model referenced a block_id/rule_id we never gave it
                # - degrade gracefully (drop this one finding) rather
                # than crash the whole batch's otherwise-valid results.
                logger.warning(
                    "Judgment finding referenced unknown block_id=%r or "
                    "rule_id=%r - dropping this finding",
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

    logger.info(
        "Judgment pass: %d batches (max %d concurrent), %d blocks with candidates -> %d findings",
        len(batches),
        max_concurrent_batches,
        len(prepared),
        len(findings),
    )

    return findings