"""
app/documents/xlsx_parser.py

Parses .xlsx files into the common ParsedDocument representation
(app/documents/base.py). Built and verified against openpyxl 3.1.5.

SCOPE, as confirmed in discovery: every tab ("multiple pages" = every
sheet) is reviewed; only STRING-TYPED cells are treated as reviewable
text - formulas and numeric values have nothing for a grammar/tone/
risk-language check to act on. openpyxl's cell.data_type == 's'
directly identifies string cells even in read_only mode - confirmed
against a real fixture (data_only=False), NOT filtering by checking
`isinstance(value, str)`, because a formula's raw value under
data_only=False is itself a string like "=A2*2" and would be
wrongly treated as prose without the data_type check.

read_only=True used throughout - confirmed memory-efficient mode
per prior-project precedent; NOT yet load-tested at real large
multi-tab workbook scale.

KNOWN GAP, not yet handled: charts/images embedded in a workbook.
openpyxl's read-only mode does not expose chart/image objects the
way the writable mode does - if EditEdge needs to flag embedded
charts/images in Excel the way it does for Word/PowerPoint, this
needs a second, non-read-only pass or a different library approach.
Flagging explicitly rather than silently leaving it unhandled -
confirm whether this matters for MVP before treating it as done.
"""

from __future__ import annotations

import logging

import openpyxl
from openpyxl.cell.read_only import EmptyCell
from openpyxl.utils import get_column_letter

from app.documents.base import (
    ContentBlock,
    ContentKind,
    Location,
    ParsedDocument,
)

logger = logging.getLogger("app.documents.xlsx_parser")


def parse_xlsx(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Parses an .xlsx file's readable content into a ParsedDocument.

    File-size validation is the DISPATCHER's responsibility (single
    source of truth against settings.MAX_FILE_SIZE_MB) - this
    function assumes it's already been called with an acceptable
    size and focuses purely on content extraction.
    """

    import io

    workbook = openpyxl.load_workbook(
        io.BytesIO(file_bytes),
        read_only=True,
        data_only=False,
        # data_only=False deliberately - see module docstring on why
        # we need the data_type check rather than raw values.
    )

    parsed = ParsedDocument(source_filename=filename, file_type="xlsx")

    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                if isinstance(cell, EmptyCell):
                    continue

                if cell.data_type != "s":
                    # Formula ('f') or numeric ('n') - not reviewable
                    # prose, skip per confirmed scope.
                    continue

                text = (cell.value or "").strip()
                if not text:
                    continue

                col_letter = get_column_letter(cell.column)
                cell_ref = f"{col_letter}{cell.row}"

                parsed.blocks.append(
                    ContentBlock(
                        text=text,
                        kind=ContentKind.SPREADSHEET_CELL,
                        location=Location(
                            sheet_name=worksheet.title,
                            cell_reference=cell_ref,
                        ),
                    )
                )

    workbook.close()

    logger.info(
        "Parsed %s: %d text blocks across %d sheets (%.0f chars)",
        filename,
        len(parsed.blocks),
        len(workbook.sheetnames),
        parsed.total_char_count,
    )

    return parsed