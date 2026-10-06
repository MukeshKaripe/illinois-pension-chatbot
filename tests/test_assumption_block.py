"""Three ranks, three sentences -- and only one of them is an apology.

A value the Code states gets its section. A value worked out from what the
member said gets the fact it came from. Only a value nobody supplied is an
assumption, and only that one is capped.

Before this, every fill was recorded at `assumed` and every fill was listed
under "What I assumed" -- so a member reading an age the agent had cited to
Section 11-134 was told it had been guessed at.
"""

from __future__ import annotations

from pension_chat.agent.blocks import render_assumptions
from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_DERIVED,
    SOURCE_STATUTORY,
    WorkSheet,
)


def _sheet_with(**facts) -> WorkSheet:
    sheet = WorkSheet.from_state({"turn_index": 1})
    for name, (value, source, basis) in facts.items():
        sheet.record_fact(name, value, source, basis=basis)
    return sheet


def test_a_statutory_value_is_cited_not_apologised_for():
    sheet = _sheet_with(
        age_at_annuity_start=(60, SOURCE_STATUTORY, "stated in Section 11-134(a)")
    )
    assert set(sheet.facts_at(SOURCE_STATUTORY)) == {"age_at_annuity_start"}
    assert sheet.assumed_facts() == {}

    text = render_assumptions(sheet)
    assert "11-134" in text
    assert "What I assumed" not in text
    assert "What the Code sets" in text


def test_a_derived_value_names_what_it_was_worked_out_from():
    sheet = _sheet_with(
        months_before_age_60=(42, SOURCE_DERIVED, "your birthday falling mid-year")
    )
    assert set(sheet.facts_at(SOURCE_DERIVED)) == {"months_before_age_60"}
    assert sheet.assumed_facts() == {}

    text = render_assumptions(sheet)
    assert "What I worked out" in text
    assert "What I assumed" not in text


def test_only_an_invented_value_is_an_assumption():
    sheet = _sheet_with(withdrawal_date=("2026-09-16", SOURCE_ASSUMED, "taken as today"))
    assert set(sheet.assumed_facts()) == {"withdrawal_date"}

    text = render_assumptions(sheet)
    assert "What I assumed" in text
    assert "taken as today" in text


def test_all_three_appear_in_rank_order():
    """The Code first, then what came from the member, then the guess. A
    member skimming should meet the strongest evidence first."""
    sheet = _sheet_with(
        withdrawal_date=("2026-09-16", SOURCE_ASSUMED, "taken as today"),
        months_before_age_60=(42, SOURCE_DERIVED, "your birthday falling mid-year"),
        age_at_annuity_start=(60, SOURCE_STATUTORY, "stated in Section 11-134(a)"),
    )
    text = render_assumptions(sheet)
    assert (
        text.index("What the Code sets")
        < text.index("What I worked out")
        < text.index("What I assumed")
    )


def test_an_empty_sheet_renders_nothing():
    assert render_assumptions(WorkSheet.from_state({"turn_index": 1})) == ""


def test_a_record_value_is_none_of_the_three():
    """Nothing was filled in, so there is nothing to account for."""
    from pension_chat.agent.worksheet import SOURCE_STATED

    sheet = _sheet_with(service_years=(21.5, SOURCE_STATED, ""))
    assert render_assumptions(sheet) == ""


# ── What compose tells the model ──────────────────────────────────


async def _composed(state):
    from pension_chat.agent import loop as loop_mod

    out = await loop_mod.compose(
        {"question": "q", "messages": [], "loop_messages": [], **state}
    )
    return [
        str(m.get("content") or "")
        for m in out["answer_messages"]
        if m.get("role") == "system"
    ]


_FIGURE = {"retirement_annuity": {"base": {"ok": True, "annual_amount": "38000.00"}}}


async def test_a_cited_fill_is_not_presented_as_something_to_apologise_for():
    system = await _composed(
        {
            "calculation_result": _FIGURE,
            "assumptions": {
                "age_at_annuity_start": {
                    "value": 60,
                    "source": "statutory_parameter",
                    "basis": "stated in Section 11-134(a)",
                }
            },
        }
    )
    account = [t for t in system if "11-134" in t]
    assert account, "the fill must reach the model that writes the sentence"
    assert not any("nobody supplied" in t for t in account)
    assert any("do not call them assumptions" in t for t in account)


async def test_an_invented_fill_still_must_be_disclosed():
    """Code inserts the disclosure; the model is told the figure is not exact,
    so its own sentences cannot contradict the block."""
    system = await _composed(
        {
            "calculation_result": _FIGURE,
            "assumptions": {
                "withdrawal_date": {
                    "value": "2026-09-16",
                    "source": "assumed",
                    "basis": "taken as today",
                }
            },
        }
    )
    assert any("rests on values nobody supplied" in t for t in system)
    assert any("<<<ASSUMPTIONS>>>" in t for t in system)


# ── The block is the disclosure ───────────────────────────────────
#
# A figure resting on a value nobody supplied must say so, and the model writing
# that sentence was the weak link: when it forgot, verification replaced a
# correct answer (2 of 48 on the S families after a stated salary became an
# assumed flat average). Code now inserts the block beside the figure, and the
# block alone must satisfy the same check.


def test_every_assumption_rule_is_disclosed_by_its_own_block():
    import pytest

    from pension_chat.agent.guardrails import GuardrailReport, _check_assumptions_disclosed
    from pension_chat.calculation.assumption_policy import POLICY

    checked = 0
    for name, rule in POLICY.items():
        if not rule.basis:
            continue
        block = render_assumptions(_sheet_with(**{name: ("x", SOURCE_ASSUMED, rule.basis)}))
        report = GuardrailReport()
        _check_assumptions_disclosed(report, f"Your annuity is $12,000.00 a year.\n\n{block}",
                                     {"retirement_annuity": {"base": {"ok": True}}}, {name: rule.basis})
        assert report.passed, pytest.fail(f"{name}: its block does not disclose it -- {block!r}")
        checked += 1
    assert checked >= 4


def test_the_assumptions_block_is_inserted_by_code():
    from pension_chat.agent.loop import MARKED_VIEWS
    assert "ASSUMPTIONS" in MARKED_VIEWS


def test_a_comparison_keeps_its_assumptions_block():
    # Several people: one person's working steps are dropped beside the PEOPLE
    # table, but a disclosure is never one person's -- it stays.
    from pension_chat.agent import loop

    rendered = {"PEOPLE": "| a |", "WORKED_STEPS": "steps", "ASSUMPTIONS": "**What I assumed**"}
    kept = loop._keep_for_people(rendered)
    assert set(kept) == {"PEOPLE", "ASSUMPTIONS"}


# ── One answer to "was a figure worked this turn?" ────────────────
#
# Found live (S4-002, S11-002): the block rendered only beside a "retire now"
# figure, so a turn whose figure was a scenario ("at age 67") had no block while
# the guardrail demanded one. And S4b-001: the guardrail demanded a disclosure
# from an answer that worked no figure at all ("not payable now"). One predicate
# now decides both.

_ASSUMED_DATE = {"withdrawal_date": {"value": "2026-10-06", "source": "assumed",
                                     "basis": "the date you stop working, taken as today"}}


def test_a_scenario_figure_gets_its_disclosure():
    from pension_chat.agent.loop import _views

    state = {"calculation_result": {"retirement_annuity": {
                 "base": {"ok": False},
                 "scenarios": [{"label": "at age 67", "annual_amount": "39172.43"}]}},
             "assumptions": _ASSUMED_DATE}
    assert "What I assumed" in _views(state).get("ASSUMPTIONS", "")


def test_an_answer_with_no_figure_has_nothing_to_disclose():
    from pension_chat.agent.guardrails import GuardrailReport, _check_assumptions_disclosed

    report = GuardrailReport()
    _check_assumptions_disclosed(
        report, "Your pension cannot begin before age 60. You are 56, so it is not payable now.",
        {"retirement_annuity": {"base": {"ok": False}, "scenarios": []}},
        {"withdrawal_date": "the date you stop working, taken as today"})
    assert report.passed


def test_a_scenario_figure_still_must_be_disclosed():
    from pension_chat.agent.guardrails import GuardrailReport, _check_assumptions_disclosed

    report = GuardrailReport()
    _check_assumptions_disclosed(
        report, "At 67 your annuity is $39,172.43 a year.",
        {"retirement_annuity": {"base": {"ok": False},
                                "scenarios": [{"label": "at age 67", "annual_amount": "39172.43"}]}},
        {"withdrawal_date": "the date you stop working, taken as today"})
    assert not report.passed


def test_an_assumed_salary_says_which_salary():
    # Found live (S11-001): "Sorry, the salary is $86,000" -- the block said only
    # "the salary you gave", and the answer never named the figure it now used.
    from pension_chat.calculation.assumption_policy import POLICY

    basis = POLICY["final_average_salary"].basis
    text = render_assumptions(_sheet_with(final_average_salary=(86000, SOURCE_ASSUMED, basis)))
    assert "$86,000" in text and "$86,000.00" not in text
    text = render_assumptions(_sheet_with(final_average_salary=(72500.5, SOURCE_ASSUMED, basis)))
    assert "$72,500.50" in text
