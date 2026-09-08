"""Tests for the gate that decides whether a turn computes anything.

A five-turn conversation produced no pension figure at all, and the first turn
had its answer withdrawn. Nothing was wrong with retrieval, the calculation
engine or the guardrail. The gate was one regex::

    _CALCULATION_SIGNALS = re.compile(
        r"\b(calculat|comput|estimat|how much|what would|...)\b", re.IGNORECASE)

The trailing ``\b`` applies to whichever alternative matched, and after
``calculat`` comes ``e`` -- a word character. So ``calculat``, ``comput`` and
``estimat`` could never match anything. They were dead alternatives that had
never once fired, in the three most obvious words a member could use.

"calculate my estimated pension if I retire today" matched nothing, so no
calculation was planned, no recipe ran, and ``calculation_result`` was None. The
model, holding the member's salary and service in its prompt, multiplied them
together in prose -- and verification correctly threw the whole answer away.

The eval suite passed 25/25 throughout, because every case happened to be
phrased so a *different* alternative fired. "What would my pension be" matches
``what would``; "calculate my pension" matches ``my pension``, not ``calculat``.

So the lesson is not "add a test for calculate". A handful of hand-picked
sentences is exactly what was already there. The test below requires *every*
alternative in the pattern to carry a worked example that actually matches, and
fails if an alternative is added without one.
"""

from __future__ import annotations

import pytest

from pension_chat.agent.nodes import (
    _CALCULATION_SIGNALS,
    _stated_in,
    extract_scenarios,
)

# One natural sentence per alternative in the pattern. The point is not the
# sentences -- it is that the set below must stay equal to the set of
# alternatives in the regex, so no alternative can be added untested.
EXAMPLES: dict[str, str] = {
    "calculat": "calculate my pension for me",
    "recalculat": "please recalculate that",
    "comput": "computed on my final salary",
    "estimat": "give me an estimate",
    "compar": "put both in a comparison table",
    "project": "what is my projected benefit",
    "how much": "how much do I get",
    "what would": "what would I receive",
    "what will": "what will I receive",
    "amount": "the amount I would receive",
    "benefit": "my benefit at retirement",
    "annuity": "my annuity at retirement",
    "my pension": "work out my pension",
    "per month": "what do I get per month",
    "monthly": "my monthly figure",
    "annual": "my annual figure",
    "scenario": "run that scenario",
}


def _alternatives() -> set[str]:
    """The alternatives actually present in the compiled pattern."""
    inner = _CALCULATION_SIGNALS.pattern
    inner = inner.removeprefix(r"\b(?:").removesuffix(r")\b")
    return {
        alternative.replace(r"\w*", "").strip()
        for alternative in inner.split("|")
        if alternative.strip()
    }


def test_the_examples_cover_every_alternative():
    """Adding an alternative without an example must fail here.

    This is the guard that would have caught the original bug: a dead
    alternative has no sentence that matches it, so it cannot be listed above.
    """
    assert _alternatives() == set(EXAMPLES)


@pytest.mark.parametrize(("alternative", "sentence"), sorted(EXAMPLES.items()))
def test_every_alternative_fires(alternative, sentence):
    match = _CALCULATION_SIGNALS.search(sentence)

    assert match, f"{alternative!r} never matches -- it is a dead alternative"
    assert alternative in match.group(0).lower()


# -- The exact questions that failed -------------------------------

@pytest.mark.parametrize(
    "question",
    [
        # Turn 1. Produced the right figures in prose and had them withdrawn.
        "Using my current employee information, calculate my estimated pension "
        "if I retire today.",
        # Turn 2, asked again more plainly. Same result.
        "calculate my estimated pension if I retire today",
        # Never asked, but would have failed the same way.
        "Now calculate it at age 60.",
        # "compare" does not match "comparison" either.
        "Put all four results in one comparison table and explain what changed "
        "between the scenarios.",
    ],
)
def test_the_reported_questions_plan_a_calculation(question):
    assert _CALCULATION_SIGNALS.search(question)


# -- An elliptical follow-up ---------------------------------------

def test_a_follow_up_naming_an_age_carries_no_calculation_vocabulary():
    """The second, independent defect, which survives the regex fix.

    "Now do the same at age 59" has no calculation word in it at all. What it
    does have is a retirement timing, and ``extract_scenarios`` already finds
    that -- so the follow-up is recognised by what it names, not by a wider
    keyword list.
    """
    question = "Now do the same at age 59."

    assert not _CALCULATION_SIGNALS.search(question)
    assert extract_scenarios(question) == [
        {"label": "at age 59", "retirement_age": 59}
    ]


# -- A value the member actually wrote -----------------------------

class TestStatedIn:
    """`_stated_in` decides whether an extracted value may override the record.

    It was a substring test, so any extracted 0 counted as stated by the member
    whenever a 0 appeared anywhere in their message. "Now calculate it at age
    60." made `months_before_age_60 = 0` look member-supplied; the inferred
    value then beat the record, and the early-retirement reduction silently
    vanished while service stayed at 21.5 years -- an annuity figure whose own
    listed inputs contradicted it.

    The same digit-boundary hazard was fixed once already in `_corroborated`,
    where $72,000 was corroborating a retirement age of 72.
    """

    @pytest.mark.parametrize(
        ("value", "text"),
        [
            (0, "Now calculate it at age 60."),      # the reported failure
            (5, "I am 56 with 21.5 years"),
            (1, "21.5 years of service"),
            (44, "Now calculate it at age 60."),
            (2000, "I have $72,000 in salary"),
            ("active", "I am inactive"),
        ],
    )
    def test_a_number_hiding_inside_another_is_not_stated(self, value, text):
        assert not _stated_in(value, text)

    @pytest.mark.parametrize(
        ("value", "text"),
        [
            (60, "Now calculate it at age 60."),
            (25, "Actually I have 25 years of service"),
            (21.5, "I have 21.5 years"),
            # Thousands separators and trailing zeros are how a person writes a
            # number, not a different number.
            (72000, "My salary is $72,000 a year"),
            (72000, "My salary is 72000"),
            (69956.40, "my best four years came to $69,956.40"),
            ("2026-09-08", "I retire on 2026-09-08"),
            ("active", "I am an active employee"),
        ],
    )
    def test_a_value_the_member_wrote_is_stated(self, value, text):
        assert _stated_in(value, text)
