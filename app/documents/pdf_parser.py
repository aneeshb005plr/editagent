"""
app/documents/pdf_parser.py

Parses .pdf files into the common ParsedDocument representation
(app/documents/base.py). Built and verified against pymupdf 1.28.2.
Uses `import pymupdf` directly, NOT `import fitz` - fitz is the old
alias and is deprecated in this version (confirmed via a runtime
deprecation warning during verification, not assumed).

DELIBERATELY NOT using pymupdf4llm.to_markdown() here, despite it
being in requirements.txt for possible future use: verification
testing showed it silently invokes Tesseract OCR (printed "Using
Tesseract for OCR processing" partway through a plain to_markdown()
call on a normal, non-scanned fixture). Tesseract happened to be
present in the sandbox used for verification, but that's an
uncontrolled, undeclared system-level dependency - nothing in this
project has confirmed Tesseract will exist in the real production
container. Using raw pymupdf page.get_text() instead, which has no
such hidden dependency, and keeps PDF image-handling consistent
with the docx/pptx parsers (flag now, extract via our own
controlled vision step later - not a silent, format-specific OCR
path). Revisit pymupdf4llm deliberately, with Tesseract's presence
explicitly decided, if PDF structure (headings/tables) ever needs
to be distinguished beyond flat per-page text.

NO HEADING/PARAGRAPH DISTINCTION: raw get_text() returns flat text
per page with no structural markup - unlike docx/pptx, PDF content
here is uniformly ContentKind.PAGE_TEXT. Acceptable for MVP (rules
still apply per-block regardless of kind); revisit only if findings
need to distinguish "this was a heading" specifically for PDFs.

NOT YET LOAD-TESTED AT 100MB.

IMAGE DETECTION uses page.get_image_info(xrefs=True), NOT page.
get_images() - confirmed via pymupdf maintainer guidance that
get_images() reports images merely REFERENCED in a page's inherited
/Resources dictionary, not necessarily images actually displayed on
that specific page, which could cause false SCANNED_PAGE flags and
duplicate/mislocated IMAGE items. get_image_info() reports only
images with a real bounding box (i.e. actually rendered there).

NOT DEDUPED ACROSS PAGES, deliberately: if the same physical image
(e.g. a company logo) appears on many pages, each occurrence gets
its own IMAGE item at its own page location - correct for accurate
per-page findings, but means the same bytes get sent through vision
extraction once per occurrence rather than once total. Real dedup
(by xref or image_info's "digest" content hash) belongs in the
extraction/orchestration layer that calls this parser, where it can
be weighed against the batch's actual cost/latency budget, not
hardcoded into the parser itself.

EXPLICITLY OUT OF SCOPE, not silently missing:
- Images inside annotation appearance streams (/AP) - stamps, seals,
  some signatures - are invisible to get_image_info() the same way
  they were to get_images(); no direct pymupdf API surfaces them.
- Vector graphics/drawings (charts, diagrams, plots drawn with PDF
  path commands rather than embedded raster images) are not
  detected at all - pymupdf's page.get_drawings() could surface the
  underlying paths, but reliably distinguishing "this is a
  chart/diagram" from decorative lines or table borders is a hard,
  unreliable heuristic problem, not attempted here. Same class of
  gap as PDF charts flagged during broader chart/image scoping
  discussion.
- Font-broken text (bad ToUnicode mappings producing garbled but
  technically non-empty extracted text) has no cheap, general
  detection - such a page would pass the scanned-page check (text
  exists) and get reviewed as garbage PAGE_TEXT with no flag. No
  fix attempted; would need OCR as a fallback, which is the same
  open scope question as scanned-page handling generally.
"""

from __future__ import annotations

import logging

import pymupdf

from app.documents.base import (
    ContentBlock,
    ContentKind,
    ExtractionStatus,
    Location,
    ParsedDocument,
    UnsupportedItem,
    UnsupportedKind,
)

logger = logging.getLogger("app.documents.pdf_parser")


def parse_pdf(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Parses a .pdf file's readable content into a ParsedDocument.

    File-size validation is the DISPATCHER's responsibility (single
    source of truth against settings.MAX_FILE_SIZE_MB) - this
    function assumes it's already been called with an acceptable
    size and focuses purely on content extraction.
    """

    doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    parsed = ParsedDocument(source_filename=filename, file_type="pdf")

    try:
        for page_index, page in enumerate(doc):
            page_number = page_index + 1  # 1-indexed for user-facing display

            text = page.get_text().strip()

            # get_image_info(xrefs=True) reports images actually
            # DISPLAYED on this specific page (each has a real bbox),
            # NOT page.get_images() - which reports images merely
            # REFERENCED in the page's /Resources dictionary. PDF
            # pages can inherit a shared /Resources dict from an
            # ancestor in the page tree, so get_images() can report
            # the same image on every page that inherits the
            # dictionary even when only one page actually shows it.
            # This was a real correctness bug in the prior version,
            # not just a documentation gap: it could falsely flag a
            # genuinely blank page as SCANNED_PAGE, and could emit
            # duplicate IMAGE items (with duplicate bytes) for a
            # single physical image across multiple pages that merely
            # reference it. Confirmed via pymupdf's own maintainer
            # guidance and by checking get_image_info()'s real return
            # shape (bbox/xref/digest per actual occurrence).
            #
            # NOTE: a `clip=pymupdf.INFINITE_RECT` parameter was
            # considered (to catch images with off-page/negative-
            # origin bounding boxes) but confirmed NOT to exist on
            # get_image_info() in this installed version (1.28.2) -
            # calling it raises TypeError. Omitted; off-page images
            # are a known, accepted gap for now (see module docstring).
            image_infos = page.get_image_info(xrefs=True)

            if text:
                parsed.blocks.append(
                    ContentBlock(
                        text=text,
                        kind=ContentKind.PAGE_TEXT,
                        location=Location(page_number=page_number),
                    )
                )
            elif image_infos:
                # No extractable text AND at least one image actually
                # DISPLAYED on the page - likely a scanned page. Using
                # image_infos (not the old get_images() check) means
                # this no longer false-positives on a page that merely
                # inherits an unused /Resources image reference.
                parsed.unsupported_items.append(
                    UnsupportedItem(
                        kind=UnsupportedKind.SCANNED_PAGE,
                        location=Location(page_number=page_number),
                        note="no extractable text - likely a scanned page",
                        extraction_status=ExtractionStatus.NOT_APPLICABLE,
                        # NOT_APPLICABLE for now: scanned-page OCR was
                        # explicitly flagged as an open scope question
                        # (see conversation history) - default to not
                        # attempting it until that's decided, rather
                        # than silently trying.
                    )
                )

            for image_info in image_infos:
                xref = image_info["xref"]
                try:
                    extracted = doc.extract_image(xref)
                    parsed.unsupported_items.append(
                        UnsupportedItem(
                            kind=UnsupportedKind.IMAGE,
                            location=Location(page_number=page_number),
                            note=f"content_type=image/{extracted['ext']}",
                            raw_bytes=extracted["image"],
                            extraction_status=ExtractionStatus.PENDING,
                        )
                    )
                except Exception:
                    logger.warning(
                        "Could not extract image xref=%s on page %d",
                        xref,
                        page_number,
                        exc_info=True,
                    )
                    parsed.unsupported_items.append(
                        UnsupportedItem(
                            kind=UnsupportedKind.IMAGE,
                            location=Location(page_number=page_number),
                            note="image bytes unavailable",
                            extraction_status=ExtractionStatus.FAILED,
                        )
                    )
    finally:
        doc.close()

    logger.info(
        "Parsed %s: %d text blocks, %d unsupported items (%.0f chars)",
        filename,
        len(parsed.blocks),
        len(parsed.unsupported_items),
        parsed.total_char_count,
    )

    return parsed