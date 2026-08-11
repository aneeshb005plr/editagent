"""
scripts/test_review_pipeline.py

Standalone, run-it-yourself validation of the full pipeline against a
REAL file and REAL GenAI endpoint - deliberately bypasses job infra,
FastAPI, and everything else not yet built. This is the fastest path
to closing every "verified via mock only" caveat accumulated so far:

- image_extraction.py's vision call - never hit a real endpoint
- with_structured_output()'s default json_schema mode - never
  confirmed the real GenAI service supports it the same way OpenAI's
  API does
- judgment/consistency pass output QUALITY - mocks proved the wiring
  works, they say nothing about whether real findings are any good

Run from the repo root:

    python scripts/test_review_pipeline.py path/to/real_file.docx
    python scripts/test_review_pipeline.py path/to/real_file.pptx --audit
    python scripts/test_review_pipeline.py path/to/real_file.pdf --no-images

Requires GENAI_BASE_URL and GENAI_API_KEY set in your real .env -
this script builds its own GenAI client directly (NOT via app.llm's
connect_genai(), which expects a FastAPI app instance to attach
state to) since there's no app lifecycle here.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Allow running as `python scripts/test_review_pipeline.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_openai import ChatOpenAI

from app.config import settings
from app.documents.pipeline import parse_and_extract
from app.review.engine import review_document
from app.rules.schema import AppliesTo
from app.rules.taxonomy import RULE_SET


def _build_genai_client() -> ChatOpenAI:
    """Standalone construction, mirroring app.llm.connect_genai()'s
    parameters exactly but without the FastAPI app.state attachment -
    same settings, same confirmed-current constructor args, just for
    a script context rather than a running server."""

    if not settings.GENAI_BASE_URL:
        print("ERROR: GENAI_BASE_URL is not set in your .env - cannot proceed.")
        sys.exit(1)

    if not settings.GENAI_API_KEY or not settings.GENAI_API_KEY.get_secret_value():
        print("ERROR: GENAI_API_KEY is not set in your .env - cannot proceed.")
        sys.exit(1)

    return ChatOpenAI(
        model=settings.GENAI_LLM_MODEL,
        base_url=settings.GENAI_BASE_URL,
        api_key=settings.GENAI_API_KEY.get_secret_value(),
        max_tokens=settings.GENAI_MAX_TOKENS,
        timeout=60.0,
        max_retries=2,
    )


def _print_findings(findings, elapsed_seconds: float) -> None:
    print()
    print("=" * 70)
    print(f"REVIEW COMPLETE - {len(findings)} finding(s) in {elapsed_seconds:.1f}s")
    print("=" * 70)

    if not findings:
        print("No findings. Either the document is clean, or something silently")
        print("failed - check the log output above for batch errors before")
        print("trusting a zero-finding result.")
        return

    by_type: dict[str, int] = {}
    for f in findings:
        by_type[f.detection_type.value] = by_type.get(f.detection_type.value, 0) + 1
    print("By detection type:", by_type)
    print()

    for i, f in enumerate(findings, 1):
        print(f"[{i}] {f.category.value} / {f.detection_type.value} - {f.rule_id}")
        print(f"    Location: {f.location_display}")
        print(f"    Text: {f.original_text!r}")
        print(f"    Explanation: {f.explanation}")
        if f.suggested_rewrite:
            print(f"    Suggested: {f.suggested_rewrite}")
        print(f"    Source: {f.source_reference}")
        print()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file_path", type=str, help="Path to a real .docx/.pptx/.xlsx/.pdf file")
    parser.add_argument("--audit", action="store_true", help="Treat as an audit/assurance proposal")
    parser.add_argument("--pcs", action="store_true", help="Treat as a PCS audit proposal (implies --audit)")
    parser.add_argument("--no-images", action="store_true", help="Skip vision-based image text extraction")
    args = parser.parse_args()

    file_path = Path(args.file_path)
    if not file_path.exists():
        print(f"ERROR: file not found: {file_path}")
        sys.exit(1)

    applies_to = AppliesTo.AUDIT if (args.audit or args.pcs) else AppliesTo.GENERAL

    print(f"Loading {file_path.name} ({file_path.stat().st_size / 1024:.1f} KB)...")
    file_bytes = file_path.read_bytes()

    print("Building GenAI client against your real endpoint...")
    genai_client = _build_genai_client()

    print("Parsing" + (" (skipping image extraction)" if args.no_images else " and extracting image text via vision")+ "...")
    t0 = time.monotonic()
    parsed = await parse_and_extract(
        file_bytes=file_bytes,
        filename=file_path.name,
        max_size_mb=settings.MAX_FILE_SIZE_MB,
        vision_model=genai_client.bind(temperature=0, max_tokens=1000),
        extract_images=not args.no_images,
    )
    parse_elapsed = time.monotonic() - t0

    print(
        f"Parsed in {parse_elapsed:.1f}s: {len(parsed.blocks)} text blocks, "
        f"{len(parsed.unsupported_items)} unsupported items, "
        f"{parsed.total_char_count} chars"
    )

    print(f"Running review (applies_to={applies_to.value}, is_pcs={args.pcs})...")
    t1 = time.monotonic()
    findings = await review_document(
        parsed=parsed,
        rule_set=RULE_SET,
        applies_to=applies_to,
        judgment_model=genai_client,
        is_pcs=args.pcs,
    )
    review_elapsed = time.monotonic() - t1

    _print_findings(findings, parse_elapsed + review_elapsed)
    print(f"(parse: {parse_elapsed:.1f}s, review: {review_elapsed:.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())