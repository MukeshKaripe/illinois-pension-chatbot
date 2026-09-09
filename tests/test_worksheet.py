"""Tests for the conversation working sheet.

The sheet is a view over durable state keys that already exist, so these
tests pin two things: that a round trip through state loses nothing, and
that provenance actually governs what a figure is allowed to rest on.
"""

from __future__ import annotations

from typing import Any

from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_RECORD,
    SOURCE_STATED,
    SOURCE_WITHHELD,
    Fact,
    WorkSheet,
)


def _state() -> dict[str, Any]:
    return {
        "turn_index": 2,
        "member_facts": {"age": 56, "service_years": 21.5, "final_average_salary": 69956.4},
        "assumptions": {},
        "baseline": None,
        "scenario_set": {},
        "open_question": None,
    }


def test_member_facts_arrive_as_record_provenance() -> None:
    sheet = WorkSheet.from_state(_state())
    fact = sheet.fact("age")
    assert fact is not None
    assert fact.source == SOURCE_RECORD
    assert fact.value == 56


def test_a_stated_value_overrides_the_record_and_keeps_its_source() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 23.0, SOURCE_STATED, turn=2)
    fact = sheet.fact("service_years")
    assert fact is not None
    assert fact.value == 23.0
    assert fact.source == SOURCE_STATED


def test_withheld_facts_are_known_but_not_usable() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 21.5, SOURCE_WITHHELD, basis="premise is Article 12")
    assert sheet.knows("service_years") is True
    assert "service_years" not in sheet.usable_values()


def test_assumed_facts_are_usable_and_reportable() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("withdrawal_date", "2026-09-09", SOURCE_ASSUMED, basis="today")
    assert sheet.usable_values()["withdrawal_date"] == "2026-09-09"
    assert "withdrawal_date" in sheet.assumed_facts()


def test_revising_an_input_makes_the_figures_that_used_it_stale() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_figure(
        "now",
        {"annual_amount": "32126.78"},
        {"section": "11-134", "subsection": "(a)"},
        rests_on=("service_years", "final_average_salary"),
        turn=1,
    )
    stale = sheet.apply_revisions({"service_years": 23.0}, turn=2)
    assert stale == {"now"}
    assert sheet.stale_figures() == ["now"]
    fact = sheet.fact("service_years")
    assert fact is not None
    assert fact.source == SOURCE_STATED


def test_revising_an_unrelated_input_leaves_figures_alone() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_figure(
        "now", {"annual_amount": "1"}, {"section": "11-134"},
        rests_on=("service_years",), turn=1,
    )
    assert sheet.apply_revisions({"spouse_age": 53}, turn=2) == set()
    assert sheet.stale_figures() == []


def test_asks_for_things_already_known_are_dropped() -> None:
    sheet = WorkSheet.from_state(_state())
    kept = sheet.filter_asks([
        {"name": "age", "ask_for": "your date of birth"},
        {"name": "withdrawal_date", "ask_for": "the date you plan to stop working"},
    ])
    assert [item["name"] for item in kept] == ["withdrawal_date"]


def test_a_withheld_ask_is_kept_but_marked_so_it_can_be_phrased_honestly() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 21.5, SOURCE_WITHHELD, basis="premise is Article 12")
    kept = sheet.filter_asks([{"name": "service_years", "ask_for": "your years of service"}])
    assert len(kept) == 1
    assert kept[0]["withheld"] is True
    assert kept[0]["withheld_basis"] == "premise is Article 12"


def test_a_round_trip_through_state_preserves_facts_figures_and_dependencies() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("withdrawal_date", "2026-09-09", SOURCE_ASSUMED, basis="today", turn=2)
    sheet.record_figure(
        "now", {"annual_amount": "32126.78"}, {"section": "11-134"},
        rests_on=("service_years",), turn=2,
    )
    state = {**_state(), **sheet.to_state_update()}
    restored = WorkSheet.from_state(state)
    fact = restored.fact("withdrawal_date")
    assert fact is not None
    assert fact.source == SOURCE_ASSUMED
    assert fact.basis == "today"
    assert restored.figures["now"].rests_on == ("service_years",)


def test_filter_asks_is_idempotent_and_preserves_order() -> None:
    sheet = WorkSheet.from_state(_state())
    asks = [
        {"name": "withdrawal_date", "ask_for": "the date you plan to stop working"},
        {"name": "spouse_age", "ask_for": "your spouse's age"},
    ]
    once = sheet.filter_asks(asks)
    twice = sheet.filter_asks(once)
    assert [a["name"] for a in once] == ["withdrawal_date", "spouse_age"]
    assert [a["name"] for a in twice] == ["withdrawal_date", "spouse_age"]


def test_an_ask_already_put_to_the_member_is_not_repeated() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.facts["spouse_age"] = Fact("spouse_age", None, SOURCE_STATED, asked=True, turn=1)
    kept = sheet.filter_asks([{"name": "spouse_age", "ask_for": "your spouse's age"}])
    assert kept[0]["already_asked"] is True
