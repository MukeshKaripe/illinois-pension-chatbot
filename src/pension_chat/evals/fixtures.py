"""MEABF-shaped employee records for evals, held in memory, never in a database.

Written in relative terms (age, years since hire) and materialised against the
run date: `build_member_facts` computes ages from date.today(), so a fixed
birth date would make every case about a different person each year.
Synthetic values, patterned on PbsMeabDev; no real row is copied.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

from pension_chat.records import EmployeeMatch, EmployeeRecord, SalaryYear


def _years_before(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:                      # 29 February
        return today.replace(year=today.year - years, day=28)


def _history(spec: dict[str, Any], today: date) -> list[SalaryYear]:
    current = Decimal(str(spec["current"]))
    growth = Decimal(str(spec.get("growth", 0)))
    years = int(spec.get("years", 1))
    out = []
    for back in range(years - 1, -1, -1):
        amount = (current / (1 + growth) ** back).quantize(Decimal("0.01"), ROUND_HALF_UP)
        out.append(SalaryYear(today.year - back, amount))
    return out


def load_fixtures(root: Path, today: date) -> dict[str, tuple[EmployeeRecord, list[SalaryYear]]]:
    fixtures: dict[str, tuple[EmployeeRecord, list[SalaryYear]]] = {}
    for path in sorted(root.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        missing = dict(raw.get("missing") or {})
        record = EmployeeRecord(
            key=raw["key"],
            display_name=raw.get("display_name"),
            employer=raw.get("employer"),
            department=raw.get("department"),
            birth_date=_years_before(today, int(raw["age"])) if raw.get("age") is not None else None,
            hire_date=(_years_before(today, int(raw["hired_years_ago"]))
                       if raw.get("hired_years_ago") is not None else None),
            status=raw.get("status"),
            tier=raw.get("tier"),
            service_years=(Decimal(str(raw["service_years"]))
                           if raw.get("service_years") is not None else None),
            service_source=raw.get("service_source"),
            reciprocal_service_years=(Decimal(str(raw["reciprocal_service_years"]))
                                      if raw.get("reciprocal_service_years") is not None else None),
            missing=missing,
        )
        fixtures[record.key] = (record, _history(raw["salary"], today) if raw.get("salary") else [])
    return fixtures


class FixtureProvider:
    def __init__(self, fixtures: dict[str, tuple[EmployeeRecord, list[SalaryYear]]]) -> None:
        self._fixtures = fixtures

    async def find_employees(self, query: str, scope: object | None = None) -> list[EmployeeMatch]:
        q = query.lower()
        return [
            EmployeeMatch(r.key, r.display_name or r.key, r.employer, r.status)
            for r, _ in self._fixtures.values()
            if q in (r.display_name or "").lower() or q == r.key
        ]

    async def get_record(self, key: str) -> EmployeeRecord | None:
        found = self._fixtures.get(key)
        return found[0] if found else None

    async def get_salary_history(self, key: str) -> list[SalaryYear]:
        found = self._fixtures.get(key)
        return list(found[1]) if found else []
