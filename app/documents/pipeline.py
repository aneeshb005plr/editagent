"""
app/documents/pipeline.py

The missing connective layer: dispatcher.parse_document() is
synchronous (pure parsing, no network calls); image_extraction.py's
extract_images_in_document() is asynchronous (real GenAI calls).
Nothing previously called the latter after the former - each was
tested and correct in isolation, but a document parsed via the
dispatcher alone stops at PENDING image items and never actually
gets them reviewed. This module is that missing wiring.

This is the function callers (the future job worker, or any route/
test that needs a fully-resolved document) should actually use -
NOT dispatcher.parse_document() directly, unless image/chart text
extraction is deliberately being skipped for that call site.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import Runnable

from app.documents.base import ParsedDocument
from app.documents.dispatcher import parse_document
from app.documents.image_extraction import extract_images_in_document

logger = logging.getLogger("app.documents.pipeline")


async def parse_and_extract(
    file_bytes: bytes,
    filename: str,
    max_size_mb: int,
    vision_model: Runnable,
    max_concurrent_images: int = 5,
    extract_images: bool = True,
) -> ParsedDocument:
    """Full document processing: parse (sync, fast) then extract any
    embedded image text (async, network-bound) - the combination
    dispatcher.parse_document() and image_extraction.py each assumed
    would happen, but neither wired together until now.

    extract_images defaults to True but is exposed as a real toggle -
    useful for tests that don't want a live GenAI dependency, and
    potentially for a future per-job setting if image review turns
    out to need to be optional (e.g. cost control on very large
    files - see the concurrency/cost caveats already flagged in
    image_extraction.py's docstring).
    """

    parsed = parse_document(file_bytes, filename, max_size_mb)

    if extract_images:
        await extract_images_in_document(
            parsed,
            vision_model,
            max_concurrent=max_concurrent_images,
        )
    else:
        logger.info(
            "Skipping image extraction for %s (extract_images=False) - "
            "%d image(s) remain PENDING/unreviewed",
            filename,
            sum(1 for i in parsed.unsupported_items if i.raw_bytes),
        )

    return parsed