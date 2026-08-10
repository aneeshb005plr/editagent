"""
app/documents/pptx_parser.py

Parses .pptx files into the common ParsedDocument representation
(app/documents/base.py). Built and verified against python-pptx 1.0.2
- API specifics confirmed against the real library (title-shape
detection via shape.is_placeholder + placeholder_format.type ==
PP_PLACEHOLDER.TITLE, image bytes via shape.image.blob, chart labels
via chart.chart_title / chart.series / chart.plots[0].categories -
all verified against a generated fixture, not assumed).

SMARTART DETECTION IS UNVERIFIED BY FIXTURE TEST: python-pptx has no
API to CREATE SmartArt diagrams, so no fixture could be built to
prove the detection path works end-to-end. Implemented per the
documented OOXML namespace for diagrams
(http://schemas.openxmlformats.org/drawingml/2006/diagram) - this
MUST be confirmed against a real SmartArt-containing deck during the
large-file spike before being trusted in production.

GROUPED SHAPES: recursed into (a group can contain pictures/tables/
text), one level of real-world complexity handled; deeply nested
groups are walked recursively with no depth limit - acceptable
for now, revisit only if a pathological deck causes problems.

NOT YET LOAD-TESTED AT 100MB.
"""

from __future__ import annotations

import logging

from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

# python-pptx's shape base class (pptx.shapes.base.BaseShape) is not
# re-exported from pptx.shapes' top level in 1.0.2 - confirmed by
# import error during smoke testing, not assumed. Using `Any` for
# these type hints rather than chasing the internal module path,
# since it's documentation-only here and adds no runtime behavior.
BaseShape = Any

from app.documents.base import (
    ContentBlock,
    ContentKind,
    ExtractionStatus,
    Location,
    ParsedDocument,
    UnsupportedItem,
    UnsupportedKind,
)

logger = logging.getLogger("app.documents.pptx_parser")

_DIAGRAM_NS_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"


def _is_title_shape(shape: BaseShape) -> bool:
    if not shape.is_placeholder:
        return False
    ph_type = shape.placeholder_format.type
    return ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)


def _is_smartart(shape: BaseShape) -> bool:
    """SmartArt is a graphicFrame whose graphicData uri points at the
    OOXML diagram namespace. python-pptx doesn't expose a has_smartart
    flag, so this checks the underlying XML directly - see module
    docstring re: unverified-by-fixture status."""

    if shape.shape_type != MSO_SHAPE_TYPE.CHART and hasattr(shape, "_element"):
        try:
            graphic_data = shape._element.find(
                ".//{http://schemas.openxmlformats.org/drawingml/2006/main}graphicData"
            )
            if graphic_data is not None:
                uri = graphic_data.get("uri", "")
                return uri == _DIAGRAM_NS_URI
        except AttributeError:
            return False
    return False


def _extract_chart_labels(shape: BaseShape, location: Location) -> list[ContentBlock]:
    """Chart title/series names/category labels are read directly via
    the chart's own data API - NOT a vision call. The chart's actual
    plotted DATA and visual layout remain unreviewed (flagged
    separately as UnsupportedKind.CHART) - see base.py docstring."""

    blocks: list[ContentBlock] = []
    chart = shape.chart

    if chart.has_title:
        title_text = chart.chart_title.text_frame.text.strip()
        if title_text:
            blocks.append(
                ContentBlock(
                    text=title_text,
                    kind=ContentKind.CHART_LABEL,
                    location=location,
                )
            )

    try:
        for series in chart.series:
            if series.name and series.name.strip():
                blocks.append(
                    ContentBlock(
                        text=series.name.strip(),
                        kind=ContentKind.CHART_LABEL,
                        location=location,
                    )
                )
        for category in chart.plots[0].categories:
            cat_text = str(category).strip()
            if cat_text:
                blocks.append(
                    ContentBlock(
                        text=cat_text,
                        kind=ContentKind.CHART_LABEL,
                        location=location,
                    )
                )
    except (IndexError, AttributeError):
        logger.warning(
            "Could not read series/category labels for chart at %s",
            location.display(),
            exc_info=True,
        )

    return blocks


def _process_shape(
    shape: BaseShape,
    slide_number: int,
    parsed: ParsedDocument,
    table_counter: list[int],
) -> None:
    """table_counter is a 1-item mutable list acting as a per-slide
    counter, passed by reference through the recursion (including
    into groups) so multiple tables on one slide get distinct
    table_index values instead of colliding on Row 0, Col 0 - same
    class of bug caught and fixed in docx_parser's table handling."""

    location = Location(slide_number=slide_number)

    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for sub_shape in shape.shapes:
            _process_shape(sub_shape, slide_number, parsed, table_counter)
        return

    if _is_smartart(shape):
        parsed.unsupported_items.append(
            UnsupportedItem(
                kind=UnsupportedKind.SMARTART,
                location=location,
                note="SmartArt diagram - structure/content not reviewed in current scope",
                extraction_status=ExtractionStatus.NOT_APPLICABLE,
            )
        )
        return

    if getattr(shape, "has_chart", False):
        parsed.blocks.extend(_extract_chart_labels(shape, location))
        parsed.unsupported_items.append(
            UnsupportedItem(
                kind=UnsupportedKind.CHART,
                location=location,
                note="chart data/visual layout not reviewed - labels extracted separately",
                extraction_status=ExtractionStatus.NOT_APPLICABLE,
            )
        )
        return

    if getattr(shape, "has_table", False):
        this_table_index = table_counter[0]
        table_counter[0] += 1
        for row_idx, row in enumerate(shape.table.rows):
            for col_idx, cell in enumerate(row.cells):
                cell_text = cell.text.strip()
                if cell_text:
                    parsed.blocks.append(
                        ContentBlock(
                            text=cell_text,
                            kind=ContentKind.TABLE_CELL,
                            location=Location(
                                slide_number=slide_number,
                                table_index=this_table_index,
                                row_index=row_idx,
                                column_index=col_idx,
                            ),
                        )
                    )
        return

    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        try:
            image = shape.image
            parsed.unsupported_items.append(
                UnsupportedItem(
                    kind=UnsupportedKind.IMAGE,
                    location=location,
                    note=f"content_type={image.content_type}",
                    raw_bytes=image.blob,
                    extraction_status=ExtractionStatus.PENDING,
                )
            )
        except (AttributeError, ValueError):
            # Linked (not embedded) images raise here in python-pptx -
            # flag without bytes rather than crash the parse.
            logger.warning(
                "Could not read image bytes for picture on slide %d "
                "(possibly a linked, not embedded, image)",
                slide_number,
                exc_info=True,
            )
            parsed.unsupported_items.append(
                UnsupportedItem(
                    kind=UnsupportedKind.IMAGE,
                    location=location,
                    note="image bytes unavailable (possibly linked, not embedded)",
                    extraction_status=ExtractionStatus.FAILED,
                )
            )
        return

    if shape.has_text_frame:
        text = shape.text_frame.text.strip()
        if text:
            kind = (
                ContentKind.HEADING
                if _is_title_shape(shape)
                else ContentKind.SLIDE_TEXT
            )
            parsed.blocks.append(
                ContentBlock(text=text, kind=kind, location=location)
            )
        return

    if shape.shape_type == MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT:
        parsed.unsupported_items.append(
            UnsupportedItem(
                kind=UnsupportedKind.EMBEDDED_OBJECT,
                location=location,
                note="OLE-embedded object - no extraction path in current scope",
                extraction_status=ExtractionStatus.NOT_APPLICABLE,
            )
        )


def parse_pptx(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Parses a .pptx file's readable content into a ParsedDocument.

    File-size validation is the DISPATCHER's responsibility (single
    source of truth against settings.MAX_FILE_SIZE_MB) - this
    function assumes it's already been called with an acceptable
    size and focuses purely on content extraction.
    """

    import io

    prs = Presentation(io.BytesIO(file_bytes))
    parsed = ParsedDocument(source_filename=filename, file_type="pptx")

    for slide_idx, slide in enumerate(prs.slides):
        slide_number = slide_idx + 1  # 1-indexed for user-facing display
        table_counter = [0]  # per-slide, reset each slide - see _process_shape docstring

        for shape in slide.shapes:
            _process_shape(shape, slide_number, parsed, table_counter)

        if slide.has_notes_slide:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()
            if notes_text:
                parsed.blocks.append(
                    ContentBlock(
                        text=notes_text,
                        kind=ContentKind.SLIDE_NOTES,
                        location=Location(slide_number=slide_number),
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