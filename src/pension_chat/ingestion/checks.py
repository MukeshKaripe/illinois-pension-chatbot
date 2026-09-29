"""Checks that turn a structurally damaged conversion into a refusal.

Uploading merges by key, so a lost section is never an error on its own — the
index is just quietly a section short. These checks make the damage loud.
"""

from __future__ import annotations

import re
from collections import Counter

from pension_chat.ingestion.chunker import IndexRecord
from pension_chat.ingestion.converters import IngestionError

#: Something shaped like a section header rather than a citation: the Code also
#: cites itself mid-sentence ("... Pension Code (40 ILCS 5/1-119) apply"), but a
#: header is followed by its "(from Ch." note, its "Sec." title, or a line end.
_HEADER_SHAPE_RE = re.compile(
    r"\(40 ILCS 5/\d+[A-Z]?-\d+(?:\.\d+)?[a-z]?\)(?=\s*(?:\(from Ch\.|Sec\.\s|$))"
)
_HEADER_START_RE = re.compile(r"^\(40 ILCS 5/(\d+[A-Z]?-\d+(?:\.\d+)?[a-z]?)\)")
#: The full stop after the id is optional: ILGA prints "Sec. 11-125.9 Action by".
_TITLE_RE = re.compile(r"^Sec\.\s+(\d+[A-Z]?-\d+(?:\.\d+)?[a-z]?)(?:\.|\s)")
#: Lines ILGA legitimately prints between a header and its "Sec." title,
#: measured across all 28 Article files.
_BETWEEN_HEADER_AND_TITLE_RE = re.compile(
    r"^(\(Text of Section .*\)|WITH(OUT)? the changes made by .*"
    r"|\(This Section was added by .*\)|\(Section scheduled to be repealed .*\))$",
    re.IGNORECASE,
)


class FileRejected(IngestionError):
    pass


def headers_not_at_line_start(text: str) -> list[str]:
    """A section header anywhere but the start of a line means a lost line break."""
    problems = []
    for number, line in enumerate(text.split("\n"), start=1):
        for match in _HEADER_SHAPE_RE.finditer(line):
            if line[: match.start()].strip():
                problems.append(f"line {number}: section header mid-line: {line.strip()[:80]!r}")
    return problems


def header_title_mismatches(text: str) -> list[str]:
    """Each header must be followed by its own 'Sec. N-M.' — otherwise text is out of order."""
    lines = text.split("\n")
    problems = []
    for i, line in enumerate(lines):
        header = _HEADER_START_RE.match(line.strip())
        if not header:
            continue
        section_id = header.group(1)
        for following in lines[i + 1:]:
            s = following.strip()
            if not s or _BETWEEN_HEADER_AND_TITLE_RE.match(s):
                continue
            title = _TITLE_RE.match(s)
            if not title or title.group(1) != section_id:
                problems.append(
                    f"§{section_id}: header not followed by 'Sec. {section_id}.' (next line: {s[:60]!r})"
                )
            break
        else:
            problems.append(f"§{section_id}: header with no title after it")
    return problems


def check_file_text(name: str, text: str) -> None:
    problems = headers_not_at_line_start(text) + header_title_mismatches(text)
    if problems:
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        raise FileRejected(f"{name}: " + "; ".join(problems[:5]) + more)


def duplicate_record_ids(records: list[IndexRecord]) -> list[str]:
    counts = Counter(record.id for record in records)
    return sorted(record_id for record_id, count in counts.items() if count > 1)
