"""PBMS as a RecordProvider: the fund's own records, read live.

The mapping is pure (`record_from_rows`, `salary_from_rows`, `match_from_row`)
so it is tested on synthetic rows. A field PBMS leaves empty is absent, with
the reason in `missing`: MEABF leaves the hire date empty on 80,763 live rows
(spec §6.1), and a plausible default there would be a wrong figure with a real
citation on it. Name search is added in `names.py` (Task 4).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from pension_chat.pbms.names import NameDirectory
from pension_chat.pbms.queries import SERVICE_FROM_PAYROLL
from pension_chat.records import EmployeeMatch, EmployeeRecord, SalaryYear, normalise_meab_id

_TIER_CODES = {"1": 1, "2": 2, "3": 3}
# Employee_Status_Cd -> the agent's employment_status; extended only from codes
# spec §6.2 found described. Anything else is reported, not guessed.
_STATUS = {"A": "active"}
SERVICE_FROM_PAYROLL_SOURCE = "counted from payroll (months with a pension deduction)"


class _Client(Protocol):
    async def fetch(self, name: str, **params: Any) -> list[dict[str, Any]]: ...

    async def warm(self, connections: int = 8) -> None: ...

    async def close(self) -> None: ...


def _day(value: Any) -> date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def tier_from_participation(start: date | None) -> int | None:
    """Section 1-160 as Article 8 draws it; 2017 is left open (Tier 2 or 3)."""
    if start is None:
        return None
    if start.year < 2011:
        return 1
    if start.year <= 2016:
        return 2
    if start.year == 2017:
        return None
    return 3


def reconcile_tier(code: Any, start: date | None) -> tuple[int | None, str | None]:
    """PBMS's tier is a prior, not an oracle: agree, or say where it and the dates part."""
    raw = _text(code) or ""
    on_file = _TIER_CODES.get(raw)
    derived = tier_from_participation(start)
    when = start.isoformat() if start else None
    if on_file is not None:
        if derived is None or derived == on_file:
            return on_file, None
        return on_file, (f"PBMS records Tier {on_file}, but participation beginning {when} "
                         f"indicates Tier {derived}")
    label = f"PBMS tier code {raw!r} is not Tier 1, 2 or 3" if raw else "PBMS holds no tier code"
    if derived is not None:
        return derived, f"{label}; Tier {derived} is taken from participation beginning {when}"
    return None, f"{label}, and the participation date ({when or 'not on file'}) does not settle it"


def _display_name(row: dict[str, Any]) -> str | None:
    return " ".join(p for p in (_text(row.get("First_Name")), _text(row.get("Last_Name"))) if p) or None


def _employer(row: dict[str, Any]) -> str | None:
    return _text(row.get("Er_Type_Short_Desc")) or _text(row.get("ER_Type_Cd"))


def _status(row: dict[str, Any]) -> tuple[str | None, str | None]:
    code = _text(row.get("Employee_Status_Cd"))
    return (_STATUS.get(code), code) if code else (None, None)


def match_from_row(row: dict[str, Any]) -> EmployeeMatch:
    status, code = _status(row)
    key = str(row["MEAB_Id"]).strip().upper()
    birth_year = row.get("Birth_Year")
    return EmployeeMatch(
        key=key,
        display_name=_display_name(row) or "(no name on file)",
        employer=_employer(row),
        status=status or (f"PBMS status {code}" if code else None),
        department=_text(row.get("Department_Short_Desc")),
        birth_year=int(birth_year) if birth_year else None,
        first_name=_text(row.get("First_Name")),
        last_name=_text(row.get("Last_Name")),
    )


def record_from_rows(row: dict[str, Any], salary_rows: list[dict[str, Any]]) -> EmployeeRecord:
    missing: dict[str, str] = {}

    hire = _day(row.get("Hire_Date"))
    first_payroll = _day(row.get("First_Payroll_Date"))
    if hire is None and first_payroll is not None:
        hire = first_payroll
        missing["hire_date"] = (f"PBMS holds no hire date; the first payroll date "
                                f"({first_payroll.isoformat()}) is used in its place")
    elif hire is None:
        missing["hire_date"] = "PBMS holds neither a hire date nor a first payroll date"

    birth = _day(row.get("Birth_Date"))
    if birth is None:
        missing["birth_date"] = "PBMS holds no date of birth"

    tier, tier_note = reconcile_tier(row.get("Tier_Type_Cd"), hire)
    if tier_note:
        missing["tier"] = tier_note

    status, code = _status(row)
    if code and status is None:
        missing["employment_status"] = f"PBMS status code {code!r} is not one the chatbot maps yet"

    service: Decimal | None = None
    source: str | None = None
    months = sum(int(r.get("Contribution_Months") or 0) for r in salary_rows)
    if not SERVICE_FROM_PAYROLL:
        missing["service_years"] = "PBMS holds no service figure the chatbot can read yet (client question 38)"
    elif months:
        service = (Decimal(months) / 12).quantize(Decimal("0.01"), ROUND_HALF_UP)
        source = SERVICE_FROM_PAYROLL_SOURCE
    else:
        missing["service_years"] = "PBMS holds no payroll periods with a pension deduction"

    if (_text(row.get("Reciprocal_Flg")) or "").upper() == "Y":
        missing["reciprocal_service_years"] = "PBMS flags reciprocal service but holds no amount of it"

    return EmployeeRecord(
        key=str(row["MEAB_Id"]).strip().upper(),
        article_number=8,
        display_name=_display_name(row),
        employer=_employer(row),
        department=_text(row.get("Department_Short_Desc")),
        birth_date=birth,
        hire_date=hire,
        termination_date=_day(row.get("Seperation_Date")),
        status=status,
        tier=tier,
        service_years=service,
        service_source=source,
        missing=missing,
        source="PBMS",
    )


def salary_from_rows(rows: list[dict[str, Any]], *, as_of: date) -> list[SalaryYear]:
    """Complete calendar years only: this year's part-year pay is not an annual salary."""
    return [
        SalaryYear(int(r["Year"]), Decimal(str(r["Salary"])))
        for r in rows
        if r.get("Salary") and int(r["Year"]) < as_of.year
    ]


class PbmsProvider:
    def __init__(self, client: _Client, *, today: Callable[[], date] = date.today,
                 salary_ttl_s: float = 60.0) -> None:
        self._client = client
        self._today = today
        self._ttl = salary_ttl_s
        self._salary: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._names: NameDirectory | None = None

    async def find_employees(self, query: str, scope: object | None = None) -> list[EmployeeMatch]:
        """The active member a MEAB ID (an internal `ref`) names; nothing else is searched here."""
        meab_id = normalise_meab_id(query)
        if meab_id is None:
            return []
        return [match_from_row(r) for r in await self._client.fetch("resolve_meab_id", meab_id=meab_id)]

    async def load_names(self) -> int:
        """(Re)load the directory of active members' names; the old one stays if this fails."""
        rows = await self._client.fetch("name_directory")
        self._names = NameDirectory((r["First_Name"], r["Last_Name"]) for r in rows)
        return len(self._names)

    @property
    def names_ready(self) -> bool:
        return self._names is not None

    def names_in(self, text: str) -> list[tuple[str, str]]:
        return self._names.names_in(text) if self._names is not None else []

    async def find_by_name(self, first: str, last: str) -> list[EmployeeMatch]:
        rows = await self._client.fetch("resolve_name", first_name=first, last_name=last)
        return [match_from_row(r) for r in rows]

    async def get_record(self, key: str) -> EmployeeRecord | None:
        meab_id = normalise_meab_id(key)
        if meab_id is None:
            return None
        rows, salary = await asyncio.gather(
            self._client.fetch("record", meab_id=meab_id), self._salary_rows(meab_id)
        )
        return record_from_rows(rows[0], salary) if rows else None

    async def get_salary_history(self, key: str) -> list[SalaryYear]:
        meab_id = normalise_meab_id(key)
        if meab_id is None:
            return []
        return salary_from_rows(await self._salary_rows(meab_id), as_of=self._today())

    async def _salary_rows(self, meab_id: str) -> list[dict[str, Any]]:
        # `fetch_employee_record` asks for the record and then the history;
        # one read serves both.
        now = time.monotonic()
        hit = self._salary.get(meab_id)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        rows = await self._client.fetch("salary_years", meab_id=meab_id)
        self._salary[meab_id] = (now, rows)
        if len(self._salary) > 256:
            del self._salary[min(self._salary, key=lambda k: self._salary[k][0])]
        return rows

    async def warm(self) -> None:
        """Open and prepare the connection pool, so the first question pays no setup."""
        await self._client.warm()

    async def close(self) -> None:
        await self._client.close()
