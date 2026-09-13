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

#: The shape `nodes._present` actually produces, statutory constants wrapped
#: with the section each was read from and steps keyed by the recipe's own
#: `result_name`. The fixture this file started with used `{"label", "amount"}`
#: steps and bare parameter values -- names nothing in the system emits -- so
#: the tests passed while production rendered `|  | None |` on every step row.
RESULT = {
    "inputs_used": {
        "final_average_salary": "69956.40",
        "service_years": "21.5",
        "months_before_age_60": "44",
    },
    "statutory_values_used": {
        "accrual_rate": {"value": 0.024, "source": {"section": "11-134"}},
        "monthly_reduction": {"value": 0.0025, "source": {"section": "11-134"}},
    },
    "steps": [
        {"name": "unreduced_annuity", "value": "36097.50"},
        {"name": "age_reduction_factor", "value": "0.89"},
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


def test_a_statutory_rate_reads_as_a_rate_and_not_as_money() -> None:
    """0.024 is Article 11's 2.40% accrual rate, not two cents."""
    table = render_worked_steps(RESULT)
    assert "2.40%" in table
    assert "0.25%" in table
    assert "$0.02" not in table
    # And the citation the value carries is not printed as its value.
    assert "section" not in table
    assert "11-134" not in table


def test_a_named_intermediate_step_shows_its_own_amount() -> None:
    table = render_worked_steps(RESULT)
    assert "| Unreduced annuity | $36,097.50 |" in table
    # A factor is a multiplier, not 89 cents.
    assert "| Age reduction factor | 0.89 |" in table


def test_the_worked_steps_render_from_real_interpreter_output() -> None:
    """The one test that cannot be fooled by a fixture.

    Every cell here comes from running the DSL interpreter over Section
    11-134(a) as it is actually specified, then through `nodes._present`, then
    through the renderer -- no hand-written intermediate shape anywhere. The
    block whose existence justified deleting an absolute rule from the answer
    prompt has to be right against what the engine emits, not against what the
    plan assumed it emitted.
    """
    from test_calculation_trace import ELECTED_ANNUITY, MEMBER_INPUTS

    from pension_chat.agent.nodes import _present
    from pension_chat.calculation.dsl_interpreter import DSLInterpreter

    # The interpreter predates strict typing, so the call is untyped; the point
    # of this test is the shape it returns, not its signature.
    raw = DSLInterpreter().execute(  # type: ignore[no-untyped-call]
        ELECTED_ANNUITY, dict(MEMBER_INPUTS)
    )

    for result in (raw, _present(raw, "annual_annuity")):
        table = render_worked_steps(result)
        cells = [
            cell.strip()
            for line in table.splitlines()
            if line.startswith("|") and not line.startswith("|---")
            for cell in line.strip("|").split("|")
        ]
        assert cells
        for cell in cells:
            assert cell, f"empty cell in: {table}"
            assert "None" not in cell, f"unrendered value in: {table}"
            assert "{" not in cell, f"raw dict in: {table}"
            assert "'value'" not in cell, f"parameter wrapper in: {table}"

    presented = render_worked_steps(_present(raw, "annual_annuity"))
    assert "| Years of service | 21.5 |" in presented
    assert "| Accrual rate | 2.40% |" in presented
    assert "**$32,126.78**" in presented


def test_a_step_carrying_a_prose_label_is_still_rendered() -> None:
    """Tolerated, not invented: a step may arrive labelled rather than named."""
    table = render_worked_steps({
        "steps": [{"label": "Reduction (44 months x 0.25%)", "amount": "-11.00%"}],
        "annual_amount": "32126.78",
    })
    assert "| Reduction (44 months x 0.25%) | -11.00% |" in table


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


def test_a_missing_marker_leaves_its_block_to_be_appended() -> None:
    """The recovery path: the member never loses the table because the model
    forgot to ask for it."""
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE", "BASIS": "BASIS-BLOCK"})
    streamed = sub.feed("Here is your pension. <<<BASIS>>>") + sub.flush()
    assert "BASIS-BLOCK" in streamed
    assert sub.unused() == ["TABLE"]

    recovered = streamed + "\n\n" + "\n\n".join(sub.unused())
    assert "TABLE" in recovered


def test_one_assumption_line_per_quantity_not_per_input_name() -> None:
    """`withdrawal_date` and `annuity_start_date` are one choice, two names.

    The ask side already collapses them (`_dedupe_missing_inputs`); disclosing
    both reads as two separate guesses about two separate things.
    """
    sheet = WorkSheet.from_state(
        {
            "turn_index": 1,
            "member_facts": {
                "withdrawal_date": "2026-09-10",
                "annuity_start_date": "2026-09-10",
            },
            "assumptions": {},
        }
    )
    rendered = render_assumptions(sheet)
    assert rendered.count("\n- ") == 1, rendered
    assert "stop working" in rendered


# ── Repaired answers go through the same substitution ─────────────


def test_a_repaired_answer_has_its_markers_substituted() -> None:
    """The repair model is handed the same system prompt, markers and all.

    Its deltas were joined and verified without ever meeting the substituter, so
    a repaired answer showed the member a raw `<<<BASIS>>>` and lost its
    legal-basis footer: 12 of 119 turns on a full eval run, every one a repair.
    """
    from pension_chat.agent.graph import _substitute

    blocks = {"BASIS": "**Legal basis** [40 ILCS 5/11-134]"}
    out = _substitute(blocks, "The corrected answer.\n\n<<<BASIS>>>")

    assert "<<<" not in out
    assert "**Legal basis** [40 ILCS 5/11-134]" in out


def test_a_marker_naming_no_block_is_dropped_not_shown() -> None:
    from pension_chat.agent.graph import _substitute

    assert "<<<" not in _substitute({}, "Text with <<<BASIS>>> in it.")


def test_a_block_whose_marker_never_arrived_is_still_appended() -> None:
    """Same rule as the streaming path: never short the member a block."""
    from pension_chat.agent.graph import _substitute

    out = _substitute({"BASIS": "**Legal basis** [40 ILCS 5/11-134]"}, "No marker.")

    assert "**Legal basis** [40 ILCS 5/11-134]" in out


def test_one_scenario_is_not_a_comparison():
    """L03's answer opened "The soonest option shown is $32,126.78" -- the
    retire-now figure -- to a member who asked what they would get at 58.

    The comparison lede is written to name a range, which is right for
    "compare 58, 60 and 62" and wrong for a single named age: the first number
    the member reads answers a question they did not ask. Both shapes keep the
    same parts, because one variant beside today's figure still earns the table
    and the difference. Only the lede differs.
    """
    from pension_chat.agent import presentation
    from pension_chat.agent.worksheet import WorkSheet

    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": {}, "assumptions": {}})

    def state(variants):
        return {
            "calculation_result": {
                "retirement_annuity": {"base": {"ok": True, "annual_amount": "1"}, "scenarios": variants}
            }
        }

    one = state([{"label": "At 58", "annual_amount": "2"}])
    two = state([{"label": "At 58", "annual_amount": "2"}, {"label": "At 60", "annual_amount": "3"}])

    assert presentation.shape_for(sheet, one) == "projected"
    assert presentation.shape_for(sheet, two) == "comparison"

    projected = presentation.TEMPLATES["projected"]
    assert "COMPARISON" in projected.slots, "one variant still earns the table"
    assert "Never open with today's figure" in projected.guidance
    assert "difference_from_now_annual" in projected.guidance, (
        "the difference is copied, never reached by subtracting two amounts"
    )
