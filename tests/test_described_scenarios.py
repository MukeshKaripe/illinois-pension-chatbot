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


async def test_an_age_already_passed_is_said_not_priced() -> None:
    out = await nodes.execute_calculations(_state([{"label": "at age 55", "retirement_age": 55}]))
    assert not out["calculation_result"]["retirement_annuity"].get("scenarios")
    assert any(u["label"] == "at age 55" for u in out.get("unresolved_scenarios") or [])
