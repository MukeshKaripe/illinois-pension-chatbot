"""The ledger is written from the classification the code already makes.

`_input_origins` decides where each resolved value came from, at resolution
time, in code. This turns that per-turn dict into something the next turn can
read, without asking the model anything.
"""

from __future__ import annotations

from pension_chat.agent.ledger import SELF, provenance, recall
from pension_chat.agent.nodes import _ledger_update
from pension_chat.agent.worksheet import SOURCE_RECORD, SOURCE_STATED


def test_a_stated_value_is_recorded_as_stated():
    update = _ledger_update(
        resolved={"total_service_years": 40},
        origins={"total_service_years": SOURCE_STATED},
        turn=2,
    )
    assert recall(update)["total_service_years"] == 40
    assert provenance(update)["total_service_years"] == SOURCE_STATED


def test_a_record_value_is_recorded_as_record():
    update = _ledger_update(
        resolved={"final_average_salary": 72743.62},
        origins={"final_average_salary": SOURCE_RECORD},
        turn=1,
    )
    assert provenance(update)["final_average_salary"] == SOURCE_RECORD


def test_a_value_with_no_recorded_origin_is_not_written():
    """Every resolved input has an origin. One that does not is a bug upstream,
    and guessing a source here would put a false claim on the member's card."""
    update = _ledger_update(resolved={"mystery": 3}, origins={}, turn=1)
    assert update == {}


def test_nothing_resolved_writes_nothing():
    assert _ledger_update(resolved={}, origins={}, turn=1) == {}


def test_the_update_is_keyed_by_subject():
    update = _ledger_update(
        resolved={"tier": 1}, origins={"tier": SOURCE_RECORD}, turn=1
    )
    assert set(update) == {SELF}


def test_sources_are_recorded_at_their_own_rank_whatever_the_order():
    """A value the member stated must not be written at the record's rank
    simply because both resolved on the same turn."""
    update = _ledger_update(
        resolved={"final_average_salary": 72743.62, "total_service_years": 40},
        origins={
            "final_average_salary": SOURCE_RECORD,
            "total_service_years": SOURCE_STATED,
        },
        turn=3,
    )
    assert provenance(update) == {
        "final_average_salary": SOURCE_RECORD,
        "total_service_years": SOURCE_STATED,
    }
