"""Which cross-references retrieval follows, and why the old rule missed them."""

from __future__ import annotations

import pytest

from pension_chat.agent import corpus, retrieval
from pension_chat.agent.retrieval import RetrievedSection


@pytest.fixture(autouse=True)
def _clean():
    retrieval.reset_caches()
    yield
    retrieval.reset_caches()


def section(section_id: str, refs: list[str], score: float) -> RetrievedSection:
    return RetrievedSection(
        section=section_id,
        article="11",
        title=f"Section {section_id}",
        text="x" * 2000,
        score=score,
        referenced_sections=refs,
    )


@pytest.fixture
def fetched(monkeypatch):
    """Every requested section comes back with enough text to survive the budget."""

    async def fake_fetch(section_ids, articles=None):
        return {
            s: [
                {
                    "section": s,
                    "subsection_id": "(a)",
                    "content": "y" * 2000,
                    "article": "11",
                    "section_title": f"Section {s}",
                }
            ]
            for s in section_ids
        }

    monkeypatch.setattr(retrieval, "fetch_sections", fake_fetch)


async def test_a_reference_below_the_top_three_is_now_reachable(monkeypatch, fetched):
    """The old rule read `assembled[:3]` and could not see the fourth.

    Rank is about how well a section matched the query. Whether it names the
    definition the answer turns on is a different question, and slicing by rank
    answered it by accident.
    """

    async def fake_scan():
        return [
            {"section": "11-130", "article": "11", "section_title": "A", "referenced_sections": []},
            {"section": "11-131", "article": "11", "section_title": "B", "referenced_sections": []},
            {"section": "11-132", "article": "11", "section_title": "C", "referenced_sections": []},
            {
                "section": "11-134",
                "article": "11",
                "section_title": "D",
                "referenced_sections": ["11-216"],
            },
            {"section": "11-216", "article": "11", "section_title": "Service", "referenced_sections": []},
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    corpus.reset_corpus_cache()

    assembled = [
        section("11-130", [], 0.9),
        section("11-131", [], 0.8),
        section("11-132", [], 0.7),
        section("11-134", ["11-216"], 0.6),
    ]

    extra = await retrieval._follow_references(assembled, char_budget=20000, limit=2)

    assert [s.section for s in extra] == ["11-216"]


async def test_the_most_depended_on_section_is_followed_first(monkeypatch, fetched):
    """With budget for one, take what the retrieved text leans on most.

    The old rule took whichever reference it met first while walking the
    sections in rank order, so the single passing mention in the best-matching
    section beat the definition two other sections both turned on.
    """

    async def fake_scan():
        return [
            {
                "section": "11-130",
                "article": "11",
                "section_title": "A",
                "referenced_sections": ["11-169"],
            },
            {
                "section": "11-134",
                "article": "11",
                "section_title": "B",
                "referenced_sections": ["11-216"],
            },
            {
                "section": "11-131",
                "article": "11",
                "section_title": "C",
                "referenced_sections": ["11-216"],
            },
            {"section": "11-216", "article": "11", "section_title": "Service", "referenced_sections": []},
            {"section": "11-169", "article": "11", "section_title": "Other", "referenced_sections": []},
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    corpus.reset_corpus_cache()

    assembled = [
        section("11-130", ["11-169"], 0.9),
        section("11-134", ["11-216"], 0.8),
        section("11-131", ["11-216"], 0.7),
    ]

    extra = await retrieval._follow_references(assembled, char_budget=20000, limit=1)

    assert [s.section for s in extra] == ["11-216"], "two sections cite it; 11-169 only one"


async def test_a_section_already_retrieved_is_never_followed(monkeypatch, fetched):
    async def fake_scan():
        return [
            {
                "section": "11-134",
                "article": "11",
                "section_title": "B",
                "referenced_sections": ["11-216"],
            },
            {"section": "11-216", "article": "11", "section_title": "Service", "referenced_sections": []},
        ]

    monkeypatch.setattr(corpus, "_scan_index", fake_scan)
    corpus.reset_corpus_cache()

    assembled = [section("11-134", ["11-216"], 0.9), section("11-216", [], 0.5)]

    assert await retrieval._follow_references(assembled, char_budget=20000, limit=2) == []
