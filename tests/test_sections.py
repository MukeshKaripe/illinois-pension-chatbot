"""Tests for the statute-text endpoint's two pure parts: the id gate and assembly.

The gate is a security boundary, not tidiness: `retrieval.fetch_sections`
interpolates section ids into an OData filter string, so an id carrying a quote
would break out of it. Only the shapes the Code actually uses get through.
"""

from __future__ import annotations

import pytest

from pension_chat.api.sections import SECTION_ID, assemble_section


@pytest.mark.parametrize("section_id", ["11-134", "1-160", "1-113.4a", "20-104", "22A-101"])
def test_accepts_the_codes_section_ids(section_id: str) -> None:
    assert SECTION_ID.fullmatch(section_id)


@pytest.mark.parametrize(
    "section_id",
    ["", "11", "11-134'", "11-134') or (section eq 'x", "../etc/passwd", "11-134 ", "article 11"],
)
def test_rejects_anything_else(section_id: str) -> None:
    assert not SECTION_ID.fullmatch(section_id)


def test_assembles_subsections_in_natural_order() -> None:
    chunks = [
        {"subsection_id": "(10)", "content": "ten", "section_title": "Retirement annuity", "article": 11},
        {"subsection_id": "(2)", "content": "two ", "section_title": "Retirement annuity", "article": 11},
        {"subsection_id": "(a)", "content": "a", "section_title": "Retirement annuity", "article": 11},
    ]
    section = assemble_section("11-134", chunks)
    assert section is not None
    assert section.text.split("\n\n") == ["two", "ten", "a"]
    assert section.title == "Retirement annuity"
    assert section.article == "11"
    assert section.citation == "40 ILCS 5/11-134"


def test_nothing_indexed_means_no_section() -> None:
    assert assemble_section("11-134", []) is None
