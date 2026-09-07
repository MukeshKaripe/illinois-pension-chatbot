"""Tests for reading a question about an Article the member is not in.

An Article 11 member asked what provisions an Article 12 employee should be
considered under, and was told:

    "Article 12 is not covered by the statute text in front of me."

Untrue on two counts. Article 12 is ingested and indexed; every section the
answer needed was sitting in the corpus. And "the statute text in front of me"
is the machinery talking -- what the member heard was that the assistant does
not cover their question, when what had actually happened is that retrieval was
filtered to Article 11 and never looked.

Widening the filter to both Articles is not the fix. Ranking favours Article
11's fuller provisions even for a query that says "Article 12" outright, so an
unfiltered search returns no Article 12 sections at all. Naming an Article has
to move the filter.
"""

from __future__ import annotations

import pytest

from pension_chat.agent import nodes
from pension_chat.agent.nodes import prepare_answer, triage
from pension_chat.agent.retrieval import extract_article_references
from pension_chat.agent.state import initial_state

# -- Reading the Article out of the question -----------------------

@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("An employee is covered by Article 12.", ["12"]),
        ("what does article 12 provide", ["12"]),
        ("Art. 12 vs Article 11", ["12", "11"]),
        # A section reference names its own Article.
        ("What does Section 12-133 say?", ["12"]),
        ("Explain 40 ILCS 5/11-134", ["11"]),
        # Both, when the member asks for the comparison.
        ("How does Article 12 differ from Article 11?", ["12", "11"]),
    ],
)
def test_the_article_is_read_from_the_question(question, expected):
    assert extract_article_references(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "What would my pension be?",
        "I have 25 years of service",
        # No Article named, and no digits that could be mistaken for one.
        "when can i retire",
    ],
)
def test_a_question_naming_no_article_yields_none(question):
    assert extract_article_references(question) == []


def test_a_salary_is_not_an_article():
    assert extract_article_references("My salary is $72,000 a year") == []


# -- Which Article gets searched -----------------------------------

ARTICLE_11_MEMBER = {"article_number": 11, "tier": 1}


@pytest.fixture
def searched(monkeypatch):
    """Capture the Article filter without going near the index.

    What broke was the filter, not the search, so that is what is asserted
    here — and it keeps the suite offline.
    """
    seen: dict = {}

    async def fake_retrieve(queries, *, articles=None, **kwargs):
        seen["articles"] = articles
        return []

    monkeypatch.setattr(nodes, "retrieve", fake_retrieve)
    return seen


@pytest.mark.anyio
async def test_naming_an_article_moves_the_filter_off_the_members_own(searched):
    """The whole bug in one assertion.

    Retrieval searched Article 11, found no Article 12 text, and the answer
    reported the Article as uncovered.
    """
    state = initial_state("What does Article 12 provide at 60?", "s")
    state.update(await triage(state))
    state.update(employee_record=ARTICLE_11_MEMBER, search_queries=["x"])

    result = await nodes.retrieve_policy(state)

    assert state["explicit_articles"] == ["12"]
    assert searched["articles"] == ["12"]
    assert result["answers_other_article"] is True


@pytest.mark.anyio
async def test_a_question_about_another_article_is_not_the_members_own_benefit(searched):
    """Their salary and service belong to Article 11.

    Running an Article 11 record through an Article 12 provision would produce a
    figure that is wrong for both, so the calculation is dropped and the answer
    explains the law instead.
    """
    state = initial_state("What does Article 12 pay at 60?", "s")
    state.update(
        explicit_articles=["12"],
        employee_record=ARTICLE_11_MEMBER,
        requires_calculation=True,
        search_queries=["x"],
    )

    result = await nodes.retrieve_policy(state)

    assert result["requires_calculation"] is False


@pytest.mark.anyio
async def test_the_members_own_article_is_still_their_own_question(searched):
    state = initial_state("What does Section 11-134 say?", "s")
    state.update(
        explicit_articles=["11"],
        employee_record=ARTICLE_11_MEMBER,
        requires_calculation=True,
        search_queries=["x"],
    )

    result = await nodes.retrieve_policy(state)

    assert searched["articles"] == ["11"]
    assert result["answers_other_article"] is False
    assert result["requires_calculation"] is True


@pytest.mark.anyio
async def test_no_article_named_still_pins_to_the_members_own(searched):
    state = initial_state("What would my pension be?", "s")
    state.update(
        employee_record=ARTICLE_11_MEMBER,
        requires_calculation=True,
        search_queries=["x"],
    )

    result = await nodes.retrieve_policy(state)

    assert searched["articles"] == ["11"]
    assert result["answers_other_article"] is False


@pytest.mark.anyio
async def test_an_anonymous_question_naming_no_article_searches_everything(searched):
    state = initial_state("What does the Code say about early retirement?", "s")
    state.update(search_queries=["x"])

    await nodes.retrieve_policy(state)

    assert searched["articles"] is None


# -- What the answer is told ---------------------------------------

@pytest.mark.anyio
async def test_the_answer_is_told_not_to_claim_the_article_is_missing():
    state = initial_state("What does Article 12 pay at 60?", "s")
    state.update(
        answers_other_article=True,
        member_facts={"article_number": 11, "age": 56},
        retrieved_sections=[
            {"section": "12-133", "article": "12", "title": "Fixed benefit", "text": "..."}
        ],
        recipes={},
    )

    prompt = "\n".join(m["content"] for m in (await prepare_answer(state))["answer_messages"])

    assert "do not say the Article is unavailable to you" in prompt.replace("\n", " ")
    assert "Do not give this member's own" in prompt


@pytest.mark.anyio
async def test_an_ordinary_turn_carries_no_such_note():
    state = initial_state("What would my pension be?", "s")
    state.update(retrieved_sections=[], recipes={})

    prompt = "\n".join(m["content"] for m in (await prepare_answer(state))["answer_messages"])

    assert "DIFFERENT PART OF THE CODE" not in prompt
