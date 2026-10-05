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


# ── The member at the annuity start, not the member today ─────────


def _active(**over):
    base = {
        "article_number": 3,
        "tier": 1,
        "birth_date": date(1968, 5, 15),
        "hire_date": date(1996, 3, 1),
        "employment_status": "active",
        "current_annual_salary": Decimal("90000"),
        # A record states its service; one that does not leaves it unknown.
        "total_service_years": 30,
    }
    base.update(over)
    return base


def test_employment_status_describes_the_annuity_start():
    """F03, F04 and F14, and the same defect CLAUDE.md already records for
    Article 19.

    Every fact in this dict describes the member at `as_of` -- service accrues
    to it, age is taken at it, `withdrawal_date` and `annuity_start_date` *are*
    it. `employment_status` alone passed the record's value straight through,
    so a derivation reading Section 3-111(a) ("no longer in service") against
    an active record concluded, correctly, that the provision does not reach
    them -- on a question that begins "if I retired".

    Article 11's recipe never tests status, which is why this stayed invisible
    for so long.
    """
    facts = build_member_facts(_active(), [], today=date(2026, 9, 14))
    assert facts["employment_status"] == "separated"
    assert facts["employment_status_on_record"] == "active"


def test_a_member_already_retired_is_left_alone():
    facts = build_member_facts(
        _active(employment_status="retired"), [], today=date(2026, 9, 14)
    )
    assert facts["employment_status"] == "retired"
    assert facts["employment_status_on_record"] == "retired"


def test_the_record_is_still_reachable_for_a_provision_about_being_in_service():
    """Section 11-128 covers "employees who attain age 65 in service". A
    provision about the member's present employment has to be able to test it,
    which is why the record's value keeps a name of its own rather than being
    overwritten."""
    facts = build_member_facts(_active(), [], today=date(2026, 9, 14))
    assert facts["employment_status_on_record"] == "active"


def test_service_still_accrues_to_a_posited_retirement():
    """The separation is at `as_of`, not before it -- so service keeps
    accruing up to the date the member says they would retire."""
    now = build_member_facts(_active(), [], today=date(2026, 9, 14))
    later = build_member_facts(
        _active(), [], today=date(2026, 9, 14), assumed_retirement_age=60
    )
    assert Decimal(str(later["service_years"])) > Decimal(str(now["service_years"]))
    assert later["employment_status"] == "separated"


def test_the_stopped_working_status_is_not_a_second_assumption():
    """It is derived from the withdrawal date, not guessed beside it.

    Making it a framing default meant the guardrail demanded its own
    disclosure, and F04 was thrown away on both agents for
    ASSUMPTION_NOT_DISCLOSED -- an answer that had disclosed "the date you stop
    working, taken as today" and then said nothing about the status that
    follows from it. `render_assumptions` exists precisely to stop one choice
    being reported as two.
    """
    from pension_chat.agent.facts import FRAMING_DEFAULTS

    assert "employment_status" not in FRAMING_DEFAULTS
    assert "withdrawal_date" in FRAMING_DEFAULTS


# ── Tier 2 and 3 final average salary: §1-160(b) ──────────────────────
#
# "the 96 consecutive months (or 8 consecutive years) of service within the
# last 120 months (or 10 years) of service in which the total salary ... was
# the highest". The generic name used to carry the 4-year average for every
# member -- Article 11's and Article 8 Tier 1's rule -- so a Tier 2 MEABF figure
# came out on the wrong salary basis.


def _tiered(tier: int, hire: date) -> dict:
    return _active(tier=tier, hire_date=hire, article_number=8)


def _rising(years: int, last: int = 2026) -> list[tuple[date, Decimal]]:
    # 50,000 rising 5,000 a year, so every window has a different average.
    return [(date(last - years + 1 + i, 1, 1), Decimal(50000 + 5000 * i)) for i in range(years)]


def test_a_tier_2_members_final_average_salary_is_the_best_8_of_the_last_10_years():
    history = _rising(10)
    facts = build_member_facts(_tiered(2, date(2013, 6, 1)), history, today=date(2026, 9, 14))
    best_8 = sum(Decimal(50000 + 5000 * i) for i in range(2, 10)) / 8
    best_4 = sum(Decimal(50000 + 5000 * i) for i in range(6, 10)) / 4
    assert Decimal(str(facts["final_average_salary"])) == best_8
    assert Decimal(str(facts["final_average_salary_8_year"])) == best_8
    assert Decimal(str(facts["highest_average_annual_salary_4yr"])) == best_4   # still there, by its own name


def test_a_tier_3_member_uses_the_same_rule():
    facts = build_member_facts(_tiered(3, date(2018, 6, 1)), _rising(10), today=date(2026, 9, 14))
    assert facts["final_average_salary"] == facts["final_average_salary_8_year"]


def test_a_tier_2_member_without_8_years_of_salary_has_no_final_average_salary():
    # Unknown, to be asked for -- never the 4-year figure in its place.
    facts = build_member_facts(_tiered(2, date(2020, 6, 1)), _rising(4), today=date(2026, 9, 14))
    assert "final_average_salary" not in facts and "fas" not in facts
    assert "highest_average_annual_salary_4yr" in facts


def test_a_tier_1_member_keeps_the_4_year_average():
    history = _rising(10)
    facts = build_member_facts(_tiered(1, date(1996, 3, 1)), history, today=date(2026, 9, 14))
    assert facts["final_average_salary"] == facts["highest_average_annual_salary_4yr"]


def test_the_generic_final_average_salary_label_names_no_window():
    # It is 4 years for Tier 1 and 8 for Tier 2; a label naming either is wrong
    # for the other. The windowed names say their own window.
    from pension_chat.agent.blocks import _label

    assert "4 years" not in _label("final_average_salary")
    assert "8" in _label("final_average_salary_8_year")
    assert "4 years" in _label("highest_average_annual_salary_4yr")
