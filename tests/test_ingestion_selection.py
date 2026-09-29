"""The sections another Article's text cites are derived from that text, never typed by hand."""

from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.ingestion.parser import ParsedSection, parse_statute_file
from pension_chat.ingestion.selection import one_hop_references

ARTICLE_8 = Path(__file__).resolve().parents[1] / "data" / "statutes" / "article_8.txt"


def _section(section_id: str, refs: list[str]) -> ParsedSection:
    return ParsedSection(article_number=int(section_id.split("-")[0]), section_id=section_id,
                         section_title="t", full_text="t", referenced_sections=refs)


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
        3: {"3-101"},
        5: {"5-233.1", "5-234"},
        7: {"7-139"},
        9: {"9-121.1", "9-121.11", "9-128.1"},
        12: {"12-127.1"},
        14: {"14-108.2a", "14-108.2b"},
        18: {"18-112"},
        19: {"19-101"},
        20: {"20-122"},
        21: {"21-30"},
        34: {"34-60.1"},
    }
