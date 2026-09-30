"""A recipe is found for the retirement the question is about, not for today.

`compute_benefit(retirement_age=67)` reached derivation with the member's facts
as of today, so a 58-year-old Tier 2 member was derived for as "an annuity
at 58" -- and derivation, reading §1-160, correctly found none: "no computable
annuity at age 58" on every Tier 2 turn of the 2026-09-30 smoke run. The same
today-dated facts fed the registry's applicability check, so a saved recipe for
retirement at 67 would have been judged not to reach a 58-year-old and
re-derived on every turn. Execution already evaluated each scenario at its own
age; the recipe step now does too.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from pension_chat.agent import nodes
from pension_chat.agent.facts import build_member_facts

TODAY = date.today()
RECORD = {
    "id": "tier2_standard", "article_number": 8, "tier": 2,
    "birth_date": date(TODAY.year - 58, 1, 1).isoformat(), "hire_date": "2013-09-30",
    "employment_status": "active", "total_service_years": 12.5,
    "current_annual_salary": 84000.0, "extra_facts": {},
}
HISTORY = [{"effective_date": f"{y}-01-01", "annual_salary": 84000.0} for y in range(TODAY.year - 3, TODAY.year + 1)]


def _state(scenarios: list[dict]) -> dict:
    facts = build_member_facts(
        RECORD, [(date.fromisoformat(h["effective_date"]), Decimal(str(h["annual_salary"]))) for h in HISTORY]
    )
    return {
        "requires_calculation": True, "formula_types_needed": ["retirement_annuity"],
        "retrieved_sections": [], "employee_record": RECORD, "salary_history": HISTORY,
        "member_facts": facts, "scenarios": scenarios,
        "coverage": {"article": "8", "basis": "record", "record_article": "8"},
    }


@pytest.fixture
def derived(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    async def no_recipe(*args, **kwargs):
        return None

    async def derive(formula_type, sections, facts):
        seen.append(dict(facts))
        return SimpleNamespace(ok=False, cannot_derive="stub")

    monkeypatch.setattr(nodes, "lookup_recipe", no_recipe)
    monkeypatch.setattr(nodes, "derive_recipe", derive)
    return seen


@pytest.mark.asyncio
async def test_derivation_sees_the_member_at_the_scenarios_retirement_age(derived) -> None:
    await nodes.resolve_recipes(_state([{"label": "at age 67", "retirement_age": 67}]))
    [facts] = derived
    assert facts["age_at_annuity_start"] == 67
    assert facts["service_years"] > 12.5            # service accrues to the retirement date


@pytest.mark.asyncio
async def test_without_a_scenario_derivation_sees_the_member_today(derived) -> None:
    await nodes.resolve_recipes(_state([]))
    [facts] = derived
    assert facts["age_at_annuity_start"] == 58


@pytest.mark.asyncio
async def test_a_described_employee_is_derived_for_at_the_age_asked_about(derived) -> None:
    # No record: the facts are what the user stated; the scenario still sets
    # the age the annuity would start at.
    state = {**_state([{"label": "at age 62", "retirement_age": 62}]),
             "employee_record": None, "salary_history": [], "member_facts": {},
             "coverage": {"article": "8", "basis": "record", "record_article": None},
             "fact_ledger": {}}
    await nodes.resolve_recipes(state)
    [facts] = derived
    assert facts["age_at_annuity_start"] == 62
