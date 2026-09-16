"""Which facts a turn may use, now that the conversation remembers some.

`_available_facts` is the single place this is decided -- as a calculation
input, as a fact an applicability predicate is tested against, as a figure the
answer may quote, and as a source verification will accept -- so the ledger has
to arrive here or it arrives four times.
"""

from __future__ import annotations

from pension_chat.agent.coverage import BASIS_HYPOTHETICAL, BASIS_RECORD
from pension_chat.agent.nodes import _available_facts


def _state(**over):
    base = {
        "member_facts": {"total_service_years": 21.5, "final_average_salary": 72743.62},
        "fact_ledger": {},
        "coverage": {"article": "11", "record_article": "11", "basis": BASIS_RECORD},
        "employee_record": {"article_number": 11},
        "hypothetical_frame": None,
        "stand_in_facts": False,
    }
    base.update(over)
    return base


def _supposing(**over):
    """A turn reading Article 12 for a member whose file says 11."""
    return _state(
        coverage={
            "article": "12",
            "record_article": "11",
            "basis": BASIS_HYPOTHETICAL,
        },
        hypothetical_frame={"article": "12"},
        **over,
    )


def test_what_the_member_said_beats_the_record():
    state = _state(
        fact_ledger={
            "me": {"total_service_years": {"value": 40, "source": "stated", "turn": 2}}
        }
    )
    facts = _available_facts(state)
    assert facts["total_service_years"] == 40
    assert facts["final_average_salary"] == 72743.62


def test_the_record_still_supplies_what_they_did_not_say():
    state = _state(fact_ledger={"me": {}})
    assert _available_facts(state)["final_average_salary"] == 72743.62


def test_a_ledger_value_from_the_record_does_not_shadow_a_fresher_record():
    """member_facts is rebuilt from the file every turn; a ledger entry at the
    same rank must not pin a stale copy over it."""
    state = _state(
        member_facts={"total_service_years": 22.5},
        fact_ledger={
            "me": {"total_service_years": {"value": 21.5, "source": "record", "turn": 1}}
        },
    )
    assert _available_facts(state)["total_service_years"] == 22.5


def test_a_starved_record_still_leaves_the_member_their_own_words():
    """Under a supposition the file is not usable -- but what they typed is
    theirs, not the file's, and is the one way to a figure."""
    state = _supposing(
        fact_ledger={
            "me": {
                "total_service_years": {"value": 25, "source": "stated", "turn": 2},
                "final_average_salary": {
                    "value": 72743.62,
                    "source": "record",
                    "turn": 1,
                },
            }
        }
    )
    assert _available_facts(state) == {"total_service_years": 25}


def test_a_starved_record_with_nothing_stated_is_still_empty():
    state = _supposing(
        fact_ledger={
            "me": {
                "final_average_salary": {
                    "value": 72743.62,
                    "source": "record",
                    "turn": 1,
                }
            }
        }
    )
    assert _available_facts(state) == {}


def test_an_empty_ledger_leaves_the_old_behaviour_exactly():
    """Every conversation that predates the ledger, and every turn before the
    first value resolves, must behave as it did."""
    assert _available_facts(_state())["total_service_years"] == 21.5
    assert _available_facts(_supposing()) == {}
