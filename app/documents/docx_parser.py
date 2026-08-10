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
_GRAPHIC_DATA_TAG = (
    "{http://schemas.openxmlformats.org/drawingml/2006/main}graphicData"
)

# Charts and SmartArt in Word use the SAME wp:inline wrapper as
# pictures - all three are only distinguishable by graphicData's uri
# attribute. Confirmed by inspecting real generated XML (a python-
# pptx chart's uri, reused here since Word/PowerPoint share the same
# OOXML drawing schema for this), not assumed. Getting this wrong
# previously meant charts/SmartArt fell into the picture-extraction
# path and failed there with a misleading "unparseable inline image
# structure" note instead of being correctly identified.
_PICTURE_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_DIAGRAM_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"

# Object/OLE embed tags live in a different namespace - checked broadly
# via localname to avoid a second namespace-map maintenance burden.
_OLE_OBJECT_LOCALNAMES = {"object", "OLEObject"}


def _is_heading(paragraph: Paragraph) -> bool:
    # Confirmed: python-docx exposes the applied style name directly;
    # built-in heading styles are named "Heading 1".."Heading 9".
    style_name = paragraph.style.name if paragraph.style else ""
    return style_name.lower().startswith("heading")


def _extract_picture(
    inline,
    doc: DocxDocument,
    paragraph_index: int,
) -> UnsupportedItem:
    """Resolves an inline PICTURE to its raw bytes via the document
    part's relationship map.

    Confirmed API (python-docx 1.2.0): doc.part.related_parts is a
    dict keyed by relationship id -> Part. There is no
    related_part(rId) method on DocumentPart in this version.
    """

    try:
        blip = inline.graphic.graphicData.pic.blipFill.blip
        r_id = blip.embed

        if r_id is None:
            raise AttributeError("blip has no embed rId")

        image_part = doc.part.related_parts.get(r_id)

        if image_part is None:
            raise AttributeError(f"could not resolve part for rId={r_id}")

        return UnsupportedItem(
            kind=UnsupportedKind.IMAGE,
            location=Location(paragraph_index=paragraph_index),
            note=f"content_type={image_part.content_type}",
            raw_bytes=image_part.image.blob,
            extraction_status=ExtractionStatus.PENDING,
        )

    except AttributeError:
        logger.warning(
            "Unexpected inline picture structure in paragraph %d - "
            "flagging without extraction",
            paragraph_index,
            exc_info=True,
        )
        return UnsupportedItem(
            kind=UnsupportedKind.IMAGE,
            location=Location(paragraph_index=paragraph_index),
            note="unparseable inline picture structure",
            extraction_status=ExtractionStatus.FAILED,
        )


def _extract_inline_graphics(
    paragraph: Paragraph,
    doc: DocxDocument,
    paragraph_index: int,
) -> list[UnsupportedItem]:
    """Finds every inline graphic (picture, chart, or SmartArt) inside
    this paragraph and routes each to the right handling by inspecting
    graphicData's uri - NOT by assuming every inline is a picture,
    which was the previous (incorrect) behavior. Charts/SmartArt in
    Word have no python-docx API support at all (confirmed: no
    docx.chart module exists), so - matching pptx_parser's approach -
    they're flagged only, never label-extracted the way PowerPoint
    charts are; that PPT-specific capability has no Word equivalent
    with the current library, worth knowing rather than assuming
    parity across formats."""

    items: list[UnsupportedItem] = []
    inline_elements = paragraph._p.findall(f".//{_INLINE_TAG}")

    for inline in inline_elements:
        graphic_data = inline.find(f".//{_GRAPHIC_DATA_TAG}")
        uri = graphic_data.get("uri", "") if graphic_data is not None else ""

        if uri == _PICTURE_URI:
            items.append(_extract_picture(inline, doc, paragraph_index))

        elif uri == _CHART_URI:
            items.append(
                UnsupportedItem(
                    kind=UnsupportedKind.CHART,
                    location=Location(paragraph_index=paragraph_index),
                    note=(
                        "chart data/visual layout not reviewed - "
                        "Word charts have no label-extraction path "
                        "(unlike PowerPoint) with the current library"
                    ),
                    extraction_status=ExtractionStatus.NOT_APPLICABLE,
                )
            )

        elif uri == _DIAGRAM_URI:
            items.append(
                UnsupportedItem(
                    kind=UnsupportedKind.SMARTART,
                    location=Location(paragraph_index=paragraph_index),
                    note="SmartArt diagram - structure/content not reviewed in current scope",
                    extraction_status=ExtractionStatus.NOT_APPLICABLE,
                )
            )

        else:
            logger.warning(
                "Inline graphic in paragraph %d has unrecognized "
                "graphicData uri=%r - flagging as unsupported without "
                "extraction",
                paragraph_index,
                uri,
            )
            items.append(
                UnsupportedItem(
                    kind=UnsupportedKind.EMBEDDED_OBJECT,
                    location=Location(paragraph_index=paragraph_index),
                    note=f"unrecognized graphic type (uri={uri or 'none'})",
                    extraction_status=ExtractionStatus.NOT_APPLICABLE,
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
            _extract_inline_graphics(paragraph, doc, idx)
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