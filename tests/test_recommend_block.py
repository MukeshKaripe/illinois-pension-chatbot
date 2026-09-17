"""A recommendation says what it served and what it could not weigh.

The member's requirement, in their words: recommendations "should be corpus
backed evidence based, and should give a disclaimer as well". A disclaimer the
model is asked to remember is a disclaimer that goes missing, so this is
rendered by code from the same state the arithmetic check wrote.
"""

from __future__ import annotations

from pension_chat.agent.loop import _recommended

_SOUND = {
    "choice": "at age 60",
    "objective": "highest_annual",
    "prose": "the highest annual pension",
    "candidates": {
        "at age 58": {"annual_amount": "2850.00", "section": "11-134"},
        "at age 60": {"annual_amount": "3240.00", "section": "11-134"},
    },
}


def test_a_recommendation_carries_its_disclaimer():
    text = _recommended({"recommendation": _SOUND})
    assert "at age 60" in text
    assert "not advice" in text.lower()


def test_it_names_what_the_code_cannot_weigh():
    """A member deciding when to retire is weighing their health, their tax
    position and how long they expect to live. None of that is in 40 ILCS 5,
    and a confident answer that quietly omits them implies they were considered."""
    text = _recommended({"recommendation": _SOUND}).lower()
    for factor in ("life expectancy", "tax", "health", "investment return", "risk appetite"):
        assert factor in text


def test_it_names_the_objective_it_served():
    assert "highest annual pension" in _recommended({"recommendation": _SOUND})


def test_it_names_the_provisions_the_options_rest_on():
    """Corpus-backed and evidence-based: the member can go and read what the
    comparison was built from."""
    assert "11-134" in _recommended({"recommendation": _SOUND})


def test_no_recommendation_renders_nothing():
    assert _recommended({}) == ""
    assert _recommended({"recommendation": None}) == ""


def test_an_uncited_option_does_not_invent_a_section():
    text = _recommended(
        {
            "recommendation": {
                **_SOUND,
                "candidates": {"a": {"annual_amount": "1.00"}, "b": {"annual_amount": "2.00"}},
            }
        }
    )
    assert text
    assert "Section" not in text


async def test_compose_leaves_the_decision_with_the_member():
    from pension_chat.agent import loop as loop_mod

    out = await loop_mod.compose(
        {
            "question": "when should I retire?",
            "messages": [],
            "loop_messages": [],
            "recommendation": _SOUND,
            "retrieved_sections": [],
        }
    )
    system = " ".join(
        str(m.get("content") or "")
        for m in out["answer_messages"]
        if m.get("role") == "system"
    ).lower()
    assert "decision is theirs" in system
    assert "at age 60" in system


async def test_a_turn_that_recommended_nothing_says_nothing():
    from pension_chat.agent import loop as loop_mod

    out = await loop_mod.compose(
        {
            "question": "what is Article 11?",
            "messages": [],
            "loop_messages": [],
            "retrieved_sections": [],
        }
    )
    assert not any(
        "not advice" in str(m.get("content") or "").lower()
        for m in out["answer_messages"]
    )
