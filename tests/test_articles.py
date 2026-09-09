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
from pension_chat.agent.retrieval import (
    _ARTICLE_BY_NAME,
    CROSS_CUTTING_ARTICLES,
    _article_filter,
    extract_article_references,
)
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


# ── Which Article a recipe is filed under ─────────────────────────


def test_the_governing_article_follows_coverage_not_the_record():
    """A conversation that has moved Articles must look up recipes there.

    Once a member confirms their file is wrong, or asks to suppose a different
    job, the record's Article number stops describing what is being read. Left
    unchanged, an Article 12 provision derived in that conversation is filed
    against Article 11 and handed to the next Article 11 member who asks.
    """
    record_only = {"employee_record": {"article_number": 11, "tier": 1}}
    assert nodes._governing_article(record_only) == 11

    confirmed_elsewhere = {
        "employee_record": {"article_number": 11, "tier": 1},
        "coverage": {"article": "12", "basis": "member_confirmed", "record_article": "11"},
    }
    assert nodes._governing_article(confirmed_elsewhere) == 12

    supposed = {
        "employee_record": {"article_number": 11, "tier": 1},
        "coverage": {"article": "12", "basis": "hypothetical", "record_article": "11"},
    }
    assert nodes._governing_article(supposed) == 12


def test_an_anonymous_member_falls_back_to_the_article_they_named():
    assert nodes._governing_article({"explicit_articles": ["12"]}) == 12
    assert nodes._governing_article({}) == 11


def test_a_recipe_is_filed_under_the_article_its_text_came_from():
    """Section ids carry their Article, and that beats the member's record.

    `_follow_references` crosses Article boundaries deliberately — cross-article
    references are legitimate law — so a recipe can be derived from text the
    member's own Article does not contain. At two Articles this could not go far
    wrong. At twenty it files the recipe in the wrong drawer.
    """
    assert nodes._article_of_sections(["11-134", "11-216"]) == 11
    assert nodes._article_of_sections(["12-133", "12-101"]) == 12
    # Sections from two Articles say nothing definite, so the caller's fallback
    # decides rather than one of them winning by sort order.
    assert nodes._article_of_sections(["11-134", "12-133"]) is None
    assert nodes._article_of_sections([]) is None


# -- Articles that govern the members of other Articles ------------


def test_the_general_provisions_do_not_widen_a_member_s_search():
    """Reachability comes from the fetch path, not from a wider filter.

    Section 1-160 is what makes a Tier 2 member of Article 11 accrue
    differently, so it plainly has to be reachable. Adding Article 1 and 20 to
    the search filter looked like the way to do that, and measurably was not:
    three benchmark cases began retrieving them for ordinary Article 11
    questions, and Section 11-134 and Section 11-215 were pushed out of the top
    sections on cases that had been citing them.

    `fetch_sections` is called with no article filter on every path, so a
    section the question names or the retrieved text cross-references already
    resolves whatever Article it lives in — which is where the reachability
    actually comes from.
    """
    assert _article_filter(["11"]) == "search.in(article, '11', ',')"
    assert _article_filter(["11", "12"]) == "search.in(article, '11,12', ',')"

    # No filter at all still means the whole corpus.
    assert _article_filter([]) is None
    assert _article_filter(None) is None

    # The set is still named, because coverage reads it for a different reason:
    # an Article nobody is a member of cannot be where a member is routed.
    assert CROSS_CUTTING_ARTICLES == ("1", "20")


def test_an_article_can_be_named_by_its_title_only_when_nobody_is_a_member():
    """Naming an Article moves the filter and bypasses coverage entirely.

    That is right for "what does Article 12 provide" and would be a serious
    regression for "I am a park employee" — the second sentence is coverage
    evidence to be checked against the Code, not an instruction about which law
    to read. So fund Articles are matched by number and never by name.

    Articles nobody is a member of have no such ambiguity: you cannot describe
    yourself into the Reciprocal Act, so its title is the only way anyone refers
    to it, and without this, ingesting Article 20 leaves it reachable only by
    someone who already knows to type "Article 20".
    """
    assert extract_article_references(
        "Can my service be coordinated under the Reciprocal Act?"
    ) == ["20"]
    assert extract_article_references(
        "Does the Retirement Systems Reciprocal Act apply to me?"
    ) == ["20"]

    # The restriction is the safety argument, so it is enforced rather than
    # described: adding a fund Article here would let a self-description move
    # the retrieval filter and skip coverage entirely.
    assert all(
        article in CROSS_CUTTING_ARTICLES for article, _ in _ARTICLE_BY_NAME
    ), "a fund Article must never be matchable by name"

    # The sentences the coverage layer exists to handle stay untouched.
    for description in (
        "i am a park employee, how is my pension calculated?",
        "I work for the Laborers' and Retirement Board Employees' fund",
        "a worker who works at the park",
        "I am a municipal employee of the city",
    ):
        assert extract_article_references(description) == [], description


@pytest.mark.asyncio
async def test_naming_a_cross_cutting_article_adds_to_the_filter(monkeypatch):
    """Article 20 governs this member; Article 12 governs someone else.

    Reading "under the Reciprocal Act" the way "what does Article 12 provide" is
    read would drop the member's own fund from the search and answer as though
    they had asked about somebody else's law.
    """
    calls: list[dict] = []

    async def fake_retrieve(queries, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(nodes, "retrieve", fake_retrieve)

    state = initial_state("Can my service be coordinated under the Reciprocal Act?", "s")
    state["in_scope"] = True
    state["explicit_articles"] = ["20"]
    state["employee_record"] = {"article_number": 11}

    result = await nodes.retrieve_policy(state)

    # Their own Article is still what the main query reads from...
    assert calls[0]["articles"] == ["11"]
    # ...and Article 20 is asked separately, because 294 Article 11 subsections
    # otherwise out-rank the whole 35-subsection Reciprocal Act.
    assert calls[1]["articles"] == ["20"]
    # This is the member's own law, so nothing is suppressed.
    assert result["answers_other_article"] is False


@pytest.mark.asyncio
async def test_naming_a_fund_article_still_moves_the_filter(monkeypatch):
    calls: list[dict] = []

    async def fake_retrieve(queries, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(nodes, "retrieve", fake_retrieve)

    state = initial_state("What does Article 12 provide?", "s")
    state["in_scope"] = True
    state["explicit_articles"] = ["12"]
    state["employee_record"] = {"article_number": 11}

    result = await nodes.retrieve_policy(state)

    assert len(calls) == 1
    assert calls[0]["articles"] == ["12"]
    assert result["answers_other_article"] is True
