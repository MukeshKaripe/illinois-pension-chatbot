"""Tests for the computed answer blocks and the streaming marker substituter.

These blocks are pure functions of the working sheet, which is the point: a
table the model transcribes can be mistranscribed, and a table Python renders
cannot.
"""

from __future__ import annotations

from pension_chat.agent.blocks import (
    MarkerSubstituter,
    render_assumptions,
    render_comparison,
    render_status,
    render_worked_steps,
)
from pension_chat.agent.presentation import RUNG_ASSUMED
from pension_chat.agent.worksheet import SOURCE_ASSUMED, WorkSheet

RESULT = {
    "inputs_used": {"final_average_salary": "69956.40", "service_years": "21.5"},
    "statutory_values_used": {"annual_rate": "2.40%"},
    "steps": [
        {"label": "Before early-retirement reduction", "amount": "36097.50"},
        {"label": "Reduction (44 months x 0.25%)", "amount": "-11.00%"},
    ],
    "annual_amount": "32126.78",
    "monthly_amount": "2677.23",
}


def test_the_worked_steps_table_is_well_formed_and_right_aligned() -> None:
    table = render_worked_steps(RESULT)
    lines = [line for line in table.splitlines() if line.startswith("|")]
    assert lines[1] == "|---|---:|"
    assert all(line.count("|") == 3 for line in lines)
    assert "**Annual pension**" in table
    assert "$32,126.78" in table
    assert "$2,677.23" in table


def test_money_is_formatted_once_and_only_once() -> None:
    table = render_worked_steps(RESULT)
    assert "$$" not in table
    assert "69956.40" not in table
    assert "$69,956.40" in table


def test_years_of_service_not_formatted_as_money() -> None:
    """Years of service should be rendered as a plain quantity, not as money."""
    table = render_worked_steps(RESULT)
    assert "21.5" in table
    assert "$21.50" not in table


def test_the_comparison_table_carries_the_service_column() -> None:
    table = render_comparison([
        {"label": "Now, at 56", "service_years": "21.5", "annual": "32126.78", "monthly": "2677.23"},
        {"label": "At 58", "service_years": "23.2", "annual": "36584.70", "monthly": "3048.72"},
    ])
    assert "| Years of service |" in table
    assert "| 23.2 |" in table
    assert "$36,584.70" in table


def test_the_assumptions_block_names_the_basis_and_stays_out_of_the_way_when_empty() -> None:
    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": {}, "assumptions": {}})
    assert render_assumptions(sheet) == ""

    sheet.record_fact(
        "months_before_age_60", 42, SOURCE_ASSUMED,
        basis="your birthday falling mid-year, because your date of birth is not on file",
    )
    block = render_assumptions(sheet)
    assert "What I assumed" in block
    assert "birthday falling mid-year" in block


def test_the_status_line_names_the_rung() -> None:
    assert render_status(RUNG_ASSUMED) == "**Calculated, with assumptions**"


def test_the_substituter_swaps_a_marker_that_arrives_whole() -> None:
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = sub.feed("Here it is.\n\n<<<WORKED_STEPS>>>\n\nDone.") + sub.flush()
    assert "TABLE" in out
    assert "<<<" not in out


def test_the_substituter_swaps_a_marker_split_across_deltas() -> None:
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = "".join(sub.feed(part) for part in ["Here. <<<WORK", "ED_ST", "EPS>>> done."])
    out += sub.flush()
    assert "TABLE" in out
    assert "<<<" not in out


def test_a_duplicated_marker_renders_the_block_once() -> None:
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = sub.feed("<<<WORKED_STEPS>>> and again <<<WORKED_STEPS>>>") + sub.flush()
    assert out.count("TABLE") == 1
    assert "<<<" not in out


def test_an_unused_block_is_reported_so_the_caller_can_append_it() -> None:
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = sub.feed("No marker here at all.") + sub.flush()
    assert "TABLE" not in out
    assert sub.unused() == ["TABLE"]


def test_an_unknown_marker_is_removed_rather_than_shown_to_the_member() -> None:
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = sub.feed("Text <<<NOT_A_BLOCK>>> more.") + sub.flush()
    assert "<<<" not in out
    assert "NOT_A_BLOCK" not in out
