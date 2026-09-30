"""fetch_employee_record asks a RecordProvider when one is in force.

The production PBMS read layer and the eval fixtures both implement the same
interface, so the agent code is identical in both. With no provider, the old
Postgres path is untouched.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pension_chat.agent import nodes
from pension_chat.records import EmployeeRecord, SalaryYear, current_provider, use_provider


class _Stub:
    def __init__(self, record, history):
        self.record, self.history, self.asked = record, history, []

    async def find_employees(self, query, scope=None):
        return []

    async def get_record(self, key):
        self.asked.append(key)
        return self.record if key == self.record.key else None

    async def get_salary_history(self, key):
        return self.history


def _tier2() -> EmployeeRecord:
    return EmployeeRecord(
        key="tier2_standard", tier=2, birth_date=date(1968, 3, 1),
        hire_date=date(2013, 6, 1), status="active",
        service_years=Decimal("12.5"), service_source="stored",
    )


def test_to_agent_record_keeps_the_shape_the_agent_reads() -> None:
    rec = _tier2().to_agent_record([SalaryYear(2024, Decimal("80000")), SalaryYear(2025, Decimal("84000"))])
    assert rec["id"] == "tier2_standard"
    assert rec["article_number"] == 8
    assert rec["tier"] == 2
    assert rec["birth_date"] == "1968-03-01"
    assert rec["hire_date"] == "2013-06-01"
    assert rec["total_service_years"] == 12.5
    assert rec["current_annual_salary"] == 84000.0
    assert rec["extra_facts"] == {}


def test_a_missing_field_is_left_out_not_set_to_none() -> None:
    rec = EmployeeRecord(key="no_hire", tier=None, birth_date=date(1970, 1, 1),
                         missing={"hire_date": "not in PBMS"}).to_agent_record([])
    assert "hire_date" not in rec
    assert "total_service_years" not in rec
    assert rec["tier"] is None            # read with [] by nodes.py, so always present


@pytest.mark.asyncio
async def test_fetch_uses_the_provider_when_one_is_in_force() -> None:
    stub = _Stub(_tier2(), [SalaryYear(2025, Decimal("84000"))])
    with use_provider(stub):
        out = await nodes.fetch_employee_record({"employee_id": "tier2_standard"})
    assert stub.asked == ["tier2_standard"]
    assert out["employee_record"]["tier"] == 2
    assert out["salary_history"] == [{"effective_date": "2025-01-01", "annual_salary": 84000.0}]
    assert current_provider() is None      # reset on exit


@pytest.mark.asyncio
async def test_fetch_with_a_record_missing_dates_does_not_raise() -> None:
    rec = EmployeeRecord(key="no_hire", tier=None, birth_date=date(1970, 1, 1))
    with use_provider(_Stub(rec, [])):
        out = await nodes.fetch_employee_record({"employee_id": "no_hire"})
    assert out["employee_record"]["id"] == "no_hire"


@pytest.mark.asyncio
async def test_unknown_key_means_no_record() -> None:
    with use_provider(_Stub(_tier2(), [])):
        out = await nodes.fetch_employee_record({"employee_id": "nobody"})
    assert out["employee_record"] is None


@pytest.mark.asyncio
async def test_without_a_provider_the_postgres_path_runs(monkeypatch) -> None:
    called = []

    async def fake_load(employee_id):
        called.append(employee_id)
        return None

    monkeypatch.setattr(nodes, "_load_record", fake_load)
    await nodes.fetch_employee_record({"employee_id": "abc"})
    assert called == ["abc"]


def test_service_the_record_does_not_hold_stays_unknown_not_zero() -> None:
    # PBMS stores service for few MEABF records; 0 years with origin "record"
    # would be a plausible wrong input that no check catches.
    from pension_chat.agent.facts import build_member_facts

    rec = EmployeeRecord(key="no_stored_service", tier=1, birth_date=date(1969, 1, 1),
                         hire_date=date(2004, 9, 30), status="active",
                         missing={"service_years": "no stored service"}).to_agent_record([])
    facts = build_member_facts(rec, [])
    for name in ("service_years", "total_service_years", "years_of_service", "creditable_service_years"):
        assert name not in facts, name
    assert build_member_facts(_tier2().to_agent_record([]), [])["service_years"] == 12.5


def test_the_agent_record_carries_what_the_employer_needs_to_see() -> None:
    rec = EmployeeRecord(
        key="reciprocal", tier=1, birth_date=date(1966, 1, 1), display_name="K. Ibrahim",
        employer="City of Chicago", department="Aviation", status="active",
        service_years=Decimal("11.5"), service_source="stored",
        reciprocal_service_years=Decimal("9.0"), missing={"status_meaning": "DB7 unmapped"},
    ).to_agent_record([])
    assert rec["display_name"] == "K. Ibrahim"
    assert rec["employer"] == "City of Chicago" and rec["department"] == "Aviation"
    assert rec["reciprocal_service_years"] == 9.0
    assert rec["service_source"] == "stored"
    assert rec["missing"] == {"status_meaning": "DB7 unmapped"}
