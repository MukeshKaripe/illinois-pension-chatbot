"""Tests for choosing how much of an answer the evidence supports.

The invariant that matters is monotonicity: adding a fact must never move the
answer down the ladder. Without it the ladder becomes its own source of
erratic behaviour, which is the thing it exists to remove.
"""

from __future__ import annotations

from pension_chat.agent.presentation import (
    RUNG_ASK,
    RUNG_ASSUMED,
    RUNG_CALCULATED,
    RUNG_ONE_MORE,
    RUNG_PATHWAYS,
    STATUS_LABELS,
    select_rung,
    shape_for,
)
from pension_chat.agent.worksheet import SOURCE_ASSUMED, WorkSheet

FACTS = {"age": 56, "service_years": 21.5, "final_average_salary": 69956.4}
CALC = {"retirement_annuity": {"base": {"ok": True, "annual_amount": "32126.78"}}}


def _sheet(**extra: object) -> WorkSheet:
    return WorkSheet.from_state({"turn_index": 1, "member_facts": FACTS, "assumptions": {}, **extra})


def test_a_fully_sourced_figure_is_rung_one() -> None:
    assert select_rung(_sheet(), {"calculation_result": CALC, "missing_inputs": []}) == RUNG_CALCULATED


def test_a_figure_resting_on_an_assumption_is_rung_two() -> None:
    sheet = _sheet()
    sheet.record_fact("withdrawal_date", "2026-09-09", SOURCE_ASSUMED, basis="today")
    assert select_rung(sheet, {"calculation_result": CALC, "missing_inputs": []}) == RUNG_ASSUMED


def test_one_missing_input_is_rung_three() -> None:
    state: dict[str, object] = {"calculation_result": None, "missing_inputs": [{"name": "withdrawal_date"}]}
    assert select_rung(_sheet(), state) == RUNG_ONE_MORE


def test_competing_pathways_are_rung_four() -> None:
    state: dict[str, object] = {
        "calculation_result": None,
        "missing_inputs": [],
        "candidate_articles": ["11", "12"],
    }
    assert select_rung(_sheet(), state) == RUNG_PATHWAYS


def test_several_missing_inputs_is_rung_five() -> None:
    state: dict[str, object] = {
        "calculation_result": None,
        "missing_inputs": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
    }
    assert select_rung(_sheet(), state) == RUNG_ASK


def test_adding_a_fact_never_moves_the_answer_down_the_ladder() -> None:
    before: int = select_rung(
        _sheet(), {"calculation_result": None, "missing_inputs": [{"name": "a"}, {"name": "b"}]}
    )
    after = select_rung(_sheet(), {"calculation_result": CALC, "missing_inputs": []})
    assert after <= before


def test_every_rung_has_a_status_label() -> None:
    for rung in (RUNG_CALCULATED, RUNG_ASSUMED, RUNG_ONE_MORE, RUNG_PATHWAYS, RUNG_ASK):
        assert STATUS_LABELS[rung]


def test_a_comparison_takes_the_comparison_shape() -> None:
    state: dict[str, object] = {"calculation_result": CALC, "missing_inputs": [], "scenario_set": {"at 60": {}}}
    assert shape_for(_sheet(), state) == "comparison"


def test_a_two_part_question_takes_the_partial_shape() -> None:
    state: dict[str, object] = {"calculation_result": None, "missing_inputs": [], "answerable_part": "whether the annuity is taxed"}
    assert shape_for(_sheet(), state) == "partial"


def test_a_question_with_no_figure_wanted_takes_the_legal_shape() -> None:
    state: dict[str, object] = {"calculation_result": None, "missing_inputs": [], "requires_calculation": False}
    assert shape_for(_sheet(), state) == "legal"
