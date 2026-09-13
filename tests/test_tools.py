"""The capabilities the loop chooses between, and how they behave when they fail.

Offline throughout: each test replaces the retrieval function underneath,
because what is under test is the contract with the model -- what it is told,
and what reaches state -- not whether Azure Search works.
"""

from __future__ import annotations

from pension_chat.agent import corpus, tools
from pension_chat.agent.retrieval import RetrievedSection


def _section(section: str, score: float = 2.0, text: str = "Statute text.") -> RetrievedSection:
    return RetrievedSection(
        section=section, article="11", title=f"Section {section}", text=text, score=score
    )


async def test_search_reports_how_well_it_did(monkeypatch):
    """A model grading its own evidence tends to accept it.

    Saying the score outright is what makes a weak search visible instead of
    something the model has to infer from text it is inclined to believe.
    """

    async def weak(queries, **kwargs):
        return [_section("11-178", score=0.2)]

    monkeypatch.setattr(tools, "retrieve", weak)
    text, _ = await tools.run_tool("search_statute", {"queries": ["x"], "why": "y"}, {})
    assert "weak" in text
    assert "0.20" in text


async def test_search_accumulates_into_retrieved_sections(monkeypatch):
    """The key the pipeline writes, so the guardrail and the card need no change."""

    async def found(queries, **kwargs):
        return [_section("11-134")]

    monkeypatch.setattr(tools, "retrieve", found)
    text, update = await tools.run_tool("search_statute", {"queries": ["x"], "why": "y"}, {})
    assert [s["section"] for s in update["retrieved_sections"]] == ["11-134"]
    # And rendered under the heading ANSWER_SYSTEM's citation rule names.
    assert "### 40 ILCS 5/11-134" in text


async def test_a_search_with_no_queries_says_so_rather_than_failing():
    text, update = await tools.run_tool("search_statute", {}, {})
    assert "queries" in text
    assert update == {}


async def test_outline_lists_headings_in_order(monkeypatch):
    async def scan():
        return [
            {"section": "11-134", "article": "11", "section_title": "Minimum annuities",
             "referenced_sections": []},
            {"section": "11-101", "article": "11", "section_title": "Creation of fund",
             "referenced_sections": []},
        ]

    monkeypatch.setattr(corpus, "_scan_index", scan)
    corpus.reset_corpus_cache()
    text, update = await tools.run_tool("outline", {"article": "11"}, {})
    assert text.index("11-101") < text.index("11-134")
    assert "Creation of fund" in text
    # Headings only: the model must read a section to cite it.
    assert update == {}
    corpus.reset_corpus_cache()


async def test_related_sections_refuses_an_unknown_direction(monkeypatch):
    """"citedBy" and "backward" are exactly the near-misses to expect from a
    model-authored call, and the refusal must reach it as text it can act on."""

    async def scan():
        return [{"section": "11-134", "article": "11", "section_title": "x",
                 "referenced_sections": ["11-216"]}]

    monkeypatch.setattr(corpus, "_scan_index", scan)
    corpus.reset_corpus_cache()
    text, _ = await tools.run_tool("related_sections", {"section": "11-134", "direction": "backward"}, {})
    assert "cites" in text
    corpus.reset_corpus_cache()


async def test_an_unknown_section_is_reported_not_raised(monkeypatch):
    async def nothing(queries, **kwargs):
        return []

    monkeypatch.setattr(tools, "retrieve", nothing)
    text, update = await tools.run_tool("get_section", {"section": "99-999"}, {})
    assert "not in the indexed corpus" in text
    assert update == {}


async def test_an_unknown_tool_name_is_reported_not_raised():
    text, update = await tools.run_tool("summon_lawyer", {}, {})
    assert "no tool called" in text
    assert "search_statute" in text, "say what it could have called instead"
    assert update == {}


async def test_a_tool_that_blows_up_does_not_end_the_turn(monkeypatch):
    """A raised exception takes the member's whole answer with it."""

    async def explode(queries, **kwargs):
        raise RuntimeError("index on fire")

    monkeypatch.setattr(tools, "retrieve", explode)
    text, update = await tools.run_tool("search_statute", {"queries": ["x"], "why": "y"}, {})
    assert "index on fire" in text
    assert "different approach" in text
    assert update == {}


# ── Enrichment, never a filter ────────────────────────────────────


async def test_a_hint_rides_beside_the_results_it_did_not_choose(monkeypatch):
    """"Sections that previously answered questions like this" is a line of
    text next to the results. The search still ran its own query and still
    returned what it found."""

    async def fake_recall(question, *, article, tier):
        return ["11-216", "11-134"]

    monkeypatch.setattr(tools.memory, "recall", fake_recall)
    async def found(queries, **kwargs):
        return [_section("11-134")]

    monkeypatch.setattr(tools, "retrieve", found)

    text, update = await tools.run_tool(
        "search_statute",
        {"queries": ["early retirement reduction"], "why": "the reduction"},
        {"question": "can I retire early?", "coverage": {"article": "11"}},
    )
    assert "11-216" in text
    assert "previously answered" in text.lower()
    assert [s["section"] for s in update["retrieved_sections"]] == ["11-134"], (
        "the hint changed nothing about what was searched or returned"
    )


async def test_a_hint_cannot_suppress_a_fresh_search(monkeypatch):
    """The safety property, stated as a test. A section that once got lucky
    must not be able to entrench itself, so recall never reaches the query."""
    seen: dict = {}

    async def fake_recall(question, *, article, tier):
        return ["11-999"]

    async def fake_retrieve(queries, **kwargs):
        seen["queries"] = list(queries)
        seen["kwargs"] = kwargs
        return []

    monkeypatch.setattr(tools.memory, "recall", fake_recall)
    monkeypatch.setattr(tools, "retrieve", fake_retrieve)

    await tools.run_tool(
        "search_statute", {"queries": ["widow annuity"], "why": "survivors"}, {}
    )
    assert seen["queries"] == ["widow annuity"]
    assert "11-999" not in str(seen["kwargs"]), "no hint reaches the retrieval call"


async def test_no_memory_means_no_line(monkeypatch):
    async def fake_recall(question, *, article, tier):
        return []

    monkeypatch.setattr(tools.memory, "recall", fake_recall)
    async def found(queries, **kwargs):
        return [_section("11-134")]

    monkeypatch.setattr(tools, "retrieve", found)
    text, _ = await tools.run_tool(
        "search_statute", {"queries": ["x"], "why": "y"}, {}
    )
    assert "previously answered" not in text.lower()
