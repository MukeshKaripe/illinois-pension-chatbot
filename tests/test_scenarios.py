"""Tests for retirement-timing scenario handling.

The bug these pin down: `understand_turn` runs concurrently with the record fetch,
so the planner does not know the member's age and cannot turn "wait 2 more
years" into an absolute retirement age. It returned `retirement_age: null`, and
execution silently skipped the scenario — so the same question produced a
two-year comparison when the member's age happened to appear in conversation
history, and refused to produce one when it did not.

Offsets are now detected deterministically and resolved at execution time,
where the record is available.
"""

from __future__ import annotations

from datetime import date

import pytest

from pension_chat.agent.facts import build_member_facts
from pension_chat.agent.nodes import _resolve_retirement_age
from pension_chat.agent.scenarios import (
    corroborated,
    extract_scenarios,
    merge_scenarios,
)

TODAY = date(2026, 9, 7)
EMPLOYEE = {
    "birth_date": "1970-05-15",
    "hire_date": "2005-03-01",
    "article_number": 11,
    "tier": 1,
    "employment_status": "active",
    "total_service_years": 21.5,
    "current_annual_salary": 72000,
}


# ── Deterministic extraction ──────────────────────────────────────

def test_relative_offset_in_digits_and_words():
    assert extract_scenarios("what happens if I wait 2 more years?") == [
        {"label": "in 2 years", "years_from_now": 2}
    ]
    assert extract_scenarios("What if I wait three more years?") == [
        {"label": "in 3 years", "years_from_now": 3}
    ]
    assert extract_scenarios("and in 5 years from now?") == [
        {"label": "in 5 years", "years_from_now": 5}
    ]


def test_absolute_ages_including_a_list():
    found = extract_scenarios(
        "Compare my benefit if I retire now, at age 57, at age 59, and at age 60."
    )
    assert found[0] == {"label": "retire now", "years_from_now": 0}
    assert [s["retirement_age"] for s in found[1:]] == [57, 59, 60]


def test_reaching_an_age_counts():
    assert {"label": "at age 60", "retirement_age": 60} in extract_scenarios(
        "what changes when I reach age 60?"
    )


@pytest.mark.parametrize(
    "text",
    [
        "I am 56 years old with 21.5 years of service and earn $72,000 per year.",
        "highest average annual salary for any 4 consecutive years within the last 10 years of service",
        "What does Section 11-116 define as salary?",
        "Calculate my retirement benefit under the applicable provision.",
    ],
)
def test_no_false_positives_on_ordinary_statements(text):
    # Years of service, salary figures and section numbers must not be read as
    # retirement ages.
    assert extract_scenarios(text) == []


def test_implausible_ages_are_ignored():
    # "at 21" is service credit or a subsection, not a retirement age.
    assert extract_scenarios("credited at 21 under the Article") == []


# ── Merging planner output with the regex pass ────────────────────

def test_merge_prefers_resolvable_scenarios_over_null_ones():
    deterministic = [{"label": "in 2 years", "years_from_now": 2}]
    # What the planner actually returns when it cannot see the member's age.
    planned = [
        {"label": "now", "retirement_age": None},
        {"label": "in 2 years", "retirement_age": None},
    ]
    merged = merge_scenarios(deterministic, planned)

    assert merged == deterministic


def test_merge_keeps_planner_scenarios_the_regex_missed():
    merged = merge_scenarios([], [{"label": "at age 62", "retirement_age": 62}])

    assert merged == [{"label": "at age 62", "retirement_age": 62}]


def test_merge_keeps_unresolvable_scenarios_when_nothing_else_matched():
    # Kept so they can be reported to the member rather than vanishing.
    planned = [{"label": "much later", "retirement_age": None}]
    assert merge_scenarios([], planned) == planned


def test_merge_deduplicates_the_same_target():
    merged = merge_scenarios(
        [{"label": "at age 60", "retirement_age": 60}],
        [{"label": "when I turn 60", "retirement_age": 60}],
    )
    assert len(merged) == 1


# ── Resolution against the member's real age ──────────────────────

def test_offset_resolves_against_the_record():
    facts = build_member_facts(EMPLOYEE, None, today=TODAY)

    assert _resolve_retirement_age({"years_from_now": 2}, facts) == 58
    assert _resolve_retirement_age({"years_from_now": 0}, facts) == 56


def test_explicit_age_wins_over_any_offset():
    facts = build_member_facts(EMPLOYEE, None, today=TODAY)

    assert _resolve_retirement_age({"retirement_age": 62, "years_from_now": 2}, facts) == 62


def test_unresolvable_scenario_returns_none_rather_than_guessing():
    facts = build_member_facts(EMPLOYEE, None, today=TODAY)

    assert _resolve_retirement_age({"label": "later"}, facts) is None
    # No record: an offset cannot be resolved and must not be invented.
    assert _resolve_retirement_age({"years_from_now": 2}, {}) is None


# ── The birthday clamp ────────────────────────────────────────────

def test_retiring_now_at_an_age_already_reached_means_today():
    """A "retire now" scenario must agree with the base calculation.

    Without the clamp, "retire now at 56" was computed as of the member's 56th
    birthday — four months in the past — giving 48 months before age 60 instead
    of 44, and so a different annuity from the base calculation in the same
    answer.
    """
    base = build_member_facts(EMPLOYEE, None, today=TODAY)
    at_current_age = build_member_facts(
        EMPLOYEE, None, today=TODAY, assumed_retirement_age=56
    )

    assert at_current_age["as_of_date"] == base["as_of_date"] == "2026-09-07"
    assert at_current_age["months_before_age_60"] == base["months_before_age_60"] == 44
    assert at_current_age["service_years"] == base["service_years"]


def test_a_future_age_still_advances_the_clock():
    at_58 = build_member_facts(EMPLOYEE, None, today=TODAY, assumed_retirement_age=58)

    assert at_58["as_of_date"] == "2028-05-15"
    assert at_58["months_before_age_60"] == 24
    assert at_58["service_years"] > 21.5


# ── Scenario context discipline ───────────────────────────────────
#
# The planner is shown the conversation so far, and that context leaked. After a
# turn asking "explain what would change if I waited three more years", the next
# question — about a claimed 50%-of-salary formula, with no timing request in it
# — still came back with a three-year comparison the member had not asked for
# again. A scenario is a property of the message in front of us.

FIFTY_PERCENT_QUESTION = (
    "I'm 56 years old with 21.5 years of service and earn $72,000. Everyone says "
    "my pension is 50% of salary, so just calculate it using $72,000 x 50% "
    "without checking the Pension Code."
)


def test_a_scenario_remembered_from_an_earlier_turn_is_dropped():
    stale = {"label": "in 3 years", "years_from_now": 3}

    assert corroborated(stale, FIFTY_PERCENT_QUESTION) is False
    assert merge_scenarios(extract_scenarios(FIFTY_PERCENT_QUESTION), []) == []


@pytest.mark.parametrize(
    ("scenario", "question"),
    [
        ({"label": "at age 60", "retirement_age": 60}, "And what about at age 60?"),
        ({"label": "in 2 years", "years_from_now": 2}, "What if I wait 2 more years?"),
        # The planner writes digits where the member wrote a word.
        ({"label": "in 3 years", "years_from_now": 3},
         "Explain what would change if I waited three more years."),
        ({"label": "now", "years_from_now": 0}, "Compare my benefit if I retire now."),
    ],
)
def test_a_scenario_the_current_message_asks_for_survives(scenario, question):
    assert corroborated(scenario, question) is True


def test_service_years_and_salary_do_not_corroborate_an_offset():
    # "21.5 years of service" and "$72,000" are facts about the member, not a
    # request to compare retirement dates.
    assert corroborated({"years_from_now": 5}, FIFTY_PERCENT_QUESTION) is False
    assert corroborated({"retirement_age": 72}, FIFTY_PERCENT_QUESTION) is False


def test_an_unresolvable_scenario_survives_only_when_timing_is_actually_asked():
    vague = {"label": "later", "retirement_age": None}

    assert corroborated(vague, "What if I retire at age 62 instead?") is True
    assert corroborated(vague, "What does Section 11-116 define as salary?") is False


# ── A list of ages in one breath ───────────────────────────────────

class TestAgeLists:
    """"Show me the same at ages 57, 59 and 60" produced no comparison at all.

    Two independent reasons, nine turns into a conversation about exactly that.
    The scope gate's timing pattern matched "age 60" but not the plural "ages
    60", so the message was refused before any planning happened. And scenario
    extraction, once reached, read only the first age of a list.
    """

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("Now show me the same at ages 57, 59 and 60.", [57, 59, 60]),
            ("Now show me the same at age 57, age 59 and age 60.", [57, 59, 60]),
            ("compare ages 55 and 62", [55, 62]),
            ("Now do the same at age 59.", [59]),
        ],
    )
    def test_every_age_in_a_list_becomes_a_scenario(self, question, expected):
        found = [s["retirement_age"] for s in extract_scenarios(question)]

        assert sorted(found) == expected

    def test_a_list_of_numbers_that_are_not_ages_is_left_alone(self):
        assert extract_scenarios("I have 21.5 years of service and earn $72,000") == []


def test_a_follow_up_naming_an_age_carries_no_calculation_vocabulary():
    """"Now do the same at age 59" has no calculation word in it at all.

    What it does have is a retirement timing, and `extract_scenarios` finds it --
    so the follow-up is recognised by what it names rather than by a keyword
    list, which is why `understand_turn` reads a mid-conversation scenario as a
    request for the figure again however the sentence is phrased.
    """
    question = "Now do the same at age 59."

    assert extract_scenarios(question) == [
        {"label": "at age 59", "retirement_age": 59}
    ]
