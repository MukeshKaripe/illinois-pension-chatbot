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
    """The guardrail throws the answer away when it is not, so the instruction
    has to stay absolute for the rank that earns it."""
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
    assert any("will be withdrawn before the member sees it" in t for t in system)
