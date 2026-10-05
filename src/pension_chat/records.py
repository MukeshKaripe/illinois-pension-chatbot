"""Employee records, from wherever they live, in one shape the agent reads.

The agent never queries a members' database directly. It asks a
`RecordProvider`, and whichever one is in force answers: `PbmsProvider` (Phase 1)
reads the client's SQL Server with fixed parameterised queries; the eval suite's
`FixtureProvider` answers from YAML in memory. With neither, the agent falls
back to the original Postgres path (`nodes._load_record`).

A field PBMS does not have is *absent*, with the reason in `missing` -- MEABF
leaves hire dates and stored service empty for many records, and a plausible
default in their place would be a wrong figure with a real citation on it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SalaryYear:
    year: int
    annual_salary: Decimal


@dataclass(frozen=True)
class EmployeeMatch:
    key: str
    display_name: str
    employer: str | None = None
    status: str | None = None
    department: str | None = None
    birth_year: int | None = None
    first_name: str | None = None
    last_name: str | None = None


@dataclass
class EmployeeRecord:
    key: str
    article_number: int = 8
    display_name: str | None = None
    employer: str | None = None
    department: str | None = None
    birth_date: date | None = None
    hire_date: date | None = None
    termination_date: date | None = None
    status: str | None = None
    tier: int | None = None
    service_years: Decimal | None = None
    service_source: str | None = None
    reciprocal_service_years: Decimal | None = None
    missing: dict[str, str] = field(default_factory=dict)
    # Where the record came from when it is the fund's own ("PBMS"); the agent
    # names the employee and reports the record's gaps only for such a record.
    source: str | None = None

    def to_agent_record(self, history: list[SalaryYear]) -> dict[str, Any]:
        """The dict `fetch_employee_record` has always returned.

        `id`, `article_number` and `tier` are always present (nodes.py reads
        them with []); everything else is left out when unknown, because
        `build_member_facts` reads with .get and treats absence as unknown.
        """
        record: dict[str, Any] = {
            "id": self.key,
            "article_number": self.article_number,
            "tier": self.tier,
            "extra_facts": {},
        }
        if self.birth_date:
            record["birth_date"] = self.birth_date.isoformat()
        if self.hire_date:
            record["hire_date"] = self.hire_date.isoformat()
        if self.status:
            record["employment_status"] = self.status
        if self.service_years is not None:
            record["total_service_years"] = float(self.service_years)
        if history:
            latest = max(history, key=lambda h: h.year)
            record["current_annual_salary"] = float(latest.annual_salary)
        # What an employer looking at this person needs, and what the record
        # could not say and why -- kept, so the agent can report a gap rather
        # than fill it.
        for name in ("display_name", "employer", "department", "service_source"):
            if getattr(self, name):
                record[name] = getattr(self, name)
        if self.termination_date:
            record["termination_date"] = self.termination_date.isoformat()
        if self.reciprocal_service_years is not None:
            record["reciprocal_service_years"] = float(self.reciprocal_service_years)
        if self.missing:
            record["missing"] = dict(self.missing)
        if self.source:
            record["record_source"] = self.source
        return record


class RecordUnavailable(Exception):  # noqa: N818 -- a state, not a programming error
    """The record source could not answer (unreachable, timed out, refused).

    Distinct from "no such employee", which is `None`. The agent reports this
    one to the user rather than answering as if there were no record.
    """


def agent_history(history: list[SalaryYear]) -> list[dict[str, Any]]:
    """Salary rows in the shape `fetch_employee_record` returns."""
    return [
        {"effective_date": date(h.year, 1, 1).isoformat(), "annual_salary": float(h.annual_salary)}
        for h in sorted(history, key=lambda h: h.year)
    ]


class RecordProvider(Protocol):
    async def find_employees(self, query: str, scope: object | None = None) -> list[EmployeeMatch]: ...

    async def get_record(self, key: str) -> EmployeeRecord | None: ...

    async def get_salary_history(self, key: str) -> list[SalaryYear]: ...


@runtime_checkable
class NameSearch(Protocol):
    """A record source that can say which employees a message names (PBMS)."""

    def names_in(self, text: str) -> list[tuple[str, str]]: ...

    async def find_by_name(self, first: str, last: str) -> list[EmployeeMatch]: ...


_provider: ContextVar[RecordProvider | None] = ContextVar("pension_chat_record_provider", default=None)

# The process-wide provider the app installs at startup (RECORD_SOURCE=pbms).
# A provider set with `use_provider` -- the eval suite's fixtures -- outranks it.
_default_provider: RecordProvider | None = None


def set_default_provider(provider: RecordProvider | None) -> None:
    global _default_provider
    _default_provider = provider


def current_provider() -> RecordProvider | None:
    return _provider.get() or _default_provider


MEAB_ID_PATTERN = r"^M-[0-9]{6}$"
_MEAB_ID = re.compile(MEAB_ID_PATTERN)


def normalise_meab_id(text: str | None) -> str | None:
    """`M-######` in canonical form, or None if `text` is not one. Internal key only."""
    candidate = (text or "").strip().upper()
    return candidate if _MEAB_ID.fullmatch(candidate) else None


@contextmanager
def use_provider(provider: RecordProvider) -> Iterator[RecordProvider]:
    token = _provider.set(provider)
    try:
        yield provider
    finally:
        _provider.reset(token)
