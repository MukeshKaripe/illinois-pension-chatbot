"""A recommendation arrives as data, never as a sentence to be parsed.

The alternative is reading the choice back out of the answer with a regex,
which is the pattern this codebase has twice recorded as failing -- and a
recommendation caught after generation is one the member has already read. A
tool makes the choice structured, and makes the refusal something the model can
act on while it still has the turn.
"""

from __future__ import annotations

from pension_chat.agent.tools import run_tool

CANDIDATES = {
    "at age 58": {"annual_amount": "2850.00", "section": "11-134"},
    "at age 60": {"annual_amount": "3240.00", "section": "11-134"},
}


async def test_a_sound_recommendation_is_recorded():
    text, update = await run_tool(
        "recommend",
        {"choice": "at age 60", "objective": "highest_annual"},
        {"scenario_set": CANDIDATES},
    )
    assert update["recommendation"]["choice"] == "at age 60"
    assert update["recommendation"]["objective"] == "highest_annual"
    assert "highest" in text


async def test_a_recommendation_the_figures_refuse_is_not_recorded():
    text, update = await run_tool(
        "recommend",
        {"choice": "at age 58", "objective": "highest_annual"},
        {"scenario_set": CANDIDATES},
    )
    assert "recommendation" not in update
    assert "at age 60" in text, "the refusal has to say what would be right"


async def test_the_tool_answers_rather_than_raising():
    """Every tool answers, even when it fails -- a tool that raises ends the
    member's turn over something the model could have recovered from."""
    text, update = await run_tool("recommend", {}, {})
    assert text
    assert update == {}


async def test_an_excluded_factor_is_named_and_refused():
    text, update = await run_tool(
        "recommend",
        {"choice": "at age 60", "objective": "life expectancy"},
        {"scenario_set": CANDIDATES},
    )
    assert "not in the Pension Code" in text
    assert "recommendation" not in update


async def test_a_superseded_option_is_not_a_candidate():
    """The same filtering the comparison table uses: a figure the member
    corrected is history, and recommending it would recommend a number they
    have already told us is wrong."""
    text, update = await run_tool(
        "recommend",
        {
            "choice": "at age 60 (before the correction on turn 2)",
            "objective": "highest_annual",
        },
        {
            "scenario_set": {
                **CANDIDATES,
                "at age 60 (before the correction on turn 2)": {
                    "annual_amount": "99999.00"
                },
            }
        },
    )
    assert "recommendation" not in update
    assert "not among" in text


def test_the_objective_vocabulary_is_offered_not_guessed():
    """The model picks from an enum, so an unrecognised objective is a bug in
    this list rather than a refusal the member pays for."""
    from pension_chat.agent.objectives import OBJECTIVES
    from pension_chat.agent.tools import RECOMMENDATION_TOOLS

    schema = RECOMMENDATION_TOOLS[0]["function"]["parameters"]["properties"]
    assert set(schema["objective"]["enum"]) == set(OBJECTIVES)


def test_the_tool_is_one_the_loop_offers():
    from pension_chat.agent.loop import BUDGETS, LOOP_TOOLS

    assert "recommend" in {t["function"]["name"] for t in LOOP_TOOLS}
    assert BUDGETS["recommend"] >= 1
