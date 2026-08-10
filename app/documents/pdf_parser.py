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
            if text:
                parsed.blocks.append(
                    ContentBlock(
                        text=text,
                        kind=ContentKind.PAGE_TEXT,
                        location=Location(page_number=page_number),
                    )
                )
            elif page.get_images(full=True):
                # No extractable text AND at least one image on the
                # page - likely a scanned page. Flag distinctly from
                # a normal embedded image so findings output can
                # explain the difference to the user.
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

            for image_index, image_info in enumerate(page.get_images(full=True)):
                xref = image_info[0]
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