"""Tests for retirement-timing scenario handling.

The bug these pin down: `plan_request` runs concurrently with the record fetch,
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
from pension_chat.agent.nodes import (
    _merge_scenarios,
    _resolve_retirement_age,
    extract_scenarios,
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
    merged = _merge_scenarios(deterministic, planned)

    assert merged == deterministic


def test_merge_keeps_planner_scenarios_the_regex_missed():
    merged = _merge_scenarios([], [{"label": "at age 62", "retirement_age": 62}])

    assert merged == [{"label": "at age 62", "retirement_age": 62}]


def test_merge_keeps_unresolvable_scenarios_when_nothing_else_matched():
    # Kept so they can be reported to the member rather than vanishing.
    planned = [{"label": "much later", "retirement_age": None}]
    assert _merge_scenarios([], planned) == planned


def test_merge_deduplicates_the_same_target():
    merged = _merge_scenarios(
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
