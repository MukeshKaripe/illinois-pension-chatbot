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


def test_same_records_as_ilga_except_the_known_difference(client_text: str) -> None:
    # ILGA's .txt marks nested list items "* (a)"; the client's document indents
    # them instead. The converter maps that indentation to ILGA's marker, so both
    # renderings nest the same items inside the same parents — no exceptions
    # beyond 8-230.1.
    def ids(sections):  # type: ignore[no-untyped-def]
        return {r.id for r in chunk_sections(sections) if r.section != KNOWN_DIFFERENCE}

    assert ids(parse_statute_text(client_text, 8)) == ids(parse_statute_file(str(TXT), 8))


def test_record_counts_differ_only_by_ilgas_second_text_of_8_230_1(client_text: str) -> None:
    # Measured 2026-09-29: 366 against 367. Before indentation was read, the
    # client's document gave 381 — nested items flattened into subsections.
    assert len(chunk_sections(parse_statute_text(client_text, 8))) == 366
    assert len(chunk_sections(parse_statute_file(str(TXT), 8))) == 367


def test_a_list_inside_a_sentence_is_not_a_subsection(client_text: str) -> None:
    # "(d) ... for which he received (i) a disability benefit ..., or (ii) ...":
    # the document wraps "(i)" onto its own line.
    section = next(s for s in parse_statute_text(client_text, 8) if s.section_id == "8-226")
    assert "(i)" not in [x.subsection_id for x in section.subsections]


def test_every_client_word_is_in_the_ilga_text_in_order(client_text: str) -> None:
    client_words = re.findall(r"\S+", normalise(client_text))
    ilga_words = iter(re.findall(r"\S+", normalise(TXT.read_text(encoding="utf-8-sig"))))
    missing = [w for w in client_words if not any(w == x for x in ilga_words)]
    assert missing == []
