"""Tests for choosing how much of an answer the evidence supports.

The invariant that matters is monotonicity: adding a fact must never move the
answer down the ladder. Without it the ladder becomes its own source of
erratic behaviour, which is the thing it exists to remove.
"""

from __future__ import annotations

import itertools

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


def test_monotonicity_over_input_resolution_exhaustive() -> None:
    """Property test: resolving an input never selects a lower rung.

    Tests all combinations of figure/no-figure, assumed/not-assumed,
    0-3 missing inputs, and 0-1+ candidate articles. For each state,
    verifies that removing one missing input never increases the rung
    number, and that gaining a figure never increases the rung number.
    """
    has_figure_options = [False, True]
    has_assumed_options = [False, True]
    missing_counts = [0, 1, 2, 3]
    candidate_counts = [0, 1, 2]

    for has_figure, has_assumed, missing_count, candidate_count in itertools.product(
        has_figure_options, has_assumed_options, missing_counts, candidate_counts
    ):
        sheet = _sheet()
        if has_assumed:
            sheet.record_fact("some_assumption", "value", SOURCE_ASSUMED, basis="test")

        calc = CALC if has_figure else None
        missing: list[dict[str, str]] = [{"name": f"input_{i}"} for i in range(missing_count)]
        candidates: list[str] = [str(i) for i in range(candidate_count)]

        state: dict[str, object] = {
            "calculation_result": calc,
            "missing_inputs": missing,
            "candidate_articles": candidates,
        }
        current_rung = select_rung(sheet, state)

        # Removing one missing input should not increase rung number
        if missing_count > 0:
            state_resolved: dict[str, object] = {
                "calculation_result": calc,
                "missing_inputs": missing[:-1],
                "candidate_articles": candidates,
            }
            resolved_rung = select_rung(sheet, state_resolved)
            assert resolved_rung <= current_rung, (
                f"Resolving input increased rung: {current_rung} -> {resolved_rung} "
                f"(figure={has_figure}, assumed={has_assumed}, missing={missing_count}, candidates={candidate_count})"
            )

        # Gaining a figure should not increase rung number
        if not has_figure:
            state_with_figure: dict[str, object] = {
                "calculation_result": CALC,
                "missing_inputs": missing,
                "candidate_articles": candidates,
            }
            figure_rung = select_rung(sheet, state_with_figure)
            assert figure_rung <= current_rung, (
                f"Gaining figure increased rung: {current_rung} -> {figure_rung} "
                f"(assumed={has_assumed}, missing={missing_count}, candidates={candidate_count})"
            )


def test_one_missing_and_zero_missing_same_rung_regression() -> None:
    """Regression: resolving the last missing input must not degrade the rung.

    This was the original defect: 1 missing → RUNG_ONE_MORE (3),
    0 missing → RUNG_PATHWAYS (4). Now both should be RUNG_ONE_MORE (3).
    """
    state_one_missing: dict[str, object] = {
        "calculation_result": None,
        "missing_inputs": [{"name": "x"}],
    }
    state_zero_missing: dict[str, object] = {
        "calculation_result": None,
        "missing_inputs": [],
    }
    assert select_rung(_sheet(), state_one_missing) == select_rung(
        _sheet(), state_zero_missing
    ), "1 missing and 0 missing should yield same rung"


def test_every_rung_has_a_status_label() -> None:
    for rung in (RUNG_CALCULATED, RUNG_ASSUMED, RUNG_ONE_MORE, RUNG_PATHWAYS, RUNG_ASK):
        assert STATUS_LABELS[rung]


def test_a_comparison_takes_the_comparison_shape() -> None:
    # The signal is the variants THIS turn computed, published under the
    # formula type's `scenarios`. It used to be the durable `scenario_set`,
    # which `turn_record` writes after every successful calculation -- so every
    # turn following a figure was treated as a comparison, legal questions
    # included. See `test_a_later_turn_is_not_a_comparison_just_because_an_earlier_one_was`.
    state: dict[str, object] = {
        "calculation_result": {
            "retirement_annuity": {
                "base": {"ok": True, "annual_amount": "1"},
                "scenarios": [{"label": "At 60", "retirement_age": 60, "annual_amount": "2"}],
            }
        },
        "missing_inputs": [],
    }
    assert shape_for(_sheet(), state) == "comparison"


def test_a_later_turn_is_not_a_comparison_just_because_an_earlier_one_was() -> None:
    """A durable key cannot answer a per-turn question.

    `scenario_set` survives the turn that produced it, so routing on it made a
    plain legal question following a calculation come out as a comparison --
    dropping the worked-steps slot and telling the model to name a range of
    options that did not exist.
    """
    state: dict[str, object] = {
        "scenario_set": {"if you retire now": {"annual_amount": "32126.78"}},
        "calculation_result": None,
        "missing_inputs": [],
        "requires_calculation": False,
    }
    assert shape_for(_sheet(), state) == "legal"


def test_a_two_part_question_takes_the_partial_shape() -> None:
    state: dict[str, object] = {
        "calculation_result": None,
        "missing_inputs": [],
        "answerable_part": "whether the annuity is taxed",
    }
    assert shape_for(_sheet(), state) == "partial"


def test_a_question_with_no_figure_wanted_takes_the_legal_shape() -> None:
    state: dict[str, object] = {"calculation_result": None, "missing_inputs": [], "requires_calculation": False}
    assert shape_for(_sheet(), state) == "legal"
