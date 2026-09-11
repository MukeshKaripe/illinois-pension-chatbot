"""A conversation keeps the record it loaded on its first turn (spec §6).

Profiles became editable, and a profile edited mid-conversation must change new
chats, not one already under way -- whose earlier answers rest on the facts it
started with. So the first load is pinned in durable state and later turns read
the pin rather than the database.
"""

from __future__ import annotations

import pytest

from pension_chat.agent import nodes
from pension_chat.agent.state import DURABLE_KEYS, PER_TURN_KEYS

RECORD = {
    "id": "11111111-1111-1111-1111-111111111111",
    "article_number": 11,
    "tier": 1,
    "birth_date": "1967-02-11",
    "hire_date": "1995-06-05",
    "employment_status": "active",
    "total_service_years": 31.0,
    "current_annual_salary": 86000.0,
}
HISTORY = [
    {"effective_date": "2025-01-01", "annual_salary": 84000.0},
    {"effective_date": "2026-01-01", "annual_salary": 86000.0},
]
PIN = {"record": RECORD, "salary_history": HISTORY}


@pytest.mark.asyncio
async def test_a_later_turn_reads_the_pin_not_the_database(monkeypatch):
    def no_database(**_kwargs):
        raise AssertionError("a pinned conversation re-read the database")

    monkeypatch.setattr(nodes, "get_db_session", no_database)
    out = await nodes.fetch_employee_record({"employee_id": RECORD["id"], "pinned_record": PIN})

    assert out["employee_record"] == RECORD
    assert out["salary_history"] == HISTORY
    assert out["member_facts"], "facts are still built from the pinned record"
    assert out["pinned_record"] == PIN


@pytest.mark.asyncio
async def test_a_pin_taken_from_another_profile_is_not_used(monkeypatch):
    asked_for: list[object] = []

    def database(**kwargs):
        asked_for.append(kwargs.get("employee_id"))
        raise RuntimeError("no database in unit tests")

    monkeypatch.setattr(nodes, "get_db_session", database)
    other = "22222222-2222-2222-2222-222222222222"
    out = await nodes.fetch_employee_record({"employee_id": other, "pinned_record": PIN})

    assert asked_for == [other]
    assert out["employee_record"] is None


def test_the_pin_survives_between_turns():
    assert "pinned_record" in DURABLE_KEYS
    assert "pinned_record" not in PER_TURN_KEYS
