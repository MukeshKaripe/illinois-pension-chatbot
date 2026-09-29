"""The sections another Article's text cites are derived from that text, never typed by hand."""

from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.ingestion.parser import ParsedSection, parse_statute_file
from pension_chat.ingestion.selection import one_hop_references, other_act_references

ARTICLE_8 = Path(__file__).resolve().parents[1] / "data" / "statutes" / "article_8.txt"


def _section(section_id: str, refs: list[str], text: str = "t") -> ParsedSection:
    return ParsedSection(article_number=int(section_id.split("-")[0]), section_id=section_id,
                         section_title="t", full_text=text, referenced_sections=refs)


def test_references_outside_own_articles_are_grouped_by_article() -> None:
    sections = [
        _section("8-101", ["8-102", "1-160", "20-122"]),
        _section("8-102", ["1-160", "9-121.1", "1-113"]),
    ]
    assert one_hop_references(sections, {8}) == {
        1: {"1-160", "1-113"},
        20: {"20-122"},
        9: {"9-121.1"},
    }


def test_references_into_an_own_article_are_excluded() -> None:
    assert one_hop_references([_section("8-101", ["1-160"])], {8, 1}) == {}


@pytest.mark.skipif(not ARTICLE_8.exists(), reason="ILGA Article 8 not on disk (data/ is never committed)")
def test_article_8_cites_the_sections_the_scope_names() -> None:
    # The list in the scope document ("Statute corpus for the MEABF focus").
    refs = one_hop_references(parse_statute_file(str(ARTICLE_8), 8), {8})
    assert refs == {
        1: {"1-103.1", "1-109", "1-113", "1-160"},
        2: {"2-117.1"},
        5: {"5-233.1", "5-234"},
        7: {"7-139"},
        9: {"9-121.1", "9-121.11", "9-128.1"},
        12: {"12-127.1"},
        14: {"14-108.2a", "14-108.2b"},
        18: {"18-112"},
        19: {"19-101"},
        20: {"20-122"},
    }
    # "Section 3-101 of the Code of Civil Procedure", "Section 21-30 of the
    # Property Tax Code", "Section 34-60.1 of the School Code": same numbers,
    # other Acts. 40 ILCS 5/3-101 is the police fund, not what Article 8 cites.
    assert other_act_references(parse_statute_file(str(ARTICLE_8), 8), {8}) == {"3-101", "21-30", "34-60.1"}


def test_a_citation_to_another_act_is_not_a_pension_code_reference() -> None:
    sec = _section("8-101", ["3-101"], "as defined in Section 3-101 of the Code of Civil Procedure.")
    assert one_hop_references([sec], {8}) == {}
    assert other_act_references([sec], {8}) == {"3-101"}


def test_this_code_and_the_illinois_pension_code_are_the_pension_code() -> None:
    sections = [
        _section("8-101", ["5-234"], "Notwithstanding Section 5-234 of this Code, an officer may transfer."),
        _section("8-102", ["1-160"], "as provided in Section 1-160 of the Illinois Pension Code."),
    ]
    assert one_hop_references(sections, {8}) == {5: {"5-234"}, 1: {"1-160"}}
    assert other_act_references(sections, {8}) == set()


def test_one_plain_citation_is_enough_to_make_it_the_pension_code() -> None:
    sec = _section("8-101", ["9-101"], "See Section 9-101 of the School Code; and see Section 9-101.")
    assert one_hop_references([sec], {8}) == {9: {"9-101"}}


def test_a_longer_id_does_not_count_as_a_citation_of_the_shorter_one() -> None:
    # "9-121.11" must not be read as an occurrence of "9-121.1".
    sec = _section("8-101", ["9-121.1"],
                   "Section 9-121.1 applies. Section 9-121.11 of the School Code does not.")
    assert one_hop_references([sec], {8}) == {9: {"9-121.1"}}
