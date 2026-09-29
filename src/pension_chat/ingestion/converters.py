"""Bytes in, ILGA-shaped plain text out — one function per file format.

Everything the parser needs is on the lines, so each converter's only job is to
emit one line per paragraph, in document order, and lose nothing. Losing text is
the failure that matters: the Word document the client supplied keeps Article 8
in 212 tables, 74 of them nested, and reading cells without recursing dropped
~3,300 words while every section header survived. So `.docx` carries a
completeness check, and `.pdf` refuses files with no text layer.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any

from pension_chat.ingestion.normalise import (
    join_broken_lines,
    normalise,
    remove_page_furniture,
    strip_markdown,
)
from pension_chat.ingestion.sources import SourceFile

logger = logging.getLogger("pension_chat.ingestion.converters")


class IngestionError(Exception):
    """A file or Article that must be refused, with a reason a person can act on."""


class UnsupportedFormatError(IngestionError):
    pass


class ConversionFailedError(IngestionError):
    pass


class NoTextLayerError(ConversionFailedError):
    pass


def to_text(file: SourceFile) -> str:
    """Convert one file to normalised ILGA-shaped text."""
    ext = Path(file.name).suffix.lower()
    if ext == ".txt":
        raw = _decode(file)
    elif ext == ".md":
        raw = strip_markdown(_decode(file))
    elif ext == ".docx":
        raw = _docx_text(file)
    elif ext == ".pdf":
        raw = _pdf_text(file)
    else:
        raise UnsupportedFormatError(f"{file.name}: no converter for '{ext or '(no extension)'}'")
    return normalise(raw)


def _decode(file: SourceFile) -> str:
    try:
        return file.data.decode("utf-8-sig")
    except UnicodeDecodeError:
        logger.warning("%s is not UTF-8; decoding as cp1252", file.name)
        return file.data.decode("cp1252")


# ── .docx ────────────────────────────────────────────────────────


def _docx_text(file: SourceFile) -> str:
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(io.BytesIO(file.data))
    lines: list[str] = []
    _walk(doc.element.body, doc, lines)
    text = "\n".join(lines)

    source_chars = sum(len(re.sub(r"\s", "", t.text or "")) for t in doc.element.body.iter(qn("w:t")))
    output_chars = len(re.sub(r"\s", "", text))
    if source_chars != output_chars:
        raise ConversionFailedError(
            f"{file.name}: the document holds {source_chars} characters of text but "
            f"{output_chars} were extracted — some text sits where the converter does not read"
        )
    return text


def _walk(container: Any, parent: Any, lines: list[str]) -> None:
    """Paragraphs and tables in document order, recursing into nested tables."""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in container.iterchildren():
        if child.tag == qn("w:p"):
            lines.extend(Paragraph(child, parent).text.split("\n"))
        elif child.tag == qn("w:tbl"):
            for row in Table(child, parent).rows:
                previous = None
                for cell in row.cells:
                    # A horizontally merged cell is returned once per grid column.
                    if cell._tc is previous:
                        continue
                    previous = cell._tc
                    _walk(cell._tc, cell, lines)


# ── .pdf ─────────────────────────────────────────────────────────


def _pdf_text(file: SourceFile) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(file.data)) as pdf:
        pages = [(page.extract_text() or "").split("\n") for page in pdf.pages]

    thin = sum(1 for page in pages if len("".join(page).strip()) < 50)
    if not pages or thin * 2 > len(pages):
        raise NoTextLayerError(
            f"{file.name}: {thin} of {len(pages)} pages have no extractable text — "
            "it looks scanned and needs OCR, which this pipeline does not do"
        )
    return join_broken_lines("\n".join(remove_page_furniture(pages)))
