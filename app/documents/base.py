"""
Common intermediate representation every format-specific parser
produces. Downstream (rules engine, chunking) works against this,
never against docx/pptx/openpyxl/pymupdf objects directly - keeps
the review engine format-agnostic, per the architecture's module
boundary principle.

Deliberately plain dataclasses, not Pydantic: these are internal,
high-volume, hot-path objects created in bulk by our own trusted
parser code during a single review (a 100MB document can produce
many thousands of ContentBlock instances) - Pydantic's per-instance
validation overhead buys nothing here since nothing is validating
untrusted input at this layer. If a ParsedDocument ever needs to
cross a real boundary (API response, Mongo storage), add a thin
Pydantic model as a converter at that specific boundary rather than
changing this type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ContentKind(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE_CELL = "table_cell"
    SLIDE_TEXT = "slide_text"
    SLIDE_NOTES = "slide_notes"
    SPREADSHEET_CELL = "spreadsheet_cell"
    PAGE_TEXT = "page_text"
    IMAGE_TEXT = "image_text"
    # Text pulled from an embedded image via a vision-capable model
    # call. Kept distinct from PARAGRAPH so findings/output can
    # attribute provenance ("extracted from an image") rather than
    # implying it was directly authored text.
    CHART_LABEL = "chart_label"
    # Chart title/axis/legend text - read directly via the chart's
    # own data API, NOT a vision call. The chart's underlying
    # DATA/semantics remain unreviewed - see UnsupportedKind.CHART.


class UnsupportedKind(str, Enum):
    IMAGE = "image"
    CHART = "chart"
    SMARTART = "smartart"
    EMBEDDED_OBJECT = "embedded_object"
    SCANNED_PAGE = "scanned_page"


class ExtractionStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class Location:
    """Only the fields relevant to the source format/content-kind are
    populated; the rest stay None. `display()` is what findings/UI
    show.

    table_index/row_index/column_index exist SEPARATELY from
    paragraph_index (not reused) - a Word table cell and a Word
    paragraph can otherwise land on the same index and collide in
    findings output. table_index also combines WITH slide_number for
    PowerPoint tables (a slide can contain multiple tables) - both
    collisions were caught during parser smoke testing, not
    theoretical."""

    page_number: int | None = None
    slide_number: int | None = None
    paragraph_index: int | None = None
    sheet_name: str | None = None
    cell_reference: str | None = None
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None

    def display(self) -> str:
        if self.sheet_name is not None and self.cell_reference is not None:
            return f"{self.sheet_name}!{self.cell_reference}"

        prefix_parts: list[str] = []
        if self.slide_number is not None:
            prefix_parts.append(f"Slide {self.slide_number}")
        if self.page_number is not None:
            prefix_parts.append(f"Page {self.page_number}")

        if self.table_index is not None:
            table_part = f"Table {self.table_index}"
            if self.row_index is not None and self.column_index is not None:
                table_part += f", Row {self.row_index}, Col {self.column_index}"
            prefix_parts.append(table_part)
        elif self.paragraph_index is not None:
            prefix_parts.append(f"Paragraph {self.paragraph_index}")

        if prefix_parts:
            return ", ".join(prefix_parts)

        return "Unknown location"


@dataclass
class ContentBlock:
    text: str
    kind: ContentKind
    location: Location
    extraction_method: str | None = None
    # None for directly-parsed text. "vision_ocr" (or similar) when
    # this block came from ContentKind.IMAGE_TEXT extraction.


@dataclass
class UnsupportedItem:
    kind: UnsupportedKind
    location: Location
    note: str = ""
    raw_bytes: bytes | None = None
    # Populated ONLY for kinds where extraction is plausible (IMAGE).
    # Short-lived handoff to the extraction step, not long-term
    # storage - real memory concern at 100MB scale with many images.
    extraction_status: ExtractionStatus = ExtractionStatus.NOT_ATTEMPTED


@dataclass
class ParsedDocument:
    source_filename: str
    file_type: str  # "docx" | "pptx" | "xlsx" | "pdf"
    blocks: list[ContentBlock] = field(default_factory=list)
    unsupported_items: list[UnsupportedItem] = field(default_factory=list)

    @property
    def total_char_count(self) -> int:
        """Real reviewable text volume - the metric that actually
        drives processing cost, independent of raw file size."""
        return sum(len(b.text) for b in self.blocks)


class UnsupportedFileTypeError(ValueError):
    pass


class FileTooLargeError(ValueError):
    def __init__(self, size_mb: float, max_mb: int):
        self.size_mb = size_mb
        self.max_mb = max_mb
        super().__init__(
            f"File is {size_mb:.1f}MB, exceeds limit of {max_mb}MB"
        )