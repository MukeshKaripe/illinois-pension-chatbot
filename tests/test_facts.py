"""Tests for the deterministic member-facts layer.

These are the values every calculation is built on, and they were mocked
(``age = 55``) in the first implementation, so they get exact assertions rather
than smoke checks.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pension_chat.agent.facts import (
    age_on,
    build_member_facts,
    highest_average_salary,
    months_between,
    nth_birthday,
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
HISTORY = [
    (date(2018, 1, 1), Decimal("58000.00")),
    (date(2019, 1, 1), Decimal("59740.00")),
    (date(2020, 1, 1), Decimal("61532.20")),
    (date(2021, 1, 1), Decimal("63378.17")),
    (date(2022, 1, 1), Decimal("65279.51")),
    (date(2023, 1, 1), Decimal("67237.89")),
    (date(2024, 1, 1), Decimal("69255.03")),
    (date(2025, 1, 1), Decimal("71332.68")),
    (date(2026, 1, 1), Decimal("72000.00")),
]


@pytest.mark.parametrize(
    ("birth", "on", "expected"),
    [
        (date(1970, 5, 15), date(2026, 9, 7), 56),
        (date(1970, 5, 15), date(2026, 5, 15), 56),   # birthday itself
        (date(1970, 5, 15), date(2026, 5, 14), 55),   # day before
        (date(2000, 2, 29), date(2026, 2, 28), 25),   # leap-day birth
    ],
)
def test_age_on(birth, on, expected):
    assert age_on(birth, on) == expected


def test_months_between_counts_whole_months():
    # 2026-09-07 to 2030-05-15 is 44 whole months, not 4 years x 12.
    assert months_between(date(2026, 9, 7), date(2030, 5, 15)) == 44
    assert months_between(date(2026, 9, 7), date(2026, 9, 7)) == 0
    assert months_between(date(2026, 9, 20), date(2026, 10, 15)) == 0


def test_nth_birthday_handles_leap_day():
    assert nth_birthday(date(1970, 5, 15), 60) == date(2030, 5, 15)
    assert nth_birthday(date(2000, 2, 29), 25) == date(2025, 2, 28)


def test_highest_average_salary_picks_the_best_window():
    # The best 4 consecutive years are the last four: 2023-2026.
    expected = (
        Decimal("67237.89") + Decimal("69255.03") + Decimal("71332.68") + Decimal("72000.00")
    ) / 4
    assert highest_average_salary(HISTORY, 4, 10, TODAY) == expected


def test_highest_average_salary_returns_none_when_history_is_too_short():
    # A missing input, not a zero — the statute asks for 4 consecutive years.
    assert highest_average_salary(HISTORY[:2], 4, 10, TODAY) is None
    assert highest_average_salary([], 4, 10, TODAY) is None


def test_highest_average_salary_ignores_years_outside_the_window():
    # Only the last 10 years of service count under 11-134(a).
    old = [(date(1990, 1, 1), Decimal("999999"))] + HISTORY
    assert highest_average_salary(old, 4, 10, TODAY) == highest_average_salary(
        HISTORY, 4, 10, TODAY
    )


def test_build_member_facts_computes_real_age_and_reduction_months():
    facts = build_member_facts(EMPLOYEE, HISTORY, today=TODAY)

    assert facts["age"] == 56
    assert facts["service_years"] == 21.5
    assert facts["months_before_age_60"] == 44
    assert facts["age_60_date"] == "2030-05-15"
    assert facts["final_average_salary"] == pytest.approx(69956.4)


def test_build_member_facts_publishes_aliases_for_the_same_quantity():
    facts = build_member_facts(EMPLOYEE, HISTORY, today=TODAY)

    # A derived recipe picks its own input names; the aliases are what let it
    # resolve without a model call.
    for alias in ("age", "current_age", "age_at_withdrawal", "attained_age"):
        assert facts[alias] == 56
    for alias in ("months_before_age_60", "months_before_60", "full_months_under_age_60"):
        assert facts[alias] == 44


def test_scenario_facts_advance_age_and_accrue_service():
    at_60 = build_member_facts(EMPLOYEE, HISTORY, today=TODAY, assumed_retirement_age=60)

    assert at_60["age"] == 60
    assert at_60["as_of_date"] == "2030-05-15"
    # No early-retirement reduction applies once the member reaches 60.
    assert at_60["months_before_age_60"] == 0
    # Service keeps accruing while the member remains in service.
    assert at_60["service_years"] > 21.5


def test_scenario_does_not_accrue_service_for_a_separated_member():
    separated = {**EMPLOYEE, "employment_status": "separated"}
    at_60 = build_member_facts(separated, HISTORY, today=TODAY, assumed_retirement_age=60)

    assert at_60["service_years"] == 21.5


def test_anonymous_member_yields_no_facts_to_calculate_from():
    facts = build_member_facts(None, today=TODAY)

    assert facts == {"as_of_date": "2026-09-07"}


def test_missing_birth_date_does_not_fabricate_an_age():
    facts = build_member_facts({**EMPLOYEE, "birth_date": None}, HISTORY, today=TODAY)

    assert "age" not in facts


def test_fund_specific_facts_reach_the_engine_under_every_name() -> None:
    from datetime import date as _date

    from pension_chat.agent.facts import build_member_facts

    facts = build_member_facts(
        {
            "birth_date": "1965-01-25",
            "hire_date": "2005-07-01",
            "employment_status": "active",
            "total_service_years": 21,
            "current_annual_salary": 70000,
            "extra_facts": {"social_security_covered": True},
        },
        [],
        today=_date(2026, 9, 9),
    )
    # A state employee's coverage, under the names a derivation of §14-108 may pick.
    assert facts["social_security_covered"] is True
    assert facts["covered_by_social_security"] is True
    # §3-111's basis is the salary itself, under the statute's own name.
    assert facts["salary_attached_to_rank"] == facts["current_annual_salary"] == 70000.0


def test_a_growth_rate_raises_the_projected_salary():
    """Flat is the honest default and stays the default.

    But a member who tells you their raises is supplying a known rule, not
    asking you to invent one -- and "assume I get a 3% raise each year" came
    back with the same figure and a sentence saying it changed nothing.
    """
    flat = build_member_facts(EMPLOYEE, HISTORY, today=TODAY, assumed_retirement_age=60)
    grown = build_member_facts(
        EMPLOYEE,
        HISTORY,
        today=TODAY,
        assumed_retirement_age=60,
        salary_growth=Decimal("0.03"),
    )

    assert Decimal(str(grown["final_average_salary"])) > Decimal(
        str(flat["final_average_salary"])
    )
    assert "3" in str(grown["salary_basis"]), "the rule used must be disclosed"


def test_no_growth_rate_is_the_flat_projection_unchanged():
    a = build_member_facts(EMPLOYEE, HISTORY, today=TODAY, assumed_retirement_age=60)
    b = build_member_facts(
        EMPLOYEE, HISTORY, today=TODAY, assumed_retirement_age=60, salary_growth=None
    )
    assert a == b
    assert "not projected" in str(a["salary_basis"])
