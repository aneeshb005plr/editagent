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

IMAGES ARE NORMALIZED TO PNG BEFORE EVERY VISION CALL - fixed a real,
confirmed production bug found via the first real-endpoint test
against an actual audit RFP PDF: the vision endpoint (Azure OpenAI,
fronted by LiteLLM - both confirmed directly from the real error's
traceback, information not previously known about this project's
infrastructure) rejected the image with "AzureException image...
must be one of jpeg/gif/webp/png". None of the four parsers validate
or convert the native format they extract - pymupdf/python-docx/
python-pptx/openpyxl all return whatever format was actually embedded
in the source file (JPEG2000, JBIG2, TIFF, BMP, and other formats
common in scanned/complex documents are all real possibilities, not
edge cases), and this module was forwarding those raw bytes with the
extracted extension as the mime_type, completely unvalidated. Now
every image is decoded and re-encoded as PNG via Pillow BEFORE
building the vision request, regardless of source format - this is
the correct place for this fix (one shared choke point all four
parsers already feed into), not four separate per-parser fixes.

CONFIRMED VERIFIED AGAINST THE REAL PwC GENAI ENDPOINT as of this
fix - the failure above IS that verification; error handling itself
worked correctly (the APIError was caught, logged, and the rest of
the review completed normally) even before this fix, so the gap was
purely "sends a format the endpoint rejects," not the error-handling
path.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging

from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from openai import APIError
from PIL import Image, UnidentifiedImageError
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


def _normalize_to_png(raw_bytes: bytes) -> bytes | None:
    """Decodes raw_bytes in WHATEVER format it's actually in and
    re-encodes as PNG - confirmed via direct test that this round-
    trips correctly even for a format the vision endpoint itself
    rejects (e.g. TIFF in, valid PNG magic bytes out). Returns None
    if Pillow genuinely can't decode the bytes at all (corrupt data,
    or a format Pillow itself doesn't support - a real but rarer
    failure mode than "valid image, wrong format for the endpoint",
    which this function eliminates entirely)."""

    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img.load()  # force full decode now, not lazily at save() time
    except (UnidentifiedImageError, OSError):
        return None

    if img.mode not in ("RGB", "RGBA", "L"):
        # CMYK, palette, and other modes PNG can't always round-trip
        # cleanly - convert to RGB, the safest universal choice.
        img = img.convert("RGB")

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


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

    png_bytes = _normalize_to_png(item.raw_bytes)

    if png_bytes is None:
        logger.warning(
            "Could not decode image bytes at %s (format unrecognized/corrupt) "
            "- skipping vision extraction",
            item.location.display(),
        )
        item.extraction_status = ExtractionStatus.FAILED
        return None

    b64_data = base64.b64encode(png_bytes).decode("ascii")

    message = HumanMessage(
        content_blocks=[
            {"type": "text", "text": _EXTRACTION_PROMPT},
            {"type": "image", "base64": b64_data, "mime_type": "image/png"},
            # Always image/png now - normalized above, regardless of
            # whatever format was actually embedded in the source
            # file. No longer trusts item.note's extracted extension.
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
    UNTUNED guess (5), though now we have one real data point: a
    single vision call against the real endpoint took ~6 seconds
    (confirmed from real test timing) - a 100MB deck with dozens of
    images could add real minutes at this concurrency level. Still
    needs a value informed by the full large-file spike, not just
    this one data point.
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