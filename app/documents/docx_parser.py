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

EXPLICITLY OUT OF SCOPE, not silently missing: headers, footers,
footnotes/endnotes, and textboxes live in separate document parts
that doc.paragraphs/doc.tables never touch - none of this content
is reviewed OR flagged as unsupported; it's simply not visited at
all. Stated here explicitly rather than left implicit, since
"readable content" could otherwise be assumed to mean everything in
the file. Revisit if review coverage for these sections becomes a
real requirement - each is a genuinely separate python-docx access
path (doc.sections[i].header/.footer, etc.), not a small extension
of the current paragraph walk.
"""

from __future__ import annotations

import io
import logging

from docx import Document as DocxDocument
from docx.image.exceptions import (
    InvalidImageStreamError,
    UnexpectedEndOfFileError,
    UnrecognizedImageError,
)
from docx.oxml.ns import nsmap, qn
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
_ANCHOR_TAG = f"{_WORD_DRAWING_NS}anchor"
_GRAPHIC_DATA_TAG = (
    "{http://schemas.openxmlformats.org/drawingml/2006/main}graphicData"
)

# Sourced from python-docx's OWN nsmap rather than hardcoded strings -
# these values were verified to already be correct by direct
# comparison, but importing from the library's source of truth
# prevents any future drift, per review feedback.
_PICTURE_URI = nsmap["pic"]
_CHART_URI = nsmap["c"]
_DIAGRAM_URI = nsmap["dgm"]

# Object/OLE embed tags live in a different namespace - checked broadly
# via localname to avoid a second namespace-map maintenance burden.
_OLE_OBJECT_LOCALNAMES = {"object", "OLEObject"}


def _is_heading(paragraph: Paragraph) -> bool:
    # Confirmed: python-docx exposes the applied style name directly;
    # built-in heading styles are named "Heading 1".."Heading 9".
    style_name = paragraph.style.name if paragraph.style else ""
    return style_name.lower().startswith("heading")


def _extract_picture(
    graphic_el,
    doc: DocxDocument,
    paragraph_index: int,
) -> UnsupportedItem:
    """Resolves an inline PICTURE to its raw bytes via the document
    part's relationship map.

    Confirmed API (python-docx 1.2.0): doc.part.related_parts is a
    dict keyed by relationship id -> Part. There is no
    related_part(rId) method on DocumentPart in this version.

    Handles two real cases beyond the straightforward embedded-raster
    path, both confirmed rather than assumed:
    - LINKED (not embedded) pictures use r:link instead of r:embed -
      blip.embed is None for these, which previously got misreported
      as a generic parse failure. Now correctly identified and
      flagged as linked rather than broken.
    - EMF/WMF and other vector/unusual image formats: image_part.
      image.blob raises UnrecognizedImageError/InvalidImageStreamError
      /UnexpectedEndOfFileError from docx.image.exceptions - confirmed
      these are plain Exception subclasses, NOT AttributeError, so a
      bare `except AttributeError` does not catch them and the whole
      parse would crash on a real, common case (e.g. a pasted Excel
      chart or Visio object saved as EMF). Falls back to the part's
      raw .blob (bytes without python-docx's own format parsing/
      validation) rather than crash.
    """

    # Navigate via raw XML find(), NOT the .graphic convenience
    # attribute - confirmed by testing that CT_Anchor (floating/
    # wrapped images) does not expose .graphic the way CT_Inline
    # does (AttributeError: 'CT_Anchor' object has no attribute
    # 'graphic'). This approach works identically for both.
    a_ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    blip = graphic_el.find(f".//{a_ns}blip")

    if blip is None:
        logger.warning(
            "Graphic in paragraph %d has no blip element - flagging "
            "without extraction",
            paragraph_index,
        )
        return UnsupportedItem(
            kind=UnsupportedKind.IMAGE,
            location=Location(paragraph_index=paragraph_index),
            note="unparseable inline picture structure",
            extraction_status=ExtractionStatus.FAILED,
        )

    r_id = blip.get(qn("r:embed"))

    if r_id is None:
        link_r_id = blip.get(qn("r:link"))
        if link_r_id is not None:
            return UnsupportedItem(
                kind=UnsupportedKind.IMAGE,
                location=Location(paragraph_index=paragraph_index),
                note="linked (not embedded) image - no local bytes to extract",
                extraction_status=ExtractionStatus.NOT_APPLICABLE,
            )
        logger.warning(
            "Inline picture in paragraph %d has neither r:embed nor "
            "r:link - flagging without extraction",
            paragraph_index,
        )
        return UnsupportedItem(
            kind=UnsupportedKind.IMAGE,
            location=Location(paragraph_index=paragraph_index),
            note="unparseable inline picture structure",
            extraction_status=ExtractionStatus.FAILED,
        )

    image_part = doc.part.related_parts.get(r_id)

    if image_part is None:
        logger.warning(
            "Could not resolve part for rId=%s in paragraph %d",
            r_id,
            paragraph_index,
        )
        return UnsupportedItem(
            kind=UnsupportedKind.IMAGE,
            location=Location(paragraph_index=paragraph_index),
            note=f"could not resolve part for rId={r_id}",
            extraction_status=ExtractionStatus.FAILED,
        )

    try:
        raw_bytes = image_part.image.blob
        content_type = image_part.content_type
    except (
        UnrecognizedImageError,
        InvalidImageStreamError,
        UnexpectedEndOfFileError,
    ):
        # python-docx's own image-format parsing failed (EMF/WMF and
        # similar are common real-world triggers) - fall back to the
        # part's raw bytes, which exist regardless of whether python-
        # docx can parse the format's header.
        logger.info(
            "Image in paragraph %d is in a format python-docx can't "
            "parse (likely EMF/WMF) - using raw part bytes instead",
            paragraph_index,
        )
        raw_bytes = image_part.blob
        content_type = getattr(image_part, "content_type", "unknown")

    return UnsupportedItem(
        kind=UnsupportedKind.IMAGE,
        location=Location(paragraph_index=paragraph_index),
        note=f"content_type={content_type}",
        raw_bytes=raw_bytes,
        extraction_status=ExtractionStatus.PENDING,
    )


def _extract_inline_graphics(
    paragraph: Paragraph,
    doc: DocxDocument,
    paragraph_index: int,
) -> list[UnsupportedItem]:
    """Finds every graphic (picture, chart, or SmartArt) inside this
    paragraph - both INLINE (wp:inline, flows with text) and FLOATING/
    WRAPPED (wp:anchor, positioned with text wrap) - and routes each
    to the right handling by inspecting graphicData's uri. Searching
    only wp:inline was a real, silent coverage gap: any image with
    text wrapping applied (a common real-world formatting choice, not
    an edge case) uses wp:anchor instead and was previously invisible
    to this parser entirely - not flagged, not extracted, just absent.

    Charts/SmartArt in Word have no python-docx API support at all
    (confirmed: no docx.chart module exists), so - matching
    pptx_parser's approach - they're flagged only, never label-
    extracted the way PowerPoint charts are; that PPT-specific
    capability has no Word equivalent with the current library."""

    items: list[UnsupportedItem] = []
    graphic_elements = paragraph._p.findall(f".//{_INLINE_TAG}") + paragraph._p.findall(
        f".//{_ANCHOR_TAG}"
    )

    for graphic_el in graphic_elements:
        graphic_data = graphic_el.find(f".//{_GRAPHIC_DATA_TAG}")
        uri = graphic_data.get("uri", "") if graphic_data is not None else ""

        if uri == _PICTURE_URI:
            items.append(_extract_picture(graphic_el, doc, paragraph_index))

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
                "Graphic in paragraph %d has unrecognized graphicData "
                "uri=%r - flagging as unsupported without extraction",
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
                cell_location = Location(
                    table_index=table_idx,
                    row_index=row_idx,
                    column_index=col_idx,
                )

                for cell_paragraph in cell.paragraphs:
                    cell_text = cell_paragraph.text.strip()
                    if cell_text:
                        parsed.blocks.append(
                            ContentBlock(
                                text=cell_text,
                                kind=ContentKind.TABLE_CELL,
                                location=cell_location,
                            )
                        )

                    # Real gap, now fixed: table cells can contain
                    # images/charts/SmartArt just like body paragraphs
                    # can - doc.tables only exposed cell.text
                    # previously, silently dropping any graphic
                    # embedded inside a cell.
                    graphics = _extract_inline_graphics(
                        cell_paragraph, doc, table_idx
                    )
                    for item in graphics:
                        item.location = cell_location
                    parsed.unsupported_items.extend(graphics)

    logger.info(
        "Parsed %s: %d text blocks, %d unsupported items (%.0f chars)",
        filename,
        len(parsed.blocks),
        len(parsed.unsupported_items),
        parsed.total_char_count,
    )

    return parsed