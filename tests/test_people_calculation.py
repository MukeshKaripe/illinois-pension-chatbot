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


def test_one_persons_remembered_values_never_reach_another() -> None:
    # Found live: after switching from A to B, B's figure used A's final average
    # salary -- the fact ledger held every value under one subject.
    from pension_chat.agent.nodes import _available_facts, _ledger_subject, _ledger_update

    a = {"employee_record": {"id": "M-1", "record_source": "PBMS"}}
    held = _ledger_update(resolved={"final_average_salary": 90000}, origins={"final_average_salary": "stated"},
                          turn=1, subject=_ledger_subject(a))
    b = {"employee_record": {"id": "M-2", "record_source": "PBMS", "article_number": 8},
         "member_facts": {"final_average_salary": 60000, "article_number": 8},
         "coverage": {"article": "8", "record_article": "8"}, "fact_ledger": held}
    assert _available_facts(b)["final_average_salary"] == 60000
    assert _available_facts({**b, "employee_record": {"id": "M-1", "record_source": "PBMS", "article_number": 8}}
                            )["final_average_salary"] == 90000
    assert _ledger_subject({"employee_record": {"id": "x"}}) == "me"      # a member's own record: unchanged


def test_remembered_scenarios_belong_to_their_person() -> None:
    # Found live: B's answer tabled A's "retire now" and "at 65" rows.
    from pension_chat.agent.graph import turn_record
    from pension_chat.agent.loop import _live_options

    entry = {"section": "8-138", "base": {"ok": True, "annual_amount": "41991.94", "monthly_amount": "3499.33",
                                          "inputs_used": {}},
             "scenarios": [{"label": "at age 65", "annual_amount": "41991.94", "monthly_amount": "3499.33"}]}
    a_turn = {"final_answer": "a", "turn_index": 1, "guardrail_passed": True, "turn_people": ["M-1"],
              "employee_record": {"id": "M-1", "record_source": "PBMS", "display_name": "KA_LS MA_LP"},
              "calculation_result": {"retirement_annuity": entry}}
    held = (turn_record(a_turn) or {})["scenario_set"]
    assert list(held) == ["KA_LS MA_LP — at age 65"] and held["KA_LS MA_LP — at age 65"]["person_ref"] == "M-1"
    assert _live_options({"scenario_set": held, "turn_people": ["M-1"]})
    assert _live_options({"scenario_set": held, "turn_people": ["M-2"]}) == {}
    assert _live_options({"scenario_set": held, "turn_people": ["M-1", "M-2"]}) == {}
    member = {**a_turn, "employee_record": {"id": "x"}, "turn_people": []}
    assert list((turn_record(member) or {})["scenario_set"]) == ["at age 65"]          # a member's own: unchanged


async def test_every_person_in_a_comparison_gets_a_figure() -> None:
    # Found live: with A's figure from an earlier turn in view, the model
    # computed only B -- and wrote its own table around the gap.
    from pension_chat.agent.loop import _complete_people

    calls = []

    async def run(name, arguments, working):
        calls.append((name, arguments))
        ref = next(r for r, b in PEOPLE.items() if b["label"] == arguments["person"])
        return "ok", {"calculation_result": {**(working.get("calculation_result") or {}),
                                             f"{arguments['person']} · x": {"person_ref": ref,
                                                                           "base": {"ok": True}}}}

    only_b = {k: v for k, v in _results().items() if v["person_ref"] == "M-2"}
    working = {"people": PEOPLE, "turn_people": ["M-1", "M-2"], "calculation_result": only_b}
    done = await _complete_people(working, {"benefit": "retirement_annuity", "retirement_age": 65}, run)
    assert done == ["KA_LS MA_LP"]
    assert calls == [("compute_benefit", {"benefit": "retirement_annuity", "retirement_age": 65,
                                          "person": "KA_LS MA_LP"})]
    assert await _complete_people(working, None, run) == []                       # nothing computed: nothing to copy
    assert await _complete_people({**working, "people": {"M-1": PEOPLE["M-1"]}}, {"benefit": "x"}, run) == []


def test_a_comparison_offers_only_the_people_table() -> None:
    # Found live: the model dropped <<<WORKED_STEPS>>> into a cell of its own
    # comparison table. With several people, one person's working is not the answer.
    from pension_chat.agent.loop import MARKED_VIEWS, _views

    state = {"people": PEOPLE, "turn_people": ["M-1", "M-2"], "calculation_result": _results()}
    marked = [slot for slot in _views(state) if slot in MARKED_VIEWS]
    assert marked == ["PEOPLE"]


def test_the_people_table_shows_the_figure_the_question_asked_for() -> None:
    # Found live: "compare all three at 62" tabled each person's figure as of
    # today, while the model quoted the at-62 figure -- two numbers for one person.
    results = _results()
    results["KA_LS MA_LP · retirement_annuity"]["scenarios"] = [
        {"label": "at age 62", "annual_amount": "31000.00", "monthly_amount": "2583.33"}]
    table = render_people({"people": PEOPLE, "turn_people": ["M-1", "M-2"], "calculation_result": results})
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    assert "When" in rows[0]
    assert "at age 62" in rows[1] and "$31,000.00" in rows[1] and "$27,000.00" not in rows[1]
    assert "if they retire now" in rows[2] and "$18,000.00" in rows[2]


async def test_one_persons_stated_values_are_not_copied_onto_another() -> None:
    # "Compare them if RO_BK had 25 years": the 25 is RO_BK's, and "25" being in
    # the user's words must not let it override the other person's record.
    from pension_chat.agent.loop import _complete_people

    calls = []

    async def run(name, arguments, working):
        calls.append(arguments)
        return "ok", {}

    only_b = {k: v for k, v in _results().items() if v["person_ref"] == "M-2"}
    working = {"people": PEOPLE, "turn_people": ["M-1", "M-2"], "calculation_result": only_b}
    await _complete_people(working, {"benefit": "retirement_annuity", "stated": {"service_years": 25},
                                     "stand_in_facts": True}, run)
    assert calls == [{"benefit": "retirement_annuity", "person": "KA_LS MA_LP"}]
