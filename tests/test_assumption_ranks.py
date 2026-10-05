"""What is worked out is not what is guessed.

The ceiling exists to stop the agent inventing a member. A value computed from
facts that member supplied invents nothing, so it does not spend the ceiling and
it is not labelled as an assumption on the calculation card.
"""

from __future__ import annotations

from pension_chat.agent.nodes import apply_assumptions
from pension_chat.agent.worksheet import SOURCE_ASSUMED, SOURCE_DERIVED, WorkSheet


def _sheet() -> WorkSheet:
    return WorkSheet(facts={}, figures={}, turn=1)


def _ask(name: str) -> dict:
    return {"name": name, "type": "integer", "description": name, "ask_for": name}


def test_a_value_computed_from_a_stated_age_is_derived():
    sheet = _sheet()
    filled, origins, missing = apply_assumptions(
        sheet, [_ask("months_before_age_60")], {"age": 56}, 2
    )
    assert filled["months_before_age_60"] == 42
    assert origins["months_before_age_60"] == SOURCE_DERIVED
    assert missing == []


def test_taking_retire_now_as_today_is_still_an_assumption():
    """Nothing the member said picks today. It is a framing choice, and the
    answer has to say so."""
    sheet = _sheet()
    _, origins, _ = apply_assumptions(
        sheet, [_ask("withdrawal_date")], {"as_of_date": "2026-09-16"}, 2
    )
    assert origins["withdrawal_date"] == SOURCE_ASSUMED


def test_a_derivation_does_not_spend_the_ceiling():
    """Three fills under a ceiling of two: the derivation is free, so both
    assumptions still land."""
    sheet = _sheet()
    filled, origins, missing = apply_assumptions(
        sheet,
        [_ask("months_before_age_60"), _ask("withdrawal_date"), _ask("final_average_salary")],
        {"age": 56, "as_of_date": "2026-09-16", "stated_annual_salary": 80000},
        2,
    )
    assert set(filled) == {"months_before_age_60", "withdrawal_date", "final_average_salary"}
    assert missing == []
    assert sum(1 for s in origins.values() if s == SOURCE_ASSUMED) == 2


def test_the_ceiling_still_bites_invented_values():
    sheet = _sheet()
    filled, _, missing = apply_assumptions(
        sheet,
        [_ask("withdrawal_date"), _ask("final_average_salary")],
        {"as_of_date": "2026-09-16", "stated_annual_salary": 80000},
        1,
    )
    assert len(filled) == 1
    assert [m["name"] for m in missing] == ["final_average_salary"]


def test_a_derivation_is_not_reported_as_an_assumption_to_the_member():
    sheet = _sheet()
    apply_assumptions(sheet, [_ask("months_before_age_60")], {"age": 56}, 2)
    assert "months_before_age_60" not in sheet.assumed_facts()
