"""The shape of the Code: the citation graph and the outline built over it."""

from __future__ import annotations

import pytest

from pension_chat.agent import corpus


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test builds its own corpus; a cached one from a neighbour would hide bugs."""
    corpus.reset_corpus_cache()
    yield
    corpus.reset_corpus_cache()


def chunk(section: str, article: str, title: str, refs: list[str] | None = None) -> dict:
    return {
        "section": section,
        "article": article,
        "section_title": title,
        "referenced_sections": refs or [],
    }


async def test_scan_pages_until_the_index_is_exhausted(monkeypatch):
    """One page is not the corpus.

    The index holds thousands of chunks and the client returns them a page at a
    time. A scan that reads the first page builds a graph missing most of its
    edges -- and looks perfectly healthy doing it.
    """
    pages = [
        [chunk("11-134", "11", "Minimum annuities")],
        [chunk("11-216", "11", "Computation of service")],
        [],
    ]
    calls: list[int] = []

    async def fake_page(skip: int):
        calls.append(skip)
        return pages[len(calls) - 1]

    monkeypatch.setattr(corpus, "_scan_page", fake_page)

    docs = await corpus._scan_index()

    assert [d["section"] for d in docs] == ["11-134", "11-216"]
    assert len(calls) == 3, "must keep paging until a page comes back empty"
