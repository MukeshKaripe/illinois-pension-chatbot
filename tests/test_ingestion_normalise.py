"""Every converter's output passes through the same cleanup, so formats cannot drift apart."""

from __future__ import annotations

from pension_chat.ingestion.normalise import (
    join_broken_lines,
    normalise,
    remove_page_furniture,
    strip_markdown,
)


def test_normalise_replaces_nbsp_and_removes_invisible_characters() -> None:
    raw = "﻿\xa0\xa0\xa0\xa0(40 ILCS 5/8-101)\xa0(from Ch. 108 1/2)\r\nSec.​ 8-101.\xa0Creation of fund.   \r\n"
    assert normalise(raw) == "    (40 ILCS 5/8-101) (from Ch. 108 1/2)\nSec. 8-101. Creation of fund.\n"


def test_strip_markdown_uncovers_parser_anchors() -> None:
    md = "## (40 ILCS 5/8-101)\n**Sec. 8-101.** Creation of fund.\n\\(a\\) First.\n- (b) Second.\n- an ordinary bullet"
    assert strip_markdown(md).split("\n") == [
        "(40 ILCS 5/8-101)",
        "Sec. 8-101. Creation of fund.",
        "(a) First.",
        "(b) Second.",
        "- an ordinary bullet",
    ]


def test_page_furniture_is_removed_and_a_subsection_after_a_page_break_is_kept() -> None:
    pages = [
        ["Illinois Compiled Statutes", "(40 ILCS 5/8-101)", "Sec. 8-101. Creation of fund.", "Page 1 of 3"],
        ["Illinois Compiled Statutes", "(a) A subsection that starts a page.", "more text here", "Page 2 of 3"],
        ["Illinois Compiled Statutes", "(b) Another subsection.", "(Source: Laws 1963, p. 161.)", "Page 3 of 3"],
    ]
    assert remove_page_furniture(pages) == [
        "(40 ILCS 5/8-101)",
        "Sec. 8-101. Creation of fund.",
        "(a) A subsection that starts a page.",
        "more text here",
        "(b) Another subsection.",
        "(Source: Laws 1963, p. 161.)",
    ]


def test_furniture_detection_needs_three_pages() -> None:
    # With two pages a repeated line is as likely to be text as a header.
    pages = [["Same line", "text a"], ["Same line", "text b"]]
    assert remove_page_furniture(pages) == ["Same line", "text a", "Same line", "text b"]


def test_broken_lines_are_joined_only_when_unambiguous() -> None:
    text = "\n".join([
        "Sec. 8-101. Creation of fund. The fund shall be admin-",
        "istered by the board for the benefit of",
        "employees of the city.",
        "(a) Never joined onto the line above, even though it follows a sentence",
        "(40 ILCS 5/8-102) never joined either",
        "Ends with a full stop.",
        "lower case after a full stop stays on its own line",
    ])
    assert join_broken_lines(text).split("\n") == [
        "Sec. 8-101. Creation of fund. The fund shall be administered by the board "
        "for the benefit of employees of the city.",
        "(a) Never joined onto the line above, even though it follows a sentence",
        "(40 ILCS 5/8-102) never joined either",
        "Ends with a full stop.",
        "lower case after a full stop stays on its own line",
    ]
