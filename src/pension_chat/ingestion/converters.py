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
from collections import Counter
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
        return _mirror_docx_nesting(normalise(_docx_text(file)))
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


#: A list marker at the start of a line: (a), (1), (ii), (a-5).
_LIST_MARKER_RE = re.compile(r"^\([a-z0-9]+(?:-\d+)?\)\s")


def _mirror_docx_nesting(text: str) -> str:
    """Carry a Word document's indentation into the convention ILGA's text uses.

    ILGA's .txt writes a nested list item "* (a) ...", and the parser, seeing the
    asterisk, keeps it inside its parent rather than opening a subsection. A
    Word rendering of the same statute has no asterisk; it indents the item
    deeper than the body instead (the client's Article 8: 4 spaces for the body,
    8 for nested items — 76 of 79 nested lines agree with ILGA's asterisks).
    So an item indented deeper than the document's usual body indent gets ILGA's
    marker. And a marker on an *unindented* line is a list inside a sentence that
    wrapped ("...for which he received" / "(i) a disability benefit ..., or (ii)
    ..."), so it is joined back onto the line it came from. With both, the
    client's Article 8 parses record for record like ILGA's (measured
    2026-09-29), where without them five sections flattened nested items into
    subsections and §8-226 gained a subsection "(i)" the statute does not have.
    """
    lines = text.split("\n")
    indents = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    if not indents:
        return text
    body = Counter(indents).most_common(1)[0][0]

    out: list[str] = []
    for line in lines:
        s = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if s and _LIST_MARKER_RE.match(s):
            if indent < body and any(o.strip() for o in out):
                last = max(i for i, o in enumerate(out) if o.strip())
                out[last] = out[last].rstrip() + " " + s
                continue
            if indent > body:
                line = " " * body + "* " + s
        out.append(line)
    return "\n".join(out)


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

    # A page without a text layer between pages that have one is text we cannot
    # see — and on a first ingestion nothing in the index would notice it gone.
    # Only blank pages before the first and after the last text page (a cover,
    # an end sheet) are allowed.
    has_text = [len("".join(page).strip()) >= 50 for page in pages]
    if not any(has_text):
        raise NoTextLayerError(
            f"{file.name}: none of its {len(pages)} pages has extractable text — "
            "it looks scanned and needs OCR, which this pipeline does not do"
        )
    first, last = has_text.index(True), len(has_text) - 1 - has_text[::-1].index(True)
    interior_blank = [i + 1 for i in range(first, last + 1) if not has_text[i]]
    if interior_blank:
        raise NoTextLayerError(
            f"{file.name}: page {', '.join(map(str, interior_blank))} has no extractable text "
            "between pages that do — likely scanned; OCR is not done here, so the file is refused"
        )
    return join_broken_lines("\n".join(remove_page_furniture(pages)))
