"""Tests for what the conversation remembers, and how it knows it.

The member's own words are the highest-provenance fact class in this design --
`guardrails` traces figures to "the member's own messages" as a source distinct
from their record -- and they were the only class discarded at the end of every
turn. These tests pin the precedence rules that make keeping them safe.
"""

from __future__ import annotations

from pension_chat.agent.ledger import (
    SELF,
    outranks,
    provenance,
    recall,
    remember,
    stated_only,
)
from pension_chat.agent.state import merge_dicts
from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_DERIVED,
    SOURCE_RECORD,
    SOURCE_STATED,
)


def _apply(ledger: dict, update: dict) -> dict:
    """Apply a ledger update the way the LangGraph reducer would."""
    return merge_dicts(ledger, update)


def test_what_the_member_said_outranks_their_record():
    ledger = _apply({}, remember({}, SELF, {"total_service_years": 21.5}, SOURCE_RECORD, 1))
    ledger = _apply(
        ledger, remember(ledger, SELF, {"total_service_years": 40}, SOURCE_STATED, 2)
    )
    assert recall(ledger)["total_service_years"] == 40
    assert provenance(ledger)["total_service_years"] == SOURCE_STATED


def test_the_record_does_not_overwrite_what_the_member_said():
    """Re-reading the file every turn must not quietly undo their correction."""
    ledger = _apply({}, remember({}, SELF, {"total_service_years": 40}, SOURCE_STATED, 2))
    ledger = _apply(
        ledger, remember(ledger, SELF, {"total_service_years": 21.5}, SOURCE_RECORD, 3)
    )
    assert recall(ledger)["total_service_years"] == 40


def test_a_later_statement_supersedes_an_earlier_one():
    """Equal rank replaces: the member correcting themselves is still them."""
    ledger = _apply({}, remember({}, SELF, {"final_average_salary": 75000}, SOURCE_STATED, 2))
    ledger = _apply(
        ledger, remember(ledger, SELF, {"final_average_salary": 80000}, SOURCE_STATED, 5)
    )
    assert recall(ledger)["final_average_salary"] == 80000
    assert ledger[SELF]["final_average_salary"]["turn"] == 5


def test_an_assumption_never_displaces_anything_better():
    ledger = _apply({}, remember({}, SELF, {"retirement_age": 60}, SOURCE_DERIVED, 1))
    ledger = _apply(ledger, remember(ledger, SELF, {"retirement_age": 67}, SOURCE_ASSUMED, 2))
    assert recall(ledger)["retirement_age"] == 60


def test_subjects_do_not_see_each_others_facts():
    """A comparison must not borrow one subject's salary for another."""
    ledger = _apply({}, remember({}, SELF, {"final_average_salary": 72743.62}, SOURCE_RECORD, 1))
    ledger = _apply(
        ledger,
        remember(ledger, "as_a_firefighter", {"total_service_years": 21.5}, SOURCE_STATED, 2),
    )
    assert "final_average_salary" not in recall(ledger, "as_a_firefighter")
    assert "total_service_years" not in recall(ledger, SELF)


def test_stated_only_keeps_what_is_theirs_and_drops_the_file():
    ledger = _apply({}, remember({}, SELF, {"total_service_years": 40}, SOURCE_STATED, 2))
    ledger = _apply(
        ledger, remember(ledger, SELF, {"final_average_salary": 72743.62}, SOURCE_RECORD, 1)
    )
    assert stated_only(ledger) == {"total_service_years": 40}


def test_a_none_value_is_not_recorded():
    """Extraction returns nulls for what it could not find; those are not facts."""
    assert remember({}, SELF, {"retirement_age": None}, SOURCE_STATED, 1) == {}


def test_recording_nothing_returns_an_empty_update():
    """An empty update must merge to nothing rather than clearing the key."""
    ledger = _apply({}, remember({}, SELF, {"total_service_years": 40}, SOURCE_STATED, 1))
    ledger = _apply(ledger, remember(ledger, SELF, {}, SOURCE_STATED, 2))
    assert recall(ledger)["total_service_years"] == 40


def test_outranks_is_reflexive_for_equal_sources():
    assert outranks(SOURCE_STATED, SOURCE_STATED) is True
    assert outranks(SOURCE_RECORD, SOURCE_STATED) is False
    assert outranks(SOURCE_STATED, SOURCE_RECORD) is True


def test_an_unknown_source_ranks_below_every_known_one():
    """A source this module has not heard of must not displace one it has."""
    ledger = _apply({}, remember({}, SELF, {"tier": 1}, SOURCE_ASSUMED, 1))
    ledger = _apply(ledger, remember(ledger, SELF, {"tier": 2}, "guessed_somewhere", 2))
    assert recall(ledger)["tier"] == 1
