"""Tests for what survives a turn and what does not.

Before the graph was checkpointed, every turn began from a blank blackboard and
the question did not arise. It does now: with a checkpointer, any key a node
does not write persists into the next turn, and several of those would be
actively harmful if they did.

The worst is `calculation_result`. Verification builds the set of figures an
answer is allowed to state from this turn's calculation, the member's record,
the retrieved statute and what the member typed -- deliberately never from what
the assistant said earlier, so that a figure this system stated once does not
become its own justification for stating it again. A result left lying around
across turns would put that back, through the front door, with the guardrail's
own blessing.
"""

from __future__ import annotations

import pytest

from pension_chat.agent.state import (
    CLEAR,
    DURABLE_KEYS,
    PER_TURN_KEYS,
    PensionAgentState,
    initial_state,
    merge_dicts,
    merge_lists,
    turn_input,
)


def test_every_key_is_classified():
    """A new state key must be declared durable or per-turn, not forgotten."""
    assert DURABLE_KEYS | PER_TURN_KEYS == set(PensionAgentState.__annotations__)
    assert not DURABLE_KEYS & PER_TURN_KEYS


@pytest.mark.parametrize(
    "key",
    [
        "calculation_result",   # the one that matters most
        "citations",
        "retrieved_sections",
        "member_facts",
        "missing_inputs",
        "execution_blocked",
        "guardrail_report",
        "final_answer",
    ],
)
def test_a_turns_own_findings_do_not_survive_it(key):
    assert key in PER_TURN_KEYS
    assert key not in DURABLE_KEYS


@pytest.mark.parametrize(
    "key",
    ["messages", "selected_provision", "baseline", "assumptions", "scenario_set"],
)
def test_what_the_conversation_established_does_survive(key):
    assert key in DURABLE_KEYS


def test_initial_state_defaults_every_key():
    assert set(initial_state("q", "s")) == set(PensionAgentState.__annotations__)


# -- Clearing, not merging -----------------------------------------

def test_a_merged_key_is_cleared_rather_than_merged_empty():
    """`{}` would merge to nothing and leave last turn's value in place."""
    assert merge_dicts({"age": 56}, {}) == {"age": 56}
    assert merge_dicts({"age": 56}, CLEAR) == {}
    assert merge_lists([1, 2], []) == [1, 2]
    assert merge_lists([1, 2], CLEAR) == []


@pytest.mark.parametrize("key", ["member_facts", "recipes", "resolved_inputs", "timings"])
def test_turn_input_clears_the_merged_keys(key):
    assert turn_input("q", "s")[key] is CLEAR


def test_turn_input_leaves_the_durable_keys_alone():
    """Anything it wrote would overwrite what the thread already holds."""
    update = turn_input("q", "s", "e")

    assert not (DURABLE_KEYS & set(update)) - {
        "session_id", "employee_id", "messages", "turn_index"
    }
    # The new message is appended by the reducer, not substituted for history.
    assert len(update["messages"]) == 1
    assert update["turn_index"] == 1


def test_turn_input_resets_the_previous_turns_result():
    assert turn_input("q", "s")["calculation_result"] is None


def test_the_fact_ledger_survives_a_turn():
    """The whole point. A value the member gave in turn two is still theirs in
    turn four, so the assistant does not ask again."""
    from pension_chat.agent.state import DURABLE_KEYS, PER_TURN_KEYS, turn_input

    assert "fact_ledger" in DURABLE_KEYS
    assert "fact_ledger" not in PER_TURN_KEYS
    assert "fact_ledger" not in turn_input("next question", "session-1")


def test_the_fact_ledger_starts_empty():
    from pension_chat.agent.state import initial_state

    assert initial_state("q", "session-1")["fact_ledger"] == {}


def test_the_fact_ledger_merges_rather_than_replacing():
    """Two subjects written on different turns must both survive."""
    from pension_chat.agent.state import merge_dicts

    first = {"me": {"total_service_years": {"value": 40, "source": "stated", "turn": 1}}}
    second = {"as_a_firefighter": {"tier": {"value": 1, "source": "stated", "turn": 2}}}
    merged = merge_dicts(first, second)
    assert set(merged) == {"me", "as_a_firefighter"}
