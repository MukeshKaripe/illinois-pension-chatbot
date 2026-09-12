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


def test_normalize_strips_a_subsection_but_never_the_section_id():
    """A subsection is not a section; a trailing letter can be either.

    "11-134(a)" is Section 11-134. But Section 1-113.4a is its own section --
    dropping the "a" pins 1-113.4, a different rule, and that exact confusion
    already cost the ingestion pipeline a whole section's text.
    """
    assert corpus.normalize_section("11-134(a)") == "11-134"
    assert corpus.normalize_section(" 40 ILCS 5/11-134 ") == "11-134"
    assert corpus.normalize_section("Section 11-134") == "11-134"
    assert corpus.normalize_section("1-113.4a") == "1-113.4a"
    assert corpus.normalize_section("11-134.1") == "11-134.1"
    assert corpus.normalize_section("") == ""
    assert corpus.normalize_section("not a section") == ""


def test_sections_order_the_way_the_code_prints_them():
    """Lexical order puts 11-134.1 before 11-134 and 11-9 after 11-130.

    An outline is a reading aid. In the wrong order it is worse than none,
    because it implies the Code says something it does not.
    """
    unsorted = ["11-134.1", "11-9", "11-130", "11-134", "1-113.4a", "1-113.4"]
    assert sorted(unsorted, key=corpus.section_sort_key) == [
        "1-113.4",
        "1-113.4a",
        "11-9",
        "11-130",
        "11-134",
        "11-134.1",
    ]


async def test_the_graph_runs_backwards_as_well_as_forwards(monkeypatch):
    """The backward edge is the whole point.

    Section 11-134 computes on service "as defined in Section 11-216", so a
    reader of 11-216 needs to know 11-134 leans on it. Forward edges were
    already in the index and nothing had ever inverted them.
    """
    async def fake_scan():
        return [
            chunk("11-134", "11", "Minimum annuities", ["11-216", "11-134(f-1)"]),
            chunk("11-130", "11", "Age and service annuity", ["11-216"]),
            chunk("11-216", "11", "Computation of service", []),
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    built = await corpus.load_corpus()

    assert built.cites("11-134") == ("11-216",), "a self-reference is not an edge"
    assert built.cited_by("11-216") == ("11-130", "11-134")
    assert built.cited_by("11-134") == ()
    assert built.cites("not-a-section") == ()


async def test_chunks_of_one_section_collapse_into_one_node(monkeypatch):
    """The index stores a record per subsection, not per section.

    Section 11-134 is a dozen chunks. Treated as a dozen nodes, its references
    are counted a dozen times and its title arrives from whichever chunk landed
    last.
    """
    async def fake_scan():
        return [
            chunk("11-134", "11", "Minimum annuities", ["11-216"]),
            chunk("11-134", "11", "Minimum annuities", ["11-216", "11-131"]),
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    built = await corpus.load_corpus()

    assert list(built.nodes) == ["11-134"]
    assert built.cites("11-134") == ("11-131", "11-216")


async def test_the_corpus_is_built_once_per_process(monkeypatch):
    """A full index scan on every tool call would be indefensible."""
    builds = 0

    async def fake_scan():
        nonlocal builds
        builds += 1
        return [chunk("11-134", "11", "Minimum annuities", [])]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)

    await corpus.load_corpus()
    await corpus.load_corpus()
    assert builds == 1

    corpus.reset_corpus_cache()
    await corpus.load_corpus()
    assert builds == 2


async def test_related_sections_walks_either_direction(monkeypatch):
    async def fake_scan():
        return [
            chunk("11-134", "11", "Minimum annuities", ["11-216"]),
            chunk("11-216", "11", "Computation of service", []),
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)

    assert await corpus.related_sections("11-134") == ("11-216",)
    assert await corpus.related_sections("11-134(a)", "cites") == ("11-216",)
    assert await corpus.related_sections("11-216", "cited_by") == ("11-134",)


async def test_an_unknown_direction_is_refused_rather_than_guessed(monkeypatch):
    """A typo'd direction must not silently return the forward edges.

    This argument reaches here from a model-authored tool call, where "citedBy"
    and "backward" are exactly the kinds of near-misses to expect.
    """
    async def fake_scan():
        return [chunk("11-134", "11", "Minimum annuities", ["11-216"])]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)

    with pytest.raises(ValueError, match="cites"):
        await corpus.related_sections("11-134", "backward")


async def test_outline_is_one_articles_headings_in_order(monkeypatch):
    async def fake_scan():
        return [
            chunk("11-134", "11", "Minimum annuities"),
            chunk("11-130", "11", "Age and service annuity"),
            chunk("12-118", "12", "Employee defined"),
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)

    eleven = await corpus.outline(11)
    assert [(n.section, n.title) for n in eleven] == [
        ("11-130", "Age and service annuity"),
        ("11-134", "Minimum annuities"),
    ]
    assert [n.section for n in await corpus.outline("12")] == ["12-118"]
    assert await corpus.outline(99) == ()
