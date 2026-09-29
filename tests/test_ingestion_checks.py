"""A conversion that damaged structure is refused, never repaired by guessing."""

from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.ingestion.checks import (
    FileRejected,
    check_file_text,
    duplicate_record_ids,
    header_title_mismatches,
    headers_not_at_line_start,
)
from pension_chat.ingestion.chunker import IndexRecord

STATUTE_DIR = Path(__file__).resolve().parents[1] / "data" / "statutes"


def test_a_header_lost_mid_line_is_reported() -> None:
    text = "Sec. 8-101. Creation of fund. (40 ILCS 5/8-102) Sec. 8-102. Terms."
    assert len(headers_not_at_line_start(text)) == 1


def test_an_article_heading_mid_line_is_not_a_section_header() -> None:
    assert headers_not_at_line_start("See (40 ILCS 5/Art. 8 heading) above.") == []


def test_header_followed_by_its_own_title_passes() -> None:
    text = "(40 ILCS 5/8-230.1)\n\n(Text of Section before amendment by P.A. 104-457)\nSec. 8-230.1. Right to contribute."
    assert header_title_mismatches(text) == []


def test_a_citation_in_body_text_is_not_a_lost_header() -> None:
    # Article 1's QILDRO order form cites the Code this way, mid-sentence.
    text = "I. The definitions of Section 1-119 of the Illinois Pension Code (40 ILCS 5/1-119) apply."
    assert headers_not_at_line_start(text) == []


def test_a_lost_header_followed_by_its_from_chapter_note_is_reported() -> None:
    text = "the end of one section. (40 ILCS 5/8-102) (from Ch. 108 1/2, par. 8-102)"
    assert len(headers_not_at_line_start(text)) == 1


@pytest.mark.parametrize("note", [
    "(This Section was added by P.A. 98-599, which has been held unconstitutional)",
    "(Section scheduled to be repealed on January 1, 2027)",
])
def test_ilga_editorial_notes_may_sit_between_header_and_title(note: str) -> None:
    assert header_title_mismatches(f"(40 ILCS 5/16-188)\n{note}\nSec. 16-188. Something.") == []


def test_a_title_missing_its_full_stop_still_matches() -> None:
    # ILGA prints "Sec. 11-125.9 Action by Fund..." without the period.
    assert header_title_mismatches("(40 ILCS 5/11-125.9)\nSec. 11-125.9 Action by Fund against third party.") == []


def test_header_followed_by_another_sections_title_fails() -> None:
    text = "(40 ILCS 5/8-101)\nSec. 8-102. Terms defined."
    assert header_title_mismatches(text) == [
        "§8-101: header not followed by 'Sec. 8-101.' (next line: 'Sec. 8-102. Terms defined.')"
    ]


def test_header_at_the_very_end_fails() -> None:
    assert header_title_mismatches("(40 ILCS 5/8-101)") == ["§8-101: header with no title after it"]


def test_check_file_text_names_the_file_and_the_problems() -> None:
    with pytest.raises(FileRejected, match=r"art8\.pdf.*8-101"):
        check_file_text("art8.pdf", "(40 ILCS 5/8-101)\nSec. 8-102. Wrong.")


def test_duplicate_record_ids_are_listed() -> None:
    records = [IndexRecord(id="8-101_a", content="x"), IndexRecord(id="8-101_a", content="y"),
               IndexRecord(id="8-101_b", content="z")]
    assert duplicate_record_ids(records) == ["8-101_a"]


_CORPUS = sorted(STATUTE_DIR.glob("article_*.txt"))


@pytest.mark.skipif(not _CORPUS, reason="ILGA corpus not on disk (data/ is never committed)")
@pytest.mark.parametrize("path", _CORPUS, ids=lambda p: p.name)
def test_every_ilga_file_passes_the_file_checks(path: Path) -> None:
    # The baseline the spec requires before checks 3 and 4 go live: a correct
    # file must never trip them.
    text = path.read_text(encoding="utf-8-sig")
    assert headers_not_at_line_start(text) == []
    assert header_title_mismatches(text) == []
