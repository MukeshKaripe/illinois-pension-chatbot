"""Several people, one loop: each figure from its own record; numbers only in a code-rendered table."""
from __future__ import annotations

from pension_chat.agent import tools
from pension_chat.agent.card import calculation_cards
from pension_chat.agent.graph import _people_facts
from pension_chat.agent.loop import render_people
from pension_chat.agent.worksheet import WorkSheet


def _bundle(ref, label, salary, tier=2, dept="Finance"):
    return {"label": label,
            "record": {"id": ref, "article_number": 8, "tier": tier, "department": dept, "record_source": "PBMS"},
            "salary_history": [], "member_facts": {"final_average_salary": salary, "age": 62}}


PEOPLE = {"M-1": _bundle("M-1", "KA_LS MA_LP", 90000), "M-2": _bundle("M-2", "QW_XZ PL_MR", 60000, dept="Water")}


async def test_each_call_computes_from_that_persons_record_only(monkeypatch) -> None:
    seen = []

    async def fake_node(slice_):
        seen.append((slice_["employee_record"]["id"], slice_["member_facts"]["final_average_salary"]))
        return {}

    async def fake_execute(slice_):
        salary = slice_["member_facts"]["final_average_salary"]
        return {"calculation_result": {"retirement_annuity": {"base": {"ok": True, "annual_amount": salary * 0.3}}},
                "input_sources": {"final_average_salary": "record"}}

    for name in ("resolve_recipes", "resolve_inputs", "validate_recipes"):
        monkeypatch.setattr(tools, name, fake_node)
    monkeypatch.setattr(tools, "execute_calculations", fake_execute)

    state = {"people": PEOPLE, "turn_people": ["M-1", "M-2"], "calculation_result": {}}
    _, first = await tools._compute({"benefit": "retirement_annuity", "person": "ka_ls ma_lp"}, state)
    state["calculation_result"] = first["calculation_result"]
    _, second = await tools._compute({"benefit": "retirement_annuity", "person": "QW_XZ PL_MR"}, state)

    assert [s for s in seen if s[0] == "M-1"][0][1] == 90000
    assert [s for s in seen if s[0] == "M-2"][0][1] == 60000
    result = second["calculation_result"]
    assert set(result) == {"KA_LS MA_LP · retirement_annuity", "QW_XZ PL_MR · retirement_annuity"}
    assert result["KA_LS MA_LP · retirement_annuity"]["base"]["annual_amount"] == 27000
    assert result["QW_XZ PL_MR · retirement_annuity"]["person_ref"] == "M-2"


async def test_a_missing_or_unknown_person_is_refused_with_the_labels() -> None:
    state = {"people": PEOPLE, "turn_people": ["M-1", "M-2"]}
    text, update = await tools._compute({"benefit": "retirement_annuity"}, state)
    assert update == {} and "KA_LS MA_LP" in text and "QW_XZ PL_MR" in text
    text, update = await tools._compute({"benefit": "retirement_annuity", "person": "Nobody"}, state)
    assert update == {}


def _results():
    return {
        "KA_LS MA_LP · retirement_annuity": {"person": "KA_LS MA_LP", "person_ref": "M-1", "section": "8-138",
                                            "subsection": "(g-1)", "input_sources": {},
                                            "base": {"ok": True, "annual_amount": "27000.00",
                                                     "monthly_amount": "2250.00", "inputs_used": {}}},
        "QW_XZ PL_MR · retirement_annuity": {"person": "QW_XZ PL_MR", "person_ref": "M-2", "section": "8-138",
                                            "subsection": "(g-1)", "input_sources": {},
                                            "base": {"ok": True, "annual_amount": "18000.00",
                                                     "monthly_amount": "1500.00", "inputs_used": {}}},
    }


def test_the_people_table_is_one_row_per_person_in_turn_order() -> None:
    table = render_people({"people": PEOPLE, "turn_people": ["M-2", "M-1"], "calculation_result": _results()})
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line][1:]
    assert rows[0].startswith("| QW_XZ PL_MR | Water | 2 |") and "$18,000.00" in rows[0]
    assert rows[1].startswith("| KA_LS MA_LP | Finance | 2 |") and "$27,000.00" in rows[1]
    assert "M-1" not in table


def test_one_person_has_no_people_table() -> None:
    one = {k: v for k, v in _results().items() if v["person_ref"] == "M-1"}
    assert render_people({"people": PEOPLE, "turn_people": ["M-1"], "calculation_result": one}) == ""


def test_every_persons_facts_reach_the_verifier_without_colliding() -> None:
    facts = _people_facts({"people": PEOPLE})
    assert facts["M-1:final_average_salary"] == 90000 and facts["M-2:final_average_salary"] == 60000
    assert _people_facts({"people": {"M-1": PEOPLE["M-1"]}}) == {}


def test_one_card_per_person() -> None:
    state = {"people": PEOPLE, "turn_people": ["M-1", "M-2"], "calculation_result": _results(),
             "employee_record": PEOPLE["M-1"]["record"]}
    cards = calculation_cards(state, WorkSheet.from_state(state))
    assert [c["person"] for c in cards] == ["KA_LS MA_LP", "QW_XZ PL_MR"]
    assert [c["annual_amount"] for c in cards] == ["27000.00", "18000.00"]


def test_two_peoples_scenarios_do_not_overwrite_each_other() -> None:
    from pension_chat.agent.graph import turn_record

    results = _results()
    for entry in results.values():
        entry["scenarios"] = [{"label": "at age 62", "annual_amount": entry["base"]["annual_amount"]}]
    update = turn_record({"final_answer": "a", "turn_index": 1, "calculation_result": results,
                          "guardrail_passed": True})
    labels = set((update or {}).get("scenario_set") or {})
    assert {"KA_LS MA_LP — at age 62", "QW_XZ PL_MR — at age 62"} <= labels
