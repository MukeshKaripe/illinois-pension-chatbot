"""Tests for the per-Article profile config (spec §6, Phase 3b).

`funds.py` decides which Articles a member can have a profile under, what the
picker calls each fund, how far each field may be edited, and how much the
assistant claims to know about it. Every one of those is a promise shown to a
member, so the config is checked against the spec's table and against the rest
of the system rather than trusted.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from pension_chat import funds, personas
from pension_chat.agent.coverage import NON_MEMBERSHIP_ARTICLES
from pension_chat.agent.facts import aliases_of

TODAY = date(2026, 9, 9)

# Spec §6, Phase 3b: Article -> (employer group, member label).
SPEC_TABLE = {
    5: ("City of Chicago", "Chicago police officer"),
    6: ("City of Chicago", "Chicago firefighter"),
    8: ("City of Chicago", "Chicago municipal employee"),
    11: ("City of Chicago", "Chicago laborer"),
    12: ("City of Chicago", "Chicago Park District employee"),
    17: ("City of Chicago", "Chicago Public Schools teacher"),
    9: ("Cook County", "Cook County employee"),
    10: ("Cook County", "Forest Preserve District employee"),
    2: ("State of Illinois", "General Assembly member"),
    14: ("State of Illinois", "State employee"),
    15: ("State of Illinois", "State university employee"),
    16: ("State of Illinois", "Teacher outside Chicago"),
    18: ("State of Illinois", "Judge"),
    3: ("Local and regional", "Police officer outside Chicago"),
    4: ("Local and regional", "Firefighter outside Chicago"),
    7: ("Local and regional", "Municipal employee (IMRF)"),
    13: ("Local and regional", "Water Reclamation District employee"),
    19: ("Local and regional", "Closed fund member"),
}


def test_the_config_is_the_spec_table() -> None:
    assert {
        n: (fund.employer_group, fund.member_label) for n, fund in funds.FUNDS.items()
    } == SPEC_TABLE


def test_every_group_is_in_the_display_order() -> None:
    assert funds.GROUP_ORDER == (
        "City of Chicago",
        "Cook County",
        "State of Illinois",
        "Local and regional",
    )
    assert {fund.employer_group for fund in funds.FUNDS.values()} == set(funds.GROUP_ORDER)


def test_no_fund_is_an_article_nobody_can_belong_to() -> None:
    # Articles 1 and 20 govern everyone's pension and nobody's membership;
    # 21-24 are administrative. A profile filed under one is a record the
    # coverage layer would refuse as a destination.
    assert not {str(n) for n in funds.FUNDS} & NON_MEMBERSHIP_ARTICLES


def test_only_the_pilot_pair_starts_checked() -> None:
    checked = {n for n, fund in funds.FUNDS.items() if fund.support_level == "checked"}
    assert checked == {11, 12}
    assert {fund.support_level for fund in funds.FUNDS.values()} <= {"checked", "experimental"}


def test_every_extra_field_maps_into_the_engine() -> None:
    # The rule for `extra_fields`: a field exists because a recipe reads it,
    # and a recipe can only read it if `facts.py` knows another name for it.
    for fund in funds.FUNDS.values():
        for spec in fund.extra_fields:
            assert len(aliases_of(spec.name)) > 1, (fund.article, spec.name)


def test_every_fund_serves_the_five_common_fields_with_its_own_bounds() -> None:
    judge = funds.FUNDS[18]
    names = [spec.name for spec in judge.fields]
    assert names == list(personas.EDITABLE_FIELDS)
    salary = next(spec for spec in judge.fields if spec.name == "current_annual_salary")
    assert (salary.type, salary.max) == ("money", judge.salary_max)
    status = next(spec for spec in judge.fields if spec.name == "employment_status")
    assert status.options == personas.EMPLOYMENT_STATUSES


def test_support_level_reads_the_article_however_it_arrives() -> None:
    # Coverage carries the Article as a string, the record as an integer.
    assert funds.support_level("11") == "checked"
    assert funds.support_level(5) == "experimental"
    assert funds.support_level(None) is None
    assert funds.support_level("20") is None
    assert funds.support_level("not a number") is None


# ── The standard personas ─────────────────────────────────────────


def test_exactly_one_standard_persona_per_fund() -> None:
    assert sorted(p.article_number for p in personas.STANDARD) == sorted(funds.FUNDS)
    # 11 and 12 keep the members the benchmark already binds to.
    assert personas.ARTICLE_11_STANDARD in personas.STANDARD
    assert personas.ARTICLE_12_STANDARD in personas.STANDARD


@pytest.mark.parametrize("persona", personas.PERSONAS, ids=lambda p: p.key)
def test_every_persona_is_coherent_under_its_own_funds_bounds(persona) -> None:
    assert personas.validate(persona, today=TODAY) == []


def test_every_persona_belongs_to_a_fund() -> None:
    assert {p.article_number for p in personas.PERSONAS} <= set(funds.FUNDS)


def test_the_closed_fund_persona_is_an_existing_member() -> None:
    # A closed fund admits nobody new, so its member joined long ago.
    closed = next(p for p in personas.STANDARD if p.article_number == 19)
    assert closed.employment_status == "retired"
    assert closed.hire_date < date(1990, 1, 1)


def test_a_bound_can_differ_by_article() -> None:
    # Hired at 22: an ordinary start for a laborer, not for a judge.
    laborer = replace(
        personas.ARTICLE_11_STANDARD,
        hire_date=date(1992, 6, 1),
        total_service_years=Decimal("20.00"),
    )
    assert personas.validate(laborer, today=TODAY) == []

    judge = next(p for p in personas.STANDARD if p.article_number == 18)
    young_judge = replace(judge, hire_date=date(judge.birth_date.year + 22, 12, 1))
    problems = personas.validate(young_judge, today=TODAY)
    assert any("minimum here is 25" in problem for problem in problems), problems


def test_an_article_without_a_fund_has_no_profiles() -> None:
    stray = replace(personas.ARTICLE_11_STANDARD, article_number=20)
    assert any("no profiles" in p for p in personas.validate(stray, today=TODAY))
