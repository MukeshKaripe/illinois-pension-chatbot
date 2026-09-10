"""Tests for what a turn leaves behind on its thread.

The graph is checkpointed, so anything a turn writes into durable state is
still there on the next one. That is the point -- "put all four results in one
comparison table" is only answerable because the four results survive as data
rather than as sentences in an earlier answer. It is also the hazard, and the
line between the two is narrow enough to be worth pinning down:

  * The figures an earlier turn computed are carried as a named `scenario_set`,
    which a later turn may quote. They came out of the deterministic
    interpreter, so quoting them is quoting the engine, not the model.
  * `calculation_result` is never carried. Verification builds the set of
    figures an answer may state partly from it, and a stale one would quietly
    widen what the next turn is allowed to say -- which is exactly the thing
    excluding prior assistant text was meant to prevent.
"""

from __future__ import annotations

from pension_chat.agent.graph import turn_record
from pension_chat.agent.state import DURABLE_KEYS

RESULT = {
    "retirement_annuity": {
        "section": "11-134",
        "subsection": "(a)",
        "formula_id": "11_retirement_annuity_11-134_eda19ce6",
        "selection_basis": "statutory_election",
        "base": {
            "ok": True,
            "annual_amount": "32126.78",
            "monthly_amount": "2677.23",
            "inputs_used": {"creditable_service_years": "21.5"},
        },
        "scenarios": [
            {
                "label": "at age 60",
                "annual_amount": "42280.10",
                "monthly_amount": "3523.34",
                "assumptions": {"service_years": "25.2"},
            }
        ],
    }
}


def _finished(**overrides) -> dict:
    state = {"final_answer": "Your pension is ...", "turn_index": 2}
    state.update(overrides)
    return state


def test_a_turn_with_no_answer_records_nothing():
    assert turn_record({"final_answer": None}) is None


def test_the_assistants_own_words_go_back_onto_the_thread():
    """Generation happens outside the graph, so the graph never sees the answer.

    Left at that, a thread accumulates only the member's questions and the
    assistant loses track of its own half of the conversation.
    """
    [message] = turn_record(_finished())["messages"]

    assert message.type == "ai"
    assert message.content == "Your pension is ..."


def test_only_durable_keys_are_written():
    update = turn_record(_finished(calculation_result=RESULT))

    assert set(update) <= DURABLE_KEYS


def test_the_turns_own_result_is_not_among_them():
    update = turn_record(_finished(calculation_result=RESULT))

    assert "calculation_result" not in update


def test_the_provision_the_conversation_settled_on_is_kept():
    provision = turn_record(_finished(calculation_result=RESULT))["selected_provision"]

    assert provision["section"] == "11-134"
    assert provision["subsection"] == "(a)"
    assert provision["selection_basis"] == "statutory_election"


def test_scenarios_are_kept_under_the_label_the_member_used():
    recorded = turn_record(_finished(calculation_result=RESULT))["scenario_set"]

    assert recorded["at age 60"]["annual_amount"] == "42280.10"
    assert recorded["at age 60"]["service_years"] == "25.2"
    # Stamped with the turn, so a later turn can tell its own figures from ones
    # it has inherited and must not recompute.
    assert recorded["at age 60"]["turn"] == 2


def test_a_turn_with_no_scenarios_records_the_plain_result():
    plain = {"retirement_annuity": {**RESULT["retirement_annuity"], "scenarios": []}}

    recorded = turn_record(_finished(calculation_result=plain))["scenario_set"]

    assert recorded["if you retire now"]["annual_amount"] == "32126.78"


def test_a_failed_calculation_is_not_recorded_as_a_result():
    failed = {
        "retirement_annuity": {
            "section": "11-134",
            "base": {"ok": False, "error": "APPLICABILITY_NOT_SATISFIED"},
        }
    }

    update = turn_record(_finished(calculation_result=failed))

    assert "baseline" not in update
    assert "scenario_set" not in update


def test_an_input_records_where_it_came_from():
    """A later turn may have to say which numbers were the member's supposition.

    The member's own words outrank their record, and "which of these did I tell
    you?" is a question the conversation should be able to answer several turns
    later.
    """
    update = turn_record(
        _finished(
            question="Actually I have 25 years of service",
            resolved_inputs={"creditable_service_years": 25, "age_at_annuity_start": 56},
        )
    )

    assert update["assumptions"]["creditable_service_years"]["source"] == "stated"
    assert update["assumptions"]["age_at_annuity_start"]["source"] == "record"


def test_a_value_the_system_assumed_is_still_assumed_on_the_next_turn() -> None:
    """Provenance has to survive the turn boundary, or disclosure is erased.

    `resolve_inputs` puts assumed values into `resolved_inputs` alongside the
    record's. Rebuilding `assumptions` from that dict wholesale relabelled them
    `record`, and everything downstream reads that label: the rung drops from
    "calculated, with assumptions" to "calculated", the assumptions block
    renders as nothing, and the disclosure guardrail stops firing. A disclosed
    assumption becomes an undisclosed fabrication in one turn boundary.
    """
    from pension_chat.agent.blocks import render_assumptions
    from pension_chat.agent.presentation import RUNG_ASSUMED, select_rung
    from pension_chat.agent.worksheet import SOURCE_ASSUMED, WorkSheet

    assumed = {
        "withdrawal_date": {
            "value": "2026-09-09",
            "source": SOURCE_ASSUMED,
            "basis": "your withdrawal date taken as today",
            "turn": 2,
        }
    }
    state = _finished(
        question="roughly what would I get?",
        calculation_result=RESULT,
        assumptions=assumed,
        resolved_inputs={
            "withdrawal_date": "2026-09-09",
            "creditable_service_years": 21.5,
        },
    )

    update = turn_record(state)

    # `assumptions` is a merged key, so leaving the name out is what preserves
    # the entry the sheet already wrote -- basis included.
    assert update is not None
    assert "withdrawal_date" not in update["assumptions"]
    assert update["assumptions"]["creditable_service_years"]["source"] == "record"

    carried = {**state, "assumptions": {**assumed, **update["assumptions"]}}
    sheet = WorkSheet.from_state(carried)

    assert "withdrawal_date" in sheet.assumed_facts()
    assert select_rung(sheet, carried) == RUNG_ASSUMED
    assert "taken as today" in render_assumptions(sheet)


# ── Routing on a correction ───────────────────────────────────────

def test_a_stale_figure_routes_to_the_calculation_branch_on_its_own() -> None:
    """A correction asks for a recalculation without asking for one.

    This is the combination that matters and the one no other unit test covers:
    the member used no calculation vocabulary, so `requires_calculation` is False
    and `formula_types_needed` is empty -- and the branch must still run, because
    a figure they had already been given rests on the value they just revised.
    Conversation B turn 3 ("actually sorry it's more like 23 years") was answered
    with "I would need the pension calculation using 23 years of service"
    precisely because this routed to `prepare_answer`.

    Its only other coverage is a live eval, which cannot run without the index.
    """
    from pension_chat.agent.graph import _needs_calculation

    assert _needs_calculation(
        {"stale_figures": ["now"], "requires_calculation": False, "formula_types_needed": []}
    ) == "resolve_inputs"


def test_a_turn_with_neither_a_request_nor_a_stale_figure_writes_prose() -> None:
    from pension_chat.agent.graph import _needs_calculation

    assert _needs_calculation(
        {"stale_figures": [], "requires_calculation": False, "formula_types_needed": []}
    ) == "prepare_answer"


def test_asking_for_a_figure_still_routes_to_the_calculation_branch() -> None:
    from pension_chat.agent.graph import _needs_calculation

    assert _needs_calculation(
        {
            "stale_figures": [],
            "requires_calculation": True,
            "formula_types_needed": ["retirement_annuity"],
        }
    ) == "resolve_inputs"
