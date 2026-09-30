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

    async def nothing_found(queries, **kwargs):
        return []

    monkeypatch.setattr(nodes, "lookup_recipe", no_recipe)
    monkeypatch.setattr(nodes, "derive_recipe", derive)
    monkeypatch.setattr(nodes, "retrieve", nothing_found)
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


@pytest.mark.asyncio
async def test_derivation_is_given_the_governing_articles_own_amount_provision(monkeypatch, derived) -> None:
    # The turn's search ran on "What would their annuity be at 67?" and found the
    # Tier 2 age rules, never §8-138 -- Article 8's own formula, whose 2.4% is in
    # (g-1). A derivation reads the Article's amount provision, searched for in
    # the Code's words and within that Article alone (Articles 1 and 20 bring
    # other funds' optional-benefit rates).
    from pension_chat.agent.retrieval import RetrievedSection

    searched: list[dict] = []

    async def retrieve(queries, **kwargs):
        searched.append({"queries": queries, **kwargs})
        return [RetrievedSection(section="8-138", article="8", title="Minimum annuities",
                                 text="(g-1) ... 2.4% if withdrawal is on or after January 1, 2002")]

    monkeypatch.setattr(nodes, "retrieve", retrieve)
    state = {**_state([{"label": "at age 67", "retirement_age": 67}]),
             "retrieved_sections": [{"section": "1-160", "article": "1", "text": "(c) age 67"}]}
    captured: list = []

    async def derive(formula_type, sections, facts):
        captured.append([s.section for s in sections])
        return SimpleNamespace(ok=False, cannot_derive="stub")

    monkeypatch.setattr(nodes, "derive_recipe", derive)
    await nodes.resolve_recipes(state)
    assert searched and searched[0]["articles"] == ["8"]
    assert any("final average salary" in q for q in searched[0]["queries"])
    assert captured == [["8-138", "1-160"]]          # the fund's own provision first, then the turn's


@pytest.mark.asyncio
async def test_a_found_registry_recipe_needs_no_extra_search(monkeypatch, derived) -> None:
    searched: list = []

    async def retrieve(queries, **kwargs):
        searched.append(queries)
        return []

    monkeypatch.setattr(nodes, "retrieve", retrieve)
    await nodes.resolve_recipes({**_state([]), "requires_calculation": False})
    assert searched == []
