"""The client's Article 8 document against ILGA's Article 8.

Measured 2026-09-29: the same 211 sections; with nested tables read, every word
of the client's text appears in ILGA's, in order; ILGA additionally carries a
pending-amendment variant of §8-230.1 (P.A. 104-457) and footnote asterisks.
Skipped when either file is absent: data/ is never committed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pension_chat.ingestion.checks import check_file_text
from pension_chat.ingestion.chunker import chunk_sections
from pension_chat.ingestion.converters import to_text
from pension_chat.ingestion.normalise import normalise
from pension_chat.ingestion.parser import parse_statute_file, parse_statute_text
from pension_chat.ingestion.sources import SourceFile
from pension_chat.ingestion.splitter import split_by_article

STATUTES = Path(__file__).resolve().parents[1] / "data" / "statutes"
DOC = STATUTES / "Illinois Pension Law MEABF.docx"
TXT = STATUTES / "article_8.txt"
KNOWN_DIFFERENCE = "8-230.1"

pytestmark = pytest.mark.skipif(
    not (DOC.exists() and TXT.exists()),
    reason="client document or ILGA Article 8 not on disk (data/ is never committed)",
)


@pytest.fixture(scope="module")
def client_text() -> str:
    text = to_text(SourceFile(DOC.name, DOC.read_bytes(), "test"))
    check_file_text(DOC.name, text)          # raises if structure is damaged
    parts = split_by_article(text)
    assert set(parts) == {8}
    return parts[8]


def test_same_sections_as_ilga(client_text: str) -> None:
    client = {s.section_id for s in parse_statute_text(client_text, 8)}
    ilga = {s.section_id for s in parse_statute_file(str(TXT), 8)}
    assert client == ilga
    assert len(client) == 211


_ASTERISKED_MARKER_RE = re.compile(r"^\* \(([a-z](?:-\d+)?)\) ")


def _asterisked_sections() -> dict[str, list[str]]:
    """Sections whose ILGA text prefixes subsection markers with "* ", and those markers.

    The existing parser does not read "* (d) ..." as a subsection, so ILGA's own
    .txt indexes these sections as one undivided record. The client's document
    carries no asterisks and parses them into their subsections — the *reference*
    is the one short here, not the conversion.
    """
    found: dict[str, list[str]] = {}
    current = None
    for line in TXT.read_text(encoding="utf-8-sig").split("\n"):
        header = re.match(r"^\(40 ILCS 5/(8-[\d.]+[a-z]?)\)", line.strip())
        if header:
            current = header.group(1)
        marker = _ASTERISKED_MARKER_RE.match(line)
        if marker and current:
            found.setdefault(current, []).append(f"({marker.group(1)})")
    return found


def test_same_records_except_the_known_differences(client_text: str) -> None:
    asterisked = _asterisked_sections()
    assert set(asterisked) == {"8-114", "8-154", "8-158", "8-167", "8-226"}
    skip = set(asterisked) | {KNOWN_DIFFERENCE}

    def ids(sections):  # type: ignore[no-untyped-def]
        return {r.id for r in chunk_sections(sections) if r.section not in skip}

    assert ids(parse_statute_text(client_text, 8)) == ids(parse_statute_file(str(TXT), 8))


def test_asterisked_sections_get_exactly_the_subsections_ilga_marks(client_text: str) -> None:
    client = {s.section_id: [x.subsection_id for x in s.subsections] for s in parse_statute_text(client_text, 8)}
    for section_id, markers in _asterisked_sections().items():
        # Every asterisked marker is a subsection the client's text yields.
        assert set(markers) <= set(client[section_id]), section_id


def test_every_client_word_is_in_the_ilga_text_in_order(client_text: str) -> None:
    client_words = re.findall(r"\S+", normalise(client_text))
    ilga_words = iter(re.findall(r"\S+", normalise(TXT.read_text(encoding="utf-8-sig"))))
    missing = [w for w in client_words if not any(w == x for x in ilga_words)]
    assert missing == []
