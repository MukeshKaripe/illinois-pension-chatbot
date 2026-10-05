"""`parse_statute_text` is `parse_statute_file` without the file.

Converters hand the parser text, not a path. The body of the parser moved
unchanged; this pins that the two entry points agree.
"""

from __future__ import annotations

from pathlib import Path

from pension_chat.ingestion.parser import parse_statute_file, parse_statute_text

TEXT = (
    "(40 ILCS 5/Art. 8 heading)\n"
    "(40 ILCS 5/8-101) (from Ch. 108 1/2, par. 8-101)\n"
    "Sec. 8-101. Creation of fund. In each city of more than 500,000 inhabitants a fund shall be created.\n"
    "(Source: Laws 1963, p. 161.)\n"
    "(40 ILCS 5/8-102) (from Ch. 108 1/2, par. 8-102)\n"
    "Sec. 8-102. Terms defined.\n"
    "(a) The terms used in this Article have the meanings in Sections 8-103 to 8-110.\n"
    "(b) See also Section 1-160.\n"
    "(Source: Laws 1963, p. 161.)\n"
)


def test_text_and_file_entry_points_agree(tmp_path: Path) -> None:
    path = tmp_path / "article_8.txt"
    path.write_text(TEXT, encoding="utf-8")

    from_file = parse_statute_file(str(path), 8)
    from_text = parse_statute_text(TEXT, 8)

    assert [s.section_id for s in from_text] == ["8-101", "8-102"]
    assert from_text == from_file


def test_crlf_text_parses_like_lf_text() -> None:
    assert parse_statute_text(TEXT.replace("\n", "\r\n"), 8) == parse_statute_text(TEXT, 8)
