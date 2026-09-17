"""What the member can be helped to want, and what the Code cannot tell them.

An objective is what makes a recommendation checkable: "I recommend 58 because
it gives you the highest pension" is either true of the computed figures or it
is not. A free-text objective is a sentence; this is a predicate.
"""

from __future__ import annotations

from pension_chat.agent.objectives import EXCLUDED, OBJECTIVES, objective_for

_CANDIDATES = {
    "at age 58": {"annual_amount": "2850.00", "section": "11-134"},
    "at age 60": {"annual_amount": "3240.00", "section": "11-134"},
}


def test_the_highest_amount_is_the_larger_figure():
    highest = objective_for("highest_annual")
    assert highest is not None
    assert highest.satisfied_by("at age 60", _CANDIDATES)
    assert not highest.satisfied_by("at age 58", _CANDIDATES)


def test_the_soonest_option_is_the_earliest_one_offered():
    """Order is the order the member built them in, which is the order they
    were computed and filed."""
    soonest = objective_for("soonest")
    assert soonest is not None
    assert soonest.satisfied_by("at age 58", _CANDIDATES)
    assert not soonest.satisfied_by("at age 60", _CANDIDATES)


def test_an_eligibility_objective_needs_a_section_not_a_figure():
    """"Which benefit option is available to me?" may be entirely legal. A
    recommendation is a reasoning capability that *may* invoke calculation, not
    calculation with a different label."""
    legal = objective_for("eligibility")
    assert legal is not None
    assert legal.satisfied_by("at age 58", {"at age 58": {"section": "11-134"}})
    assert not legal.satisfied_by("at age 58", {"at age 58": {}})


def test_a_numeric_objective_refuses_an_unpriced_candidate():
    """Silently dropping the candidate nobody could price would make the
    recommendation the best of whatever happened to compute."""
    highest = objective_for("highest_annual")
    assert highest is not None
    assert not highest.satisfied_by(
        "at age 58", {"at age 58": {"annual_amount": "2850.00"}, "at age 60": {}}
    )


def test_an_unknown_objective_is_not_an_objective():
    assert objective_for("whatever_feels_right") is None
    assert objective_for("") is None


def test_what_the_code_cannot_weigh_is_named_rather_than_missing():
    """The agent may say so and stop there -- but it has to be able to say it,
    which means the reasons are written down and not left to the model."""
    assert set(EXCLUDED) == {
        "life expectancy",
        "tax",
        "investment return",
        "health",
        "risk appetite",
    }
    assert all(reason for reason in EXCLUDED.values())


def test_no_objective_promises_something_excluded():
    """A bug class: an objective that needed life expectancy to evaluate would
    be checkable only by inventing one, which is exactly what `EXCLUDED` says
    the agent must not do."""
    for objective in OBJECTIVES.values():
        assert objective.reads in {"annual_amount", "monthly_amount", "section", ""}


def test_every_objective_can_say_what_it_served():
    """The answer names the objective back to the member, so a recommendation
    is never "I recommend 60" with no account of why that is the answer."""
    assert all(objective.prose for objective in OBJECTIVES.values())


def test_a_tie_is_satisfied_by_either():
    """Two options paying the same is not a reason to refuse one of them."""
    highest = objective_for("highest_annual")
    assert highest is not None
    tied = {"a": {"annual_amount": "100.00"}, "b": {"annual_amount": "100.00"}}
    assert highest.satisfied_by("a", tied)
    assert highest.satisfied_by("b", tied)
