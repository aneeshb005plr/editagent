"""
app/documents/image_extraction.py

Turns PENDING image UnsupportedItems (produced by the docx/pptx/pdf
parsers) into reviewable ContentBlocks by extracting any text present
in the image via the GenAI shared service's vision-capable endpoint.

Uses LangChain (langchain_openai.ChatOpenAI), NOT the raw openai SDK
directly - matches how the rest of this codebase talks to the GenAI
service. Built against the CURRENT standard, provider-agnostic
multimodal content-block API (HumanMessage(content_blocks=[...]),
type="image" with base64/mime_type) - confirmed via langchain_core
1.5.3's actual ImageContentBlock definition, not the older raw
OpenAI-style {"type": "image_url", "image_url": {...}} dict, which
is a legacy/provider-specific pattern this version has moved past.

Deliberately a SEPARATE step from parsing, not baked into the parsers
themselves - parsers stay synchronous, cheap, and offline; this step
is async, makes a network call, and can fail/be slow/be skipped
entirely depending on downstream decisions (concurrency budget,
whether image review is enabled for a given job, etc.). Keeping it
separate means the parsers don't need to know or care.

UNVERIFIED AGAINST THE REAL PwC GENAI ENDPOINT. Verified here only
with a MOCKED model exercising the success / no-text / failure code
paths - that proves the logic is sound, NOT that the real endpoint
(a) is reachable via ChatOpenAI's base_url/api_key pattern the same
way OpenAI's own API is, or (b) actually accepts multimodal input
for whatever model GENAI_LLM_MODEL resolves to. Both need a real
smoke test against actual GENAI_BASE_URL/GENAI_API_KEY before this
is trusted in production.
"""

from __future__ import annotations

import asyncio
import base64
import logging

from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from openai import APIError
# ChatOpenAI wraps the openai SDK internally and does not define its
# own API-error hierarchy - errors from the underlying HTTP call
# still surface as openai.APIError (confirmed: langchain_openai adds
# only StreamChunkTimeoutError, nothing else, for this version).
# openai therefore remains a transitive dependency for error typing
# even though nothing here calls it directly.

from app.documents.base import (
    ContentBlock,
    ContentKind,
    ExtractionStatus,
    ParsedDocument,
    UnsupportedItem,
    UnsupportedKind,
)

logger = logging.getLogger("app.documents.image_extraction")

_EXTRACTION_PROMPT = (
    "Extract any readable text that appears in this image, exactly as "
    "written, with no commentary or description. If the image contains "
    "no readable text at all (e.g. it's a purely decorative graphic, "
    "logo, or photo with no text), respond with exactly: NO_TEXT_FOUND"
)

_NO_TEXT_MARKER = "NO_TEXT_FOUND"




async def extract_text_from_image(
    item: UnsupportedItem,
    vision_model: Runnable,
) -> ContentBlock | None:
    """Attempts to extract text from a single image UnsupportedItem.

    Returns a ContentBlock(kind=IMAGE_TEXT) on success with readable
    text, or None if the image had no readable text or extraction
    failed. MUTATES item.extraction_status in place (SUCCEEDED/FAILED)
    so the caller can report accurate per-item outcomes without
    re-deriving state.

    Does NOT clear item.raw_bytes - caller is responsible for
    discarding processed items promptly (see UnsupportedItem's
    docstring re: short-lived byte storage at 100MB scale).
    """

    if item.kind != UnsupportedKind.IMAGE:
        raise ValueError(
            f"extract_text_from_image only handles IMAGE items, got {item.kind}"
        )

    if not item.raw_bytes:
        item.extraction_status = ExtractionStatus.FAILED
        return None

    mime_type = "image/png"
    if item.note and "content_type=" in item.note:
        mime_type = item.note.split("content_type=")[-1].strip()

    b64_data = base64.b64encode(item.raw_bytes).decode("ascii")

    message = HumanMessage(
        content_blocks=[
            {"type": "text", "text": _EXTRACTION_PROMPT},
            {"type": "image", "base64": b64_data, "mime_type": mime_type},
        ]
    )

    try:
        response = await vision_model.ainvoke([message])
        extracted_text = (response.content or "").strip()

        if not extracted_text or extracted_text == _NO_TEXT_MARKER:
            item.extraction_status = ExtractionStatus.SUCCEEDED
            # Succeeded, but genuinely nothing to review - not a
            # failure, just no ContentBlock to produce.
            return None

        item.extraction_status = ExtractionStatus.SUCCEEDED
        return ContentBlock(
            text=extracted_text,
            kind=ContentKind.IMAGE_TEXT,
            location=item.location,
            extraction_method="vision_ocr",
        )

    except APIError:
        logger.warning(
            "Vision extraction failed for image at %s",
            item.location.display(),
            exc_info=True,
        )
        item.extraction_status = ExtractionStatus.FAILED
        return None


async def extract_images_in_document(
    parsed: ParsedDocument,
    vision_model: Runnable,
    max_concurrent: int = 5,
) -> None:
    """Runs extraction for every PENDING image in
    parsed.unsupported_items, appending any resulting ContentBlocks to
    parsed.blocks in place.

    max_concurrent caps simultaneous vision calls - a real, currently
    UNTUNED guess (5). A 100MB deck could plausibly contain dozens of
    images; this needs a value informed by the large-file spike
    (actual cost/latency budget against the shared GenAI service),
    not left at a guessed default when this goes to production.
    """

    pending = [
        item
        for item in parsed.unsupported_items
        if item.kind == UnsupportedKind.IMAGE
        and item.extraction_status == ExtractionStatus.PENDING
    ]

    if not pending:
        return

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _bounded_extract(item: UnsupportedItem) -> ContentBlock | None:
        async with semaphore:
            return await extract_text_from_image(item, vision_model)

    results = await asyncio.gather(
        *[_bounded_extract(item) for item in pending],
        return_exceptions=True,
    )

    succeeded = 0
    failed = 0

    for result in results:
        if isinstance(result, BaseException):
            logger.error("Unexpected error during image extraction", exc_info=result)
            failed += 1
            continue
        if result is not None:
            parsed.blocks.append(result)
            succeeded += 1

    logger.info(
        "Image extraction for %s: %d attempted, %d text blocks added, %d unexpected errors",
        parsed.source_filename,
        len(pending),
        succeeded,
        failed,
    )