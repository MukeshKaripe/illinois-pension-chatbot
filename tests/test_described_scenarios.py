"""A described employee's what-if: "they are 62 ... what if they wait until 67?"

Live, 2026-10-01 (S4b-002): with no record the scenario was dropped as needing
one, and the answer put the age-62 figure under "At age 67". A stated age is
enough to move the retirement date; the user's own salary and service stand.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from pension_chat.agent import nodes
from pension_chat.calculation.core_meabf import CORE_FORMULAS

TIER_2 = next(f for f in CORE_FORMULAS if f["tier"] == 2)


def _state(scenarios):
    today = date.today()
    born = (today.replace(year=today.year - 62) - timedelta(days=182)).isoformat()
    return {
        "employee_record": None, "member_facts": {}, "salary_history": [],
        "recipes": {"retirement_annuity": {"spec": TIER_2["spec"], "section": "8-138"}},
        "resolved_inputs": {"final_average_salary": 80000, "service_years": 11,
                            "birth_date": born, "annuity_start_date": today.isoformat()},
        "scenarios": scenarios, "can_execute": True,
    }


async def test_a_described_employee_is_priced_at_the_age_asked_about() -> None:
    out = await nodes.execute_calculations(_state([{"label": "at age 67", "retirement_age": 67}]))
    entry = out["calculation_result"]["retirement_annuity"]
    assert Decimal(entry["base"]["annual_amount"]) < 21120          # reduced at 62
    (at_67,) = entry["scenarios"]
    assert Decimal(at_67["annual_amount"]) == 21120                 # 80,000 x 2.4% x 11, unreduced
    assert Decimal(str(at_67["assumptions"]["service_years"])) == 11     # what they said stands


async def test_retiring_younger_is_priced_as_if_they_were_that_age() -> None:
    # Live, 2026-10-01: a described 65-year-old asked "what if I retire 5
    # years early?" and was refused as "already older than 60"; the answer then
    # re-showed the age-65 figures under that heading.
    tier_1 = next(f for f in CORE_FORMULAS if f["tier"] == 1)
    state = {
        "employee_record": None, "member_facts": {}, "salary_history": [], "can_execute": True,
        "recipes": {"retirement_annuity": {"spec": tier_1["spec"], "section": "8-138"}},
        "resolved_inputs": {"final_average_salary": 85000, "service_years": 25,
                            "birth_date": (date.today().replace(year=date.today().year - 65)
                                           - timedelta(days=182)).isoformat()},
        "scenarios": [{"label": "5 years earlier", "years_from_now": -5}],
    }
    entry = (await nodes.execute_calculations(state))["calculation_result"]["retirement_annuity"]
    (at_60,) = entry["scenarios"]
    assert Decimal(at_60["annual_amount"]) == 51000      # 85,000 x 2.4% x 25; (g-1) has no reduction at 60


async def test_a_younger_age_the_formula_does_not_reach_is_said_not_priced() -> None:
    out = await nodes.execute_calculations(_state([{"label": "at age 55", "retirement_age": 55}]))
    assert not out["calculation_result"]["retirement_annuity"].get("scenarios")
    reasons = [u["reason"] for u in out.get("unresolved_scenarios") or [] if u["label"] == "at age 55"]
    assert reasons and "does not apply" in reasons[0]   # Tier 2 begins no earlier than 62


def test_an_earlier_retirement_is_labelled_as_earlier() -> None:
    from pension_chat.agent.tools import _scenario

    assert _scenario({"years_from_now": -5})[0]["label"] == "5 years earlier"
    assert _scenario({"years_from_now": 2})[0]["label"] == "2 more years"


async def test_retiring_now_is_not_priced_where_the_formula_does_not_pay_yet() -> None:
    # Live, 2026-10-01: a Tier 2 member of 61 was shown "Retiring now: $8,040"
    # beside the age-66 figure -- Tier 2 cannot begin before 62. The base is
    # now tested at today's age, and the what-if still prices.
    today = date.today()
    born = (today.replace(year=today.year - 61) - timedelta(days=182)).isoformat()
    state = {
        "employee_record": None, "member_facts": {}, "salary_history": [], "can_execute": True,
        "recipes": {"retirement_annuity": {"spec": TIER_2["spec"], "section": "8-138"}},
        "resolved_inputs": {"final_average_salary": 50000, "service_years": 10,
                            "birth_date": born, "annuity_start_date": today.isoformat()},
        "scenarios": [{"label": "at age 66", "retirement_age": 66}],
    }
    entry = (await nodes.execute_calculations(state))["calculation_result"]["retirement_annuity"]
    assert entry["base"]["ok"] is False and "62" in entry["base"]["not_applicable"]
    (at_66,) = entry["scenarios"]
    assert Decimal(at_66["annual_amount"]) > 0


def test_a_card_is_shown_for_the_what_if_when_retiring_now_does_not_pay() -> None:
    from pension_chat.agent.card import calculation_card
    from pension_chat.agent.worksheet import WorkSheet

    state = {"calculation_result": {"retirement_annuity": {
        "formula_name": "Tier 2", "section": "8-138",
        "base": {"ok": False, "not_applicable": "retiring at 62 or over (member: 61)"},
        "scenarios": [{"label": "at age 66", "annual_amount": "11280.00", "monthly_amount": "940.00",
                       "inputs_used": {"service_years": 10}}],
    }}, "input_sources": {"service_years": "stated"}}
    card = calculation_card(state, WorkSheet.from_state(state))
    assert card is not None and card["annual_amount"] is None
    assert card["scenarios"][0]["annual_amount"] == "11280.00"
    assert "62" in (card.get("not_payable_now") or "")


def test_the_tool_result_says_retiring_now_does_not_pay_and_gives_the_what_if() -> None:
    from pension_chat.agent.tools import _describe

    text = _describe({"retirement_annuity": {
        "section": "8-138",
        "base": {"ok": False, "not_applicable": "retiring at 62 or over (member: 61)"},
        "scenarios": [{"label": "at age 66", "annual_amount": "11280.00"}],
    }}, [], None, [])
    assert "not payable now" in text and "62" in text
    assert "at age 66: 11280.00 a year" in text
