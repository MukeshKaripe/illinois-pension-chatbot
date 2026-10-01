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


@pytest.mark.asyncio
async def test_validation_checks_applicability_at_the_scenarios_age(monkeypatch) -> None:
    # §8-138(g-1): "no earlier than upon attainment of age 60". Asked about 67,
    # a 58-year-old was blocked -- "requires age 60, but this member has 58".
    async def no_texts(section_ids, articles=None):
        return {}

    monkeypatch.setattr(nodes, "fetch_sections", no_texts)
    spec = {
        "formula_id": "article_8_retirement_annuity_8-138", "execution_type": "declarative",
        "inputs": [{"name": "final_average_salary", "type": "money", "required": True}],
        "parameters": {},
        "calculation": {"variable": "final_average_salary"},
        "applicability_predicates": [{
            "fact": "age_at_annuity_start", "operator": "ge", "value": 60,
            "description": "annuity begins no earlier than age 60",
            "source": {"section": "8-138", "subsection": "(g-1)"},
        }],
        "output_name": "annual_annuity", "output_type": "money",
    }
    state = {**_state([{"label": "at age 67", "retirement_age": 67}]),
             "recipes": {"retirement_annuity": {"source": "derived", "spec": spec,
                                                "source_sections": ["8-138"], "section": "8-138"}},
             "retrieved_sections": [{"section": "8-138"}],
             "resolved_inputs": {"final_average_salary": 80400.84, "age_at_annuity_start": 58}}
    def codes(out: dict) -> list[str]:
        return [e["code"] for e in out["validation_errors"]]

    at_67 = await nodes.validate_recipes(state)
    assert "APPLICABILITY_NOT_SATISFIED" not in codes(at_67), at_67["validation_errors"]

    today = await nodes.validate_recipes({**state, "scenarios": []})
    assert "APPLICABILITY_NOT_SATISFIED" in codes(today)     # at 58, today, it does not reach


@pytest.mark.asyncio
async def test_a_described_employees_own_words_reach_the_derivation(derived) -> None:
    # "An employee who joined in 2015 is 62, earns $80,000 and has 11 years" was
    # derived for with only {age_at_annuity_start: 62}: stated values are
    # extracted after a recipe exists, so the derivation could not tell which
    # provision applied and declined. With no record, it reads what was said.
    from langchain_core.messages import HumanMessage

    said = "An employee who joined in 2015 is 62, earns $80,000 and has 11 years of service."
    state = {**_state([{"label": "at age 62", "retirement_age": 62}]),
             "employee_record": None, "salary_history": [], "member_facts": {}, "fact_ledger": {},
             "question": said, "messages": [HumanMessage(content=said)],
             "coverage": {"article": "8", "basis": "deployment", "record_article": None}}
    await nodes.resolve_recipes(state)
    [facts] = derived
    assert "joined in 2015" in facts["described_in_conversation"]


@pytest.mark.asyncio
async def test_a_member_with_a_record_is_not_given_the_conversation_as_a_fact(derived) -> None:
    await nodes.resolve_recipes(_state([]))
    [facts] = derived
    assert "described_in_conversation" not in facts


@pytest.mark.parametrize("said, tiers", [
    ("An employee who joined in 2015 is 62 and earns $80,000.", [2]),
    ("She was hired in 2004 and has 22 years.", [1]),
    ("They became a member in 2019.", [3]),
    ("Joined in 2017, age 45.", [2, 3]),          # before or after July 6, 2017 -- both
    ("An employee is 56 with 18 years of service.", [1, 2]),   # said nothing about when
])
def test_a_described_employees_joining_year_decides_the_tier(said, tiers) -> None:
    assert nodes._described_tiers(said, article=8) == tiers


def test_tier_3_is_article_8_and_11_only() -> None:
    assert nodes._described_tiers("joined in 2019", article=9) == [2]


@pytest.mark.asyncio
async def test_resolve_recipes_looks_up_only_the_stated_tier(monkeypatch, derived) -> None:
    from langchain_core.messages import HumanMessage

    looked: list = []

    async def lookup(formula_type, article, tier):
        looked.append(tier)
        return None

    monkeypatch.setattr(nodes, "lookup_recipe", lookup)
    said = "An employee who joined in 2015 is 62, earns $80,000 and has 11 years of service."
    state = {**_state([]), "employee_record": None, "salary_history": [], "member_facts": {},
             "fact_ledger": {}, "question": said, "messages": [HumanMessage(content=said)],
             "coverage": {"article": "8", "basis": "deployment", "record_article": None}}
    await nodes.resolve_recipes(state)
    assert looked == [2]


def _age_rule(minimum: int) -> dict:
    return {
        "formula_id": f"f{minimum}", "execution_type": "declarative",
        "inputs": [{"name": "final_average_salary", "type": "money", "required": True}],
        "parameters": {},
        "calculation": {"variable": "final_average_salary"},
        "applicability_predicates": [{"fact": "service_years", "operator": "ge", "value": minimum,
                                      "description": f"{minimum} or more years",
                                      "source": {"section": "8-138", "subsection": "(b)"}}],
        "output_name": "annual_annuity", "output_type": "money",
    }


@pytest.mark.asyncio
async def test_one_ruled_out_branch_does_not_block_the_other(monkeypatch) -> None:
    # Tier unknown: the Tier 1 formula (20 years) is ruled out at 11 years, and
    # used to block the turn although the Tier 2 formula (10 years) applies.
    async def no_texts(section_ids, articles=None):
        return {}

    monkeypatch.setattr(nodes, "fetch_sections", no_texts)
    recipes = {
        "retirement_annuity@tier1": {"source": "registry", "spec": _age_rule(20), "source_sections": ["8-138"]},
        "retirement_annuity@tier2": {"source": "registry", "spec": _age_rule(10), "source_sections": ["8-138"]},
    }
    state = {**_state([]), "employee_record": None, "member_facts": {}, "fact_ledger": {},
             "recipes": recipes, "retrieved_sections": [{"section": "8-138"}],
             "resolved_inputs": {"final_average_salary": 80000, "service_years": 11}}
    out = await nodes.validate_recipes(state)
    assert out["can_execute"] is True and out["execution_blocked"] is None
    assert recipes["retirement_annuity@tier1"]["blocked"] == "applicability_not_satisfied"
    assert "blocked" not in recipes["retirement_annuity@tier2"]


@pytest.mark.asyncio
async def test_when_every_branch_is_ruled_out_the_turn_is_blocked(monkeypatch) -> None:
    async def no_texts(section_ids, articles=None):
        return {}

    monkeypatch.setattr(nodes, "fetch_sections", no_texts)
    recipes = {"retirement_annuity": {"source": "registry", "spec": _age_rule(20), "source_sections": ["8-138"]}}
    state = {**_state([]), "employee_record": None, "member_facts": {}, "fact_ledger": {},
             "recipes": recipes, "retrieved_sections": [{"section": "8-138"}],
             "resolved_inputs": {"final_average_salary": 80000, "service_years": 11}}
    out = await nodes.validate_recipes(state)
    assert out["can_execute"] is False and out["execution_blocked"] == "applicability_not_satisfied"


@pytest.mark.asyncio
async def test_execution_skips_a_ruled_out_branch_and_runs_the_other() -> None:
    def recipe(name: str, blocked: str | None = None) -> dict:
        r = {"source": "registry", "formula_name": name, "section": "8-138",
             "spec": {"formula_id": name, "execution_type": "declarative",
                      "inputs": [{"name": "final_average_salary", "type": "money", "required": True}],
                      "parameters": {"rate": {"value": 0.5, "source": {"section": "8-138"}}},
                      "calculation": {"operation": "multiply",
                                      "operands": [{"variable": "rate"}, {"variable": "final_average_salary"}]},
                      "output_name": "annual_annuity", "output_type": "money"}}
        if blocked:
            r["blocked"] = blocked
        return r

    state = {**_state([]), "can_execute": True,
             "recipes": {"retirement_annuity@tier1": recipe("t1", "applicability_not_satisfied"),
                         "retirement_annuity@tier2": recipe("t2")},
             "resolved_inputs": {"final_average_salary": 80000}}
    out = await nodes.execute_calculations(state)
    assert list(out["calculation_result"]) == ["retirement_annuity@tier2"]


@pytest.mark.parametrize("said, salary", [
    ("An employee who joined in 2015 is 62, earns $80,000 and has 11 years.", 80000),
    ("Their salary is 72,500 a year.", 72500),
    ("She makes $95000 annually and is 58.", 95000),
    ("An employee aged 60 with 24 years.", None),
    ("They retired on January 1, 2002 at age 60.", None),     # a date is not a salary
    ("If an employee earns $75,000, joined in 1998 and is 58.", 75000),   # a comma after the amount
    ("A Tier 3 employee aged 64 with 10 years and a $65,000 salary.", 65000),   # amount before the word
    ("They are paid $58,250 a year.", 58250),
])
def test_a_stated_annual_salary_is_read_from_the_users_words(said, salary) -> None:
    assert nodes._stated_annual_salary(said) == salary


@pytest.mark.parametrize("said, age", [
    ("An employee who joined in 2015 is 62, earns $80,000 and has 11 years.", 62),
    ("An employee aged 56 with 18 years of service.", 56),
    ("A 58-year-old with 27 years.", 58),
    ("Someone age 60 who joined in 2001.", 60),
    ("An employee with 11 years of service earning $80,000.", None),   # service is not an age
    ("They have 30 years and joined in 1996.", None),
    ("a person with 10 years of service at 40 years of age", 40),
    ("what if they are 65, then how is the pension calculated?", 65),
    ("I'm 58 with 20 years.", 58),
    ("years of service are 25 and final average salary is $85k", None),
])
def test_a_stated_age_is_read_from_the_users_words(said, age) -> None:
    assert nodes._stated_age(said) == age


@pytest.mark.asyncio
async def test_an_age_read_from_the_users_words_is_not_also_reported_missing(monkeypatch) -> None:
    # Live, 2026-09-30: resolved {"age": 62 (stated)} and missing ["age"] on the
    # same turn -- the missing list was drawn up before the age was read.
    from langchain_core.messages import HumanMessage

    async def heard_nothing(**kwargs):
        return '{"resolved": {}}'

    monkeypatch.setattr(nodes, "complete", heard_nothing)
    said = "An employee who joined in 2015 is 62, earns $80,000 and has 11 years of service."
    spec = {"formula_id": "f", "execution_type": "declarative",
            "inputs": [{"name": "age", "type": "integer", "required": True}],
            "parameters": {}, "calculation": {"variable": "age"},
            "output_name": "annual_annuity", "output_type": "money"}
    state = {"employee_record": None, "member_facts": {}, "fact_ledger": {}, "salary_history": [],
             "question": said, "messages": [HumanMessage(content=said)], "turn_index": 1,
             "coverage": {"article": "8", "basis": "deployment", "record_article": None},
             "recipes": {"retirement_annuity": {"source": "registry", "spec": spec}},
             "requires_calculation": True, "assumptions": {}}
    out = await nodes.resolve_inputs(state)
    assert out["resolved_inputs"]["age"] == 62
    assert "age" not in [m.get("name") for m in out["missing_inputs"]]


@pytest.mark.asyncio
async def test_a_stated_age_gives_a_birth_date_when_the_recipe_declares_no_age(monkeypatch) -> None:
    # Live, 2026-10-01: the hand-written core formulas declare `birth_date`, not
    # an age, so "is 62" was read into nothing and the turn asked for a birth
    # date the user had already given the means to derive.
    from langchain_core.messages import HumanMessage

    from pension_chat.agent.facts import age_on

    async def heard_nothing(**kwargs):
        return '{"resolved": {}}'

    monkeypatch.setattr(nodes, "complete", heard_nothing)
    said = "An employee who joined in 2015 is 62, earns $80,000 and has 11 years of service."
    spec = {"formula_id": "f", "execution_type": "declarative",
            "inputs": [{"name": "birth_date", "type": "date", "required": True}],
            "parameters": {}, "calculation": {"variable": "birth_date"},
            "output_name": "annual_annuity", "output_type": "money"}
    state = {"employee_record": None, "member_facts": {}, "fact_ledger": {}, "salary_history": [],
             "question": said, "messages": [HumanMessage(content=said)], "turn_index": 1,
             "coverage": {"article": "8", "basis": "deployment", "record_article": None},
             "recipes": {"retirement_annuity": {"source": "registry", "spec": spec}},
             "requires_calculation": True, "assumptions": {}}
    out = await nodes.resolve_inputs(state)
    born = date.fromisoformat(out["resolved_inputs"]["birth_date"])
    assert age_on(born, date.today()) == 62
    assert "birth_date" not in [m.get("name") for m in out["missing_inputs"]]



@pytest.mark.parametrize("said, salary", [
    ("average annual salary is $80k", 80000),     # live, 2026-10-01: read as nothing
    ("salary of 80K", 80000),
    ("earns $72.5k a year", 72500),
    ("makes 80 thousand", 80000),
    ("earns $80,000", 80000),
    ("joined in 2015 and has 11 years of service", None),
])
def test_a_stated_salary_is_read_however_it_is_written(said, salary) -> None:
    assert nodes._stated_annual_salary(said) == salary


def test_the_latest_stated_value_wins() -> None:
    # Live, 2026-10-01: "$80k" on turn 1 and "final average salary is $85k" on
    # turn 3 -- every later figure used 80,000, because the readers took the
    # first match in everything the member had typed.
    from langchain_core.messages import HumanMessage

    said = ["a person with 10 years of service at 40 years of age at an average salary of $80k",
            "years of service are 25 and final average salary is $85k", "my age is 65"]
    state = {"messages": [HumanMessage(content=s) for s in said], "question": "what if i retire 5 years early?"}
    assert nodes._latest_stated(nodes._stated_annual_salary, state) == 85000
    assert nodes._latest_stated(nodes._stated_age, state) == 65


def test_a_figure_written_in_thousands_counts_as_stated() -> None:
    # Live, 2026-10-01: "final average salary is $85k" was extracted as 85000
    # and then discarded as "not stated by the user".
    assert nodes._stated_in(85000, "years of service are 25 and final average salary is $85k")
    assert nodes._stated_in(72500, "earns 72.5 thousand")
    assert not nodes._stated_in(85000, "years of service are 25")


def test_a_value_worked_out_from_the_users_words_is_not_relabelled_record() -> None:
    # Live, 2026-10-01: a salary derived from "$80k" was carried into the next
    # turn as the member's *record*, so their correction to $85k lost to it.
    from pension_chat.agent.graph import turn_record

    update = turn_record({
        "final_answer": "...", "turn_index": 2, "question": "what if they are 65?",
        "resolved_inputs": {"final_average_salary": 80000, "service_years": 10},
        "input_sources": {"final_average_salary": "derived", "service_years": "stated"},
        "assumptions": {},
    }) or {}
    assert update["assumptions"]["final_average_salary"]["source"] == "derived"
    assert update["assumptions"]["service_years"]["source"] == "stated"
