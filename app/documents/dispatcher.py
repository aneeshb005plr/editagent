"""
app/documents/dispatcher.py

Single entry point for parsing any supported document format. Routes
by file extension to the correct format-specific parser and enforces
the file-size ceiling in ONE place, so no individual parser needs to
duplicate that check - matches what every parser's docstring already
assumes ("File-size validation is the DISPATCHER's responsibility").

Size validation here is a SECOND line of defense, not the primary
one - by the time file_bytes exists in memory, the file has already
been fully read into it. The real, cheap rejection point is the API
layer (checking Content-Length / a streaming size check before
reading the full request body) - not yet built; flagged as a
required companion to this dispatcher when the upload route is
implemented, not something this module can substitute for.

_PARSERS below is the actual source of truth for what's supported -
config.SUPPORTED_FILE_EXTENSIONS should be validated against
supported_extensions() (or generated from it) rather than maintained
as a second, independent list that can silently drift from what this
dispatcher can really parse.
"""

from __future__ import annotations

import logging
from typing import Callable

from app.documents.base import (
    FileTooLargeError,
    ParsedDocument,
    UnsupportedFileTypeError,
)
from app.documents.docx_parser import parse_docx
from app.documents.pdf_parser import parse_pdf
from app.documents.pptx_parser import parse_pptx
from app.documents.xlsx_parser import parse_xlsx

logger = logging.getLogger("app.documents.dispatcher")

_PARSERS: dict[str, Callable[[bytes, str], ParsedDocument]] = {
    ".docx": parse_docx,
    ".pptx": parse_pptx,
    ".xlsx": parse_xlsx,
    ".pdf": parse_pdf,
}


def _extension_of(filename: str) -> str:
    idx = filename.rfind(".")
    if idx == -1:
        return ""
    return filename[idx:].lower()


def parse_document(
    file_bytes: bytes,
    filename: str,
    max_size_mb: int,
) -> ParsedDocument:
    """Parses any supported document into a ParsedDocument.

    Raises:
        FileTooLargeError: file_bytes exceeds max_size_mb.
        UnsupportedFileTypeError: extension has no registered parser.
    """

    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > max_size_mb:
        raise FileTooLargeError(size_mb=size_mb, max_mb=max_size_mb)

    ext = _extension_of(filename)
    parser = _PARSERS.get(ext)

    if parser is None:
        raise UnsupportedFileTypeError(
            f"'{ext or filename}' is not a supported file type. "
            f"Supported: {', '.join(sorted(_PARSERS.keys()))}"
        )

    logger.info(
        "Dispatching %s (%.1fMB) to parser for '%s'",
        filename,
        size_mb,
        ext,
    )

    return parser(file_bytes, filename)


def supported_extensions() -> list[str]:
    """Extensions this dispatcher can actually parse - use this as
    the source of truth wherever the API layer needs to advertise or
    pre-validate supported types, rather than hardcoding a second
    list that can drift from what's really registered here."""
    return sorted(_PARSERS.keys())