"""The recommended option has to exist, and has to be the one it claims to be.

Style cannot catch this. "I recommend 58 because it gives you the highest
pension" is a well-formed sentence whose numbers both trace to engine output --
the guardrail passes it, and it is wrong. The spec's own example: 58 -> $2,850
and 60 -> $3,240, "rejected by arithmetic, not by style".
"""

from __future__ import annotations

from pension_chat.agent.recommend import check

CANDIDATES = {
    "at age 58": {"annual_amount": "2850.00", "section": "11-134"},
    "at age 60": {"annual_amount": "3240.00", "section": "11-134"},
}


def test_the_option_that_meets_the_objective_stands():
    verdict = check(choice="at age 60", objective="highest_annual", candidates=CANDIDATES)
    assert verdict.ok
    assert verdict.reason == ""
    assert verdict.objective == "highest_annual"


def test_the_option_that_does_not_is_refused_by_arithmetic():
    verdict = check(choice="at age 58", objective="highest_annual", candidates=CANDIDATES)
    assert not verdict.ok
    assert "at age 60" in verdict.reason
    assert "3,240" in verdict.reason


def test_an_option_nobody_computed_cannot_be_recommended():
    verdict = check(choice="at age 62", objective="highest_annual", candidates=CANDIDATES)
    assert not verdict.ok
    assert "not among" in verdict.reason


def test_an_objective_outside_the_vocabulary_is_refused():
    """Not rudeness -- an objective the Code cannot evaluate cannot be checked,
    and an unchecked recommendation is the thing this layer exists to prevent."""
    verdict = check(choice="at age 60", objective="best_for_me", candidates=CANDIDATES)
    assert not verdict.ok
    assert "best_for_me" in verdict.reason


def test_an_excluded_factor_is_named_as_such():
    """A member asking which option is best for their life expectancy is asking
    something real. The answer is that the Code does not say, not that the word
    was unrecognised."""
    verdict = check(
        choice="at age 60", objective="life expectancy", candidates=CANDIDATES
    )
    assert not verdict.ok
    assert "not in the Pension Code" in verdict.reason
    assert "how long a pension is drawn for" in verdict.reason


def test_nothing_to_choose_between_is_not_a_recommendation():
    assert not check(choice="x", objective="highest_annual", candidates={}).ok


def test_a_single_option_is_not_a_recommendation():
    """With one candidate the answer is the answer. Calling it a recommendation
    implies a comparison nobody made."""
    verdict = check(
        choice="at age 60",
        objective="highest_annual",
        candidates={"at age 60": {"annual_amount": "3240.00"}},
    )
    assert not verdict.ok
    assert "one option" in verdict.reason


def test_a_legal_recommendation_needs_no_figures():
    """Recommendation is a reasoning capability that *may* invoke calculation."""
    verdict = check(
        choice="the deferred annuity",
        objective="eligibility",
        candidates={
            "the deferred annuity": {"section": "11-134"},
            "a refund of contributions": {"section": "11-166"},
        },
    )
    assert verdict.ok


def test_the_verdict_carries_what_it_judged():
    """The block that renders the recommendation reads these rather than being
    passed them again, so what the member is shown is what was checked."""
    verdict = check(choice="at age 60", objective="highest_annual", candidates=CANDIDATES)
    assert verdict.choice == "at age 60"
    assert set(verdict.candidates) == set(CANDIDATES)
