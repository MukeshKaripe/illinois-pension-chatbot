"""A file may hold one Article, several, or the whole Code: split by the Code's own numbers."""

from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.ingestion.splitter import UnsupportedAct, UnsupportedArticle, split_by_article

STATUTE_DIR = Path(__file__).resolve().parents[1] / "data" / "statutes"


def _join(*lines: str) -> str:
    return "\n".join(lines)


def test_one_article_comes_back_whole() -> None:
    text = _join("(40 ILCS 5/Art. 8 heading)", "(40 ILCS 5/8-101)", "Sec. 8-101. Creation of fund.")
    assert split_by_article(text) == {8: text}


def test_two_articles_are_cut_at_the_heading() -> None:
    text = _join(
        "(40 ILCS 5/Art. 1 heading)", "(40 ILCS 5/1-101)", "Sec. 1-101. Short title.",
        "(40 ILCS 5/Art. 8 heading)", "(40 ILCS 5/8-101)", "Sec. 8-101. Creation of fund.",
    )
    parts = split_by_article(text)
    assert set(parts) == {1, 8}
    assert parts[8].startswith("(40 ILCS 5/Art. 8 heading)")
    assert "1-101" not in parts[8]


def test_a_cut_without_a_heading_happens_at_the_first_header_of_the_next_article() -> None:
    text = _join("(40 ILCS 5/8-101)", "Sec. 8-101. A.", "(40 ILCS 5/20-101)", "Sec. 20-101. B.")
    assert split_by_article(text) == {
        8: _join("(40 ILCS 5/8-101)", "Sec. 8-101. A."),
        20: _join("(40 ILCS 5/20-101)", "Sec. 20-101. B."),
    }


def test_non_contiguous_blocks_of_one_article_are_concatenated() -> None:
    text = _join("(40 ILCS 5/8-101)", "Sec. 8-101. A.", "(40 ILCS 5/20-101)", "Sec. 20-101. B.",
                 "(40 ILCS 5/8-102)", "Sec. 8-102. C.")
    assert split_by_article(text)[8] == _join("(40 ILCS 5/8-101)", "Sec. 8-101. A.",
                                              "(40 ILCS 5/8-102)", "Sec. 8-102. C.")


def test_a_division_heading_stays_with_its_article() -> None:
    text = _join("(40 ILCS 5/Art. 9, div. 2 heading)", "(40 ILCS 5/9-201)", "Sec. 9-201. A.")
    assert split_by_article(text) == {9: text}


def test_another_act_is_refused_by_name() -> None:
    with pytest.raises(UnsupportedAct, match="5 ILCS 375"):
        split_by_article(_join("(5 ILCS 375/6)", "Sec. 6. Something else."))


def test_a_letter_suffixed_article_is_refused() -> None:
    with pytest.raises(UnsupportedArticle, match="22A"):
        split_by_article(_join("(40 ILCS 5/22A-101)", "Sec. 22A-101. Short title."))


def test_text_without_headers_splits_to_nothing() -> None:
    assert split_by_article("Just a cover page.\nNothing else.") == {}


_INTEGER_ARTICLES = sorted(
    (p, int(p.stem.split("_")[1]))
    for p in STATUTE_DIR.glob("article_*.txt")
    if p.stem.split("_")[1].isdigit()
)


@pytest.mark.skipif(not _INTEGER_ARTICLES, reason="ILGA corpus not on disk (data/ is never committed)")
@pytest.mark.parametrize(("path", "article"), _INTEGER_ARTICLES, ids=lambda v: str(v))
def test_every_ilga_file_splits_into_exactly_its_own_article(path: Path, article: int) -> None:
    assert set(split_by_article(path.read_text(encoding="utf-8-sig"))) == {article}
