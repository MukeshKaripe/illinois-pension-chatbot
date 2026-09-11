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
from pension_chat.agent.facts import age_on, aliases_of

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


# Tier 1 thresholds for a retirement annuity (age, years of service), each read
# from the Article's own eligibility section in the indexed text.
RETIREMENT_THRESHOLDS = {
    2: (55, 8),  # §2-119
    3: (50, 20),  # §3-111(a)
    4: (50, 20),  # §4-109(a)
    5: (50, 20),  # §5-132(e)
    6: (50, 20),  # §6-128(c)
    7: (55, 8),  # §7-141(a)
    8: (55, 10),  # §8-132
    9: (55, 10),  # §9-128
    10: (55, 10),  # §10-103: Article 9's annuities
    13: (55, 10),  # §13-301
    14: (60, 8),  # §14-107
    15: (55, 8),  # §15-135
    16: (55, 20),  # §16-132
    17: (55, 20),  # §17-116(a)
    18: (55, 10),  # §18-124(a)(2)-(3)
}


@pytest.mark.parametrize("article", sorted(RETIREMENT_THRESHOLDS))
def test_every_working_standard_member_can_retire_by_the_end_of_this_year(article) -> None:
    """The smoke question is "what if I retired at the end of this year?".

    A member who cannot yet retire under their fund's rules gets an honest "not
    yet", which says nothing about whether that fund's formula works -- so each
    standard member is past their fund's Tier 1 threshold.
    """
    member = next(p for p in personas.STANDARD if p.article_number == article)
    min_age, min_service = RETIREMENT_THRESHOLDS[article]
    assert member.tier == 1
    assert age_on(member.birth_date, date(2026, 12, 31)) >= min_age
    assert member.total_service_years >= min_service


def test_the_closed_fund_member_is_named() -> None:
    # Article 19 holds more than one closed fund; the persona says which.
    closed = next(p for p in personas.STANDARD if p.article_number == 19)
    assert "House of Correction" in closed.summary


# ── Fund-specific fields ──────────────────────────────────────────


def test_state_employees_say_whether_social_security_covers_them() -> None:
    # §14-108: a noncovered employee accrues 2.2% of final average compensation
    # a year, a covered one 1.67%. Nothing else on the record says which.
    sers = funds.FUNDS[14]
    assert [(spec.name, spec.type) for spec in sers.extra_fields] == [
        ("social_security_covered", "yesno")
    ]
    standard = next(p for p in personas.STANDARD if p.article_number == 14)
    assert dict(standard.extra_facts) == {"social_security_covered": True}


def test_police_and_fire_outside_chicago_name_their_salary_basis() -> None:
    # §3-111 and §4-109 pay half the salary attached to the rank, not an average.
    for article in (3, 4):
        salary = next(s for s in funds.FUNDS[article].fields if s.name == "current_annual_salary")
        assert salary.label == "Salary attached to rank"
    assert "salary_attached_to_rank" in aliases_of("current_annual_salary")


def test_an_extra_fact_is_edited_and_checked_against_its_fund() -> None:
    standard = next(p for p in personas.STANDARD if p.article_number == 14)
    # The form sends text; the fund's field type decides what is stored.
    edited = personas.apply_edits(standard, {"extra_facts": {"social_security_covered": "false"}})
    assert dict(edited.extra_facts) == {"social_security_covered": False}
    assert personas.validate(edited, today=TODAY) == []

    # An open endpoint writes this JSON, so an undeclared name is refused
    # rather than stored.
    stray = personas.apply_edits(standard, {"extra_facts": {"is_admin": True}})
    assert any("not a field for this fund" in p for p in personas.validate(stray, today=TODAY))

    garbled = personas.apply_edits(standard, {"extra_facts": {"social_security_covered": "maybe"}})
    assert any("yes or no" in p for p in personas.validate(garbled, today=TODAY))

    # A laborer's fund has no such field at all.
    laborer = personas.apply_edits(
        personas.ARTICLE_11_STANDARD, {"extra_facts": {"social_security_covered": True}}
    )
    assert personas.validate(laborer, today=TODAY) != []
