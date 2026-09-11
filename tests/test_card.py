"""Tests for the calculation card: the structured twin of the answer's blocks.

The card is what the client draws as the headline figure and the calculation
drawer. Every value on it must come from the interpreter's result or the
worksheet -- never from the answer's prose -- so it is built and tested as a
pure function of state.
"""

from __future__ import annotations

from pension_chat.agent.card import DRAWER_BLOCKS, calculation_card
from pension_chat.agent.worksheet import WorkSheet


def _state(*, policy: str = "estimate", ok: bool = True) -> dict:
    return {
        "member_facts": {"service_years": "21.5"},
        "calculation_result": {
            "retirement_annuity": {
                "formula_type": "retirement_annuity",
                "formula_name": "Retirement annuity",
                "section": "11-134",
                "subsection": "(a)",
                "execution_policy": policy,
                "base": {
                    "ok": ok,
                    "annual_amount": "32126.78",
                    "monthly_amount": "2677.23",
                    "inputs_used": {
                        "service_years": "21.5",
                        "final_average_salary": "69956.40",
                    },
                },
                "scenarios": [
                    {"label": "at age 60", "annual_amount": "36097.50", "monthly_amount": "3008.13"}
                ],
            }
        },
        "answer_blocks": {
            "WORKED_STEPS": "| Step | Amount |",
            "ASSUMPTIONS": "- Retirement date: today",
            "STATUS": "Estimate",
            "BASIS": "40 ILCS 5/11-134",
        },
    }


def _card(state: dict) -> dict | None:
    return calculation_card(state, WorkSheet.from_state(state))


def test_the_card_says_how_well_the_fund_is_known() -> None:
    # Coverage's verdict decides which Article the figure was computed under,
    # so it outranks the record; with neither, the card makes no claim.
    state = _state()
    state["coverage"] = {"article": "16"}
    state["employee_record"] = {"article_number": 11}
    assert _card(state)["support_level"] == "experimental"

    state["coverage"] = None
    assert _card(state)["support_level"] == "checked"

    state["employee_record"] = None
    assert _card(state)["support_level"] is None


def test_card_carries_the_headline_figure_and_its_basis() -> None:
    card = _card(_state())
    assert card is not None
    assert card["type"] == "card"
    assert card["section"] == "11-134"
    assert card["subsection"] == "(a)"
    assert card["annual_amount"] == "32126.78"
    assert card["monthly_amount"] == "2677.23"
    assert card["estimate"] is True


def test_an_authoritative_recipe_is_not_labelled_an_estimate() -> None:
    card = _card(_state(policy="authoritative"))
    assert card is not None
    assert card["estimate"] is False


def test_the_turns_own_record_of_origins_wins() -> None:
    # `resolve_inputs` knows where each value came from as it resolves it; the
    # sheet only sometimes does. Seen live: identical questions labelled the
    # same stated salary "stated" on one run and "derived" on the next.
    state = _state()
    state["input_sources"] = {"service_years": "stated", "final_average_salary": "derived"}
    card = _card(state)
    assert card is not None
    sources = {entry["name"]: entry["source"] for entry in card["inputs"]}
    assert sources == {"service_years": "stated", "final_average_salary": "derived"}


def test_without_a_record_of_origins_the_sheet_speaks_or_nothing_does() -> None:
    card = _card(_state())
    assert card is not None
    sources = {entry["name"]: entry["source"] for entry in card["inputs"]}
    # On the record, so the sheet says so.
    assert sources["service_years"] == "record"
    # Nobody knows where this one came from, so no label is claimed for it.
    assert sources["final_average_salary"] is None
    assert all(entry["label"] for entry in card["inputs"])


def test_a_stated_value_is_found_under_its_alias() -> None:
    # Recipes name inputs in the statute's words; the member's statement lands
    # under another name for the same quantity. Seen live: a salary the member
    # stated was labelled "derived" because only the recipe's name was looked up.
    state = _state()
    base = state["calculation_result"]["retirement_annuity"]["base"]
    base["inputs_used"]["highest_average_annual_salary_4yr"] = "70000.00"
    state["assumptions"] = {"final_average_salary": {"value": "70000.00", "source": "stated"}}
    card = _card(state)
    assert card is not None
    sources = {entry["name"]: entry["source"] for entry in card["inputs"]}
    assert sources["highest_average_annual_salary_4yr"] == "stated"


def test_the_drawer_gets_only_the_calculation_blocks() -> None:
    card = _card(_state())
    assert card is not None
    assert set(card["blocks"]) == {"WORKED_STEPS", "ASSUMPTIONS"}
    assert set(card["blocks"]) <= set(DRAWER_BLOCKS)


def test_scenarios_ride_along() -> None:
    card = _card(_state())
    assert card is not None
    assert card["scenarios"] == [
        {"label": "at age 60", "annual_amount": "36097.50", "monthly_amount": "3008.13"}
    ]


def test_no_card_without_a_successful_result() -> None:
    assert _card(_state(ok=False)) is None
    assert _card({}) is None
