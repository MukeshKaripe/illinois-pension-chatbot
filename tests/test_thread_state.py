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

# `_needs_calculation` went with the pipeline at cutover. It was an edge: a
# stale figure routed the turn back through the calculation branch without the
# member having to ask again. The loop has no edges — `_context` puts the
# standing figure and what it rests on in front of the model every turn, and a
# message contradicting one of those values is read as the correction it is.
#
# That is a model decision where this was a routing rule, so the coverage is a
# live eval rather than a unit test: R03 (CRITICAL), G16, G18 and L04 all pass
# on the loop and are the cases this rule was written for.


# ── Which assumptions an answer owes the member a sentence about ───


def test_inputs_behind_reads_base_and_scenarios() -> None:
    from pension_chat.agent.graph import _inputs_behind

    computed = {
        "retirement_annuity": {
            "base": {"inputs_used": {"service_years": "21.5", "withdrawal_date": "2026-09-10"}},
            "scenarios": [{"inputs_used": {"age_at_annuity_start": "60"}}],
        },
        # `_carried_differences` is injected alongside the formula types and is
        # not a result; it must not blow up the scan.
        "_carried_differences": [{"label": "at 60"}],
    }
    assert _inputs_behind(computed) == {
        "service_years",
        "withdrawal_date",
        "age_at_annuity_start",
    }


def test_an_assumption_the_figure_never_used_is_not_owed_disclosure() -> None:
    """The durable sheet outlives the turn that made the assumption.

    A retirement date assumed on turn 1 is still on the sheet on turn 3. When
    turn 3 asks about a survivor annuity, its answer has no reason to mention a
    retirement date -- and policing every assumption on the sheet withdrew a
    correct answer for not disclosing one it had not used.
    """
    from pension_chat.agent.graph import _inputs_behind

    survivor = {"survivor_annuity": {"base": {"inputs_used": {"spouse_age": "54"}}}}
    used = _inputs_behind(survivor)
    assert "withdrawal_date" not in used
    assert used == {"spouse_age"}


def test_an_unreadable_result_polices_every_assumption() -> None:
    """Erring towards a spurious repair, never towards silence.

    If the figure does not say what it used, the alternative to policing
    everything is letting an undisclosed assumption through -- which is the one
    outcome this check exists to make impossible.
    """
    from pension_chat.agent.graph import _inputs_behind

    assert _inputs_behind({"retirement_annuity": {"base": {}}}) == set()
