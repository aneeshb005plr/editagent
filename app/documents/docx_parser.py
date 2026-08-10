"""
app/documents/docx_parser.py

Parses .docx files into the common ParsedDocument representation
(app/documents/base.py). Built and verified against python-docx 1.2.0
- see inline notes for API specifics confirmed against the real
library rather than assumed from memory (document.part.related_parts
is a dict, NOT a related_part() method - that would have been a
real bug if guessed).

KNOWN SIMPLIFICATION, deliberate for MVP: paragraphs and tables are
each processed in their own document order, but paragraph-vs-table
INTERLEAVING order is not preserved (python-docx has no built-in
body-order iterator; doing this properly means walking
document.element.body's raw XML children and dispatching by tag,
which adds real complexity for something review findings don't
need - a finding's location matters, its position relative to a
table three paragraphs later does not). Revisit only if a real
requirement for full document reconstruction appears.

NOT YET LOAD-TESTED AT 100MB - python-docx loads the full document
into memory; this is the parser most in need of the large-file
spike flagged in the architecture doc.
"""

from __future__ import annotations

import io
import logging

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.documents.base import (
    ContentBlock,
    ContentKind,
    Location,
    ParsedDocument,
    UnsupportedItem,
    UnsupportedKind,
    ExtractionStatus,
)

logger = logging.getLogger("app.documents.docx_parser")

_WORD_DRAWING_NS = (
    "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
)
_INLINE_TAG = f"{_WORD_DRAWING_NS}inline"

# Object/OLE embed tags live in a different namespace - checked broadly
# via localname to avoid a second namespace-map maintenance burden.
_OLE_OBJECT_LOCALNAMES = {"object", "OLEObject"}


def _is_heading(paragraph: Paragraph) -> bool:
    # Confirmed: python-docx exposes the applied style name directly;
    # built-in heading styles are named "Heading 1".."Heading 9".
    style_name = paragraph.style.name if paragraph.style else ""
    return style_name.lower().startswith("heading")


def _extract_images_in_paragraph(
    paragraph: Paragraph,
    doc: DocxDocument,
    paragraph_index: int,
) -> list[UnsupportedItem]:
    """Finds inline images inside this paragraph's XML and resolves
    each to its raw bytes via the document part's relationship map.

    Confirmed API (python-docx 1.2.0): doc.part.related_parts is a
    dict keyed by relationship id -> Part. There is no
    related_part(rId) method on DocumentPart in this version.
    """

    items: list[UnsupportedItem] = []
    inline_elements = paragraph._p.findall(f".//{_INLINE_TAG}")

    for inline in inline_elements:
        try:
            blip = inline.graphic.graphicData.pic.blipFill.blip
            r_id = blip.embed

            if r_id is None:
                logger.warning(
                    "Inline image in paragraph %d has no embed rId - skipping",
                    paragraph_index,
                )
                continue

            image_part = doc.part.related_parts.get(r_id)

            if image_part is None:
                logger.warning(
                    "Could not resolve image part for rId=%s in paragraph %d",
                    r_id,
                    paragraph_index,
                )
                continue

            items.append(
                UnsupportedItem(
                    kind=UnsupportedKind.IMAGE,
                    location=Location(paragraph_index=paragraph_index),
                    note=f"content_type={image_part.content_type}",
                    raw_bytes=image_part.image.blob,
                    extraction_status=ExtractionStatus.PENDING,
                )
            )

        except AttributeError:
            # Malformed or unexpected drawing XML shape - degrade to
            # a flagged-without-bytes item rather than crash the parse.
            logger.warning(
                "Unexpected inline image structure in paragraph %d - "
                "flagging without extraction",
                paragraph_index,
                exc_info=True,
            )
            items.append(
                UnsupportedItem(
                    kind=UnsupportedKind.IMAGE,
                    location=Location(paragraph_index=paragraph_index),
                    note="unparseable inline image structure",
                    extraction_status=ExtractionStatus.FAILED,
                )
            )

    return items


def _has_ole_object(paragraph: Paragraph) -> bool:
    for elem in paragraph._p.iter():
        tag_localname = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag_localname in _OLE_OBJECT_LOCALNAMES:
            return True
    return False


def parse_docx(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Parses a .docx file's readable content into a ParsedDocument.

    File-size validation is the DISPATCHER's responsibility (single
    source of truth against settings.MAX_FILE_SIZE_MB) - this
    function assumes it's already been called with an acceptable
    size and focuses purely on content extraction.
    """

    doc = DocxDocument(io.BytesIO(file_bytes))
    parsed = ParsedDocument(source_filename=filename, file_type="docx")

    for idx, paragraph in enumerate(doc.paragraphs):
        text = paragraph.text.strip()

        if text:
            kind = (
                ContentKind.HEADING
                if _is_heading(paragraph)
                else ContentKind.PARAGRAPH
            )
            parsed.blocks.append(
                ContentBlock(
                    text=text,
                    kind=kind,
                    location=Location(paragraph_index=idx),
                )
            )

        parsed.unsupported_items.extend(
            _extract_images_in_paragraph(paragraph, doc, idx)
        )

        if _has_ole_object(paragraph):
            parsed.unsupported_items.append(
                UnsupportedItem(
                    kind=UnsupportedKind.EMBEDDED_OBJECT,
                    location=Location(paragraph_index=idx),
                    note="OLE-embedded object - no extraction path in current scope",
                    extraction_status=ExtractionStatus.NOT_APPLICABLE,
                )
            )

    for table_idx, table in enumerate(doc.tables):
        for row_idx, row in enumerate(table.rows):
            for col_idx, cell in enumerate(row.cells):
                cell_text = cell.text.strip()
                if cell_text:
                    parsed.blocks.append(
                        ContentBlock(
                            text=cell_text,
                            kind=ContentKind.TABLE_CELL,
                            location=Location(
                                table_index=table_idx,
                                row_index=row_idx,
                                column_index=col_idx,
                            ),
                        )
                    )

    logger.info(
        "Parsed %s: %d text blocks, %d unsupported items (%.0f chars)",
        filename,
        len(parsed.blocks),
        len(parsed.unsupported_items),
        parsed.total_char_count,
    )

    return parsed