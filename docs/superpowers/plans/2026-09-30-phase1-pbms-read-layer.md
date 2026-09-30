# Phase 1: MEABF Read Layer and Demo Path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An employer types a MEAB ID, confirms the person, and asks about them. The agent reads that employee's record live from PBMS (SQL Server), read-only, through a fixed set of statements.

**Architecture:**
- A new package, `src/pension_chat/pbms/`, holds four parts:
  - a guard: `PbmsClient`, which runs only named, read-only, allow-listed statements;
  - the SQL: `queries.py`;
  - the mapping: `provider.py`, where `PbmsProvider` implements the existing `RecordProvider`;
  - the factory: `build_record_provider`.
- The app installs the provider process-wide when `RECORD_SOURCE=pbms`.
- A conversation gains a **subject**: `sessions.subject_meab_id`, passed to the graph as `subject_meab_id`. `fetch_employee_record` loads the record by that key.
- `triage` turns a MEAB ID typed in a message into a confirm-first suggestion.
- The UI gets an Employee bar and a suggestion card.

**Tech Stack:**
- Python 3.12, FastAPI, LangGraph, SQLAlchemy/Alembic (Postgres);
- `pyodbc` with ODBC Driver 18 for SQL Server;
- Next.js 16 / React 19 with `node --test`;
- pytest (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md`. Read it, including §6.1, "Measured".

## Global Constraints

- **Credentials.** The `sa` login is used **for reads only**. Code enforces this, not convention. Credentials come from `PBMS_SQL_SERVER`, `PBMS_SQL_DATABASE`, `PBMS_SQL_USER` and `PBMS_SQL_PASSWORD`. They are never logged, printed, or placed in an exception message or `repr`.
- **No real data in the repo.** No real PBMS data may appear in any fixture, test, log or commit. Scripts that touch PBMS print only counts, rates, codes and timings.
- **Never write to PBMS.** Nothing is ever written to it.
- **Never read personal identifiers.** No statement ever names `Ssn`, `SSN`, `eSSN`, any `CC*` column, `PbsAddress_*`, `Pbs*_Phone`, `PbsEmail`, `Maiden_Name` or `MemberID`.
- **Session settings.** Every PBMS connection uses `ApplicationIntent=ReadOnly` and `APP=pension-chat-readonly`, and runs at `READ UNCOMMITTED`. Each query has a 5 s timeout.
- **The MEAB ID is the only stored subject key.** Its canonical form is `M-` followed by six digits (`^M-[0-9]{6}$`). An Employee ID is only a way to find a MEAB ID.
- **Salary is read through `T_Pen_Ded_Master`.** No statement filters `T_Pen_Ded_Detail` by `Person_Info_Seq_No`: that column is unindexed, and one member's read scans 46.7M rows.
- **`T_Employees.Status = 'A'` means a live row.** Employment status comes from `Employee_Status_Cd`.
- **Only tier codes `1`, `2` and `3` map to a tier.** Any other code is recorded in `missing["tier"]`.
- **Gaps stay gaps.** A field PBMS leaves empty is absent, with a reason in `missing`. It is never defaulted, and nothing falls back to Postgres.
- **Default behaviour is unchanged.** With `RECORD_SOURCE` unset, which means `postgres`, local development, tests and evals behave exactly as they do today.
- **Commits.** Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**Rulings this plan makes against the spec** (the executor carries them; no further ledger entry is needed):
- **R1: pyodbc and a small in-house pool, not `aioodbc`.** Queries run through `asyncio.to_thread`, with at most 4 connections and one retry on a stale pooled connection. This means fewer third-party moving parts, and the guard is identical. *Cost if wrong:* swapping in aioodbc later touches only `connection.py`.
- **R2: `record` and `salary_years` are keyed by MEAB ID** (the linker's unique index), not by sequence numbers. It is one parameter, and it is indexed either way.
- **R3: Employee lookup lives under the session.** `GET /api/v1/sessions/{id}/employees/lookup` checks that the caller owns the session, instead of an open `/employees/lookup`, so names are never readable by someone with no session. *Cost if wrong:* moving a route.
- **R4: the ID-in-message suggestion rides in `tool_calls`** (`{"type": "subject_suggestion", …}`), the way the calculation card does, instead of a separate SSE event. `done` already carries `tool_calls`, and a reload restores the card from the stored message.
- **R6: the chip is restored with `GET /sessions/{id}/subject`,** not by adding `subject` to `done` and to a `GET /sessions/{id}`. That endpoint does not exist, and one small route does the job on reload and after a set.
- **R5: the current calendar year is left out of salary history** because it is incomplete. Otherwise `current_annual_salary` would be a part-year's pay. Its contribution months still count toward service.

## Review Focus

1. **A pooled connection the server has dropped:** the next query retries once on a fresh connection instead of failing the turn (Task 2).
2. **A query in September:** the part-year salary is not reported as the "current annual salary" (Task 3).
3. **A MEAB ID typed in lower case, or with spaces** (`" m-012345 "`): it resolves to `M-012345` in the lookup, in a message suggestion and in `PUT` (Tasks 3, 5, 6, 7).
4. **An Employee ID shared across employers:** every match is returned and none is auto-selected, and `PUT` accepts only a MEAB ID (Task 6).
5. **A connection failure whose driver message echoes the connection string:** the `PbmsUnavailable` text and `repr(client)` never contain the password (Task 2).

---

### Task 1: Settings, the driver, and the discovery script

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `src/pension_chat/config.py` (after `deployment_article`, about line 166)
- Create: `scripts/pbms_discover.py`
- Modify: `.env` (local only, never committed): uncomment the four `PBMS_SQL_*` lines (lines 92–95)
- Modify: `docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md` (add §6.2)
- Test: `tests/test_pbms_config.py`

**Interfaces:**
- Produces:
  - `Settings.record_source: Literal["postgres", "pbms"]` (default `"postgres"`);
  - `Settings.pbms_sql_server`, `pbms_sql_database` and `pbms_sql_user: str | None`;
  - `Settings.pbms_sql_password: SecretStr | None`;
  - `Settings.pbms_query_timeout_s: float = 5.0`;
  - the spec's §6.2, which records `SALARY_RULE` (one of `"gross"`, `"rate"` or `"capped"`) and `SERVICE_FROM_PAYROLL` (bool). Task 2 reads both.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pbms_config.py
"""The PBMS settings exist, default to off, and never show the password."""
from __future__ import annotations

from pension_chat.config import Settings


def _settings(**overrides):
    base = dict(
        azure_openai_api_key="k", azure_openai_endpoint="https://x", azure_search_api_key="k",
        azure_search_endpoint="https://x", pghost="h", pguser="u", pgpassword="p",
        _env_file=None,
    )
    return Settings(**{**base, **overrides})


def test_the_record_source_defaults_to_postgres() -> None:
    assert _settings().record_source == "postgres"


def test_the_password_is_a_secret() -> None:
    s = _settings(record_source="pbms", pbms_sql_password="hunter2-xyz")
    assert "hunter2-xyz" not in repr(s)
    assert s.pbms_sql_password is not None
    assert s.pbms_sql_password.get_secret_value() == "hunter2-xyz"
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_pbms_config.py -v`
Expected: FAIL, with `AttributeError: 'Settings' object has no attribute 'record_source'`. If `Settings(...)` rejects `_env_file`, check the pydantic-settings version: `_env_file` is its documented init kwarg.

- [ ] **Step 3: Add the settings and the driver**

In `src/pension_chat/config.py`, add `SecretStr` to the pydantic import (`from pydantic import SecretStr, model_validator`) and `from typing import Literal`. Then insert this directly after `deployment_article: int | None = None`:

```python
    # ── Employee records (Phase 1) ─────────────────────────────────
    # Where an employee's record comes from. "pbms" reads the client's SQL
    # Server live and read-only through `pension_chat.pbms`; "postgres" is the
    # original path. Evals ignore this -- they install their own provider.
    record_source: Literal["postgres", "pbms"] = "postgres"
    pbms_sql_server: str | None = None
    pbms_sql_database: str | None = None
    pbms_sql_user: str | None = None
    pbms_sql_password: SecretStr | None = None
    pbms_query_timeout_s: float = 5.0
```

In `pyproject.toml`, add `"pyodbc>=5.1.0",` to `[project] dependencies`, then run `uv sync --all-extras`.

- [ ] **Step 4: Run it to see it pass**

Run: `uv run pytest tests/test_pbms_config.py -v`
Expected: 2 passed.

- [ ] **Step 5: Uncomment the PBMS keys in `.env`**

In `.env`, remove the leading `#` from the four lines `#PBMS_SQL_SERVER=…`, `#PBMS_SQL_DATABASE=…`, `#PBMS_SQL_USER=…` and `#PBMS_SQL_PASSWORD=…`. Edit only those four characters, and do not print the file. `.env` is git-ignored: confirm that with `git check-ignore .env`, which should print `.env`.

- [ ] **Step 6: Write the discovery script**

```python
# scripts/pbms_discover.py
"""Phase 1 discovery against PBMS: which salary PBMS uses, and how it holds service.

Read-only and aggregate-only. It prints counts, rates, codes and medians over a
sample -- never a name, an ID, a date of birth or an amount belonging to one
person. Personal values live in memory only while they are compared.

    uv run python scripts/pbms_discover.py codes
    uv run python scripts/pbms_discover.py links
    uv run python scripts/pbms_discover.py salary --link-table <table from `links`>
    uv run python scripts/pbms_discover.py service --link-table <table from `links`>
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SAMPLE = 60
_MONEY = {"gross": "SUM(d.Gross)", "rate": "MAX(d.Ann_Sal)", "capped": "SUM(d.Capped_Salary)"}


def _connect():
    import pyodbc

    from pension_chat.config import get_settings

    s = get_settings()
    if not (s.pbms_sql_server and s.pbms_sql_database and s.pbms_sql_user and s.pbms_sql_password):
        raise SystemExit("PBMS_SQL_* are not set in .env")

    def braced(v: str) -> str:
        return "{" + v.replace("}", "}}") + "}"

    conn = pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={s.pbms_sql_server};DATABASE={braced(s.pbms_sql_database)};"
        f"UID={braced(s.pbms_sql_user)};PWD={braced(s.pbms_sql_password.get_secret_value())};"
        "Encrypt=yes;TrustServerCertificate=yes;ApplicationIntent=ReadOnly;APP=pension-chat-discovery;",
        timeout=20, autocommit=True, readonly=True,
    )
    conn.timeout = 120
    conn.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
    return conn


def _rows(conn, sql: str, *args):
    cur = conn.cursor()
    cur.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def codes(conn) -> None:
    print("== Employee_Status_Cd on live rows (code, count)")
    for r in _rows(conn, "SELECT e.Employee_Status_Cd AS code, COUNT(*) AS n FROM dbo.T_Employees e "
                         "WHERE e.Status = 'A' GROUP BY e.Employee_Status_Cd ORDER BY 2 DESC"):
        print(f"  {r['code']!r}: {r['n']}")
    print("== lookup tables that describe Employee_Status_Cd")
    for r in _rows(conn, "SELECT OBJECT_NAME(c.object_id) AS t FROM sys.columns c JOIN sys.tables t "
                         "ON t.object_id = c.object_id WHERE c.name = 'Employee_Status_Cd' "
                         "AND OBJECT_NAME(c.object_id) LIKE '%Type%'"):
        print(f"  {r['t']}")
    print("== Status codes on T_Pen_Ded_Master / T_Pen_Ded_Detail for 2024 (code, count)")
    for r in _rows(conn, "SELECT m.Status AS code, COUNT(*) AS n FROM dbo.T_Pen_Ded_Master m "
                         "WHERE m.Year = 2024 GROUP BY m.Status"):
        print(f"  master {r['code']!r}: {r['n']}")
    # A sample, aggregated on the server: never pull detail rows to the client.
    for r in _rows(conn, "SELECT x.code, COUNT(*) AS n FROM (SELECT TOP 200000 d.Status AS code FROM dbo.T_Pen_Ded_Master m "
                         "JOIN dbo.T_Pen_Ded_Detail d ON d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No "
                         "WHERE m.Year = 2024) x GROUP BY x.code"):
        print(f"  detail {r['code']!r}: {r['n']}")


def links(conn) -> None:
    print("== tables holding both Annuity_Seq_No and Person_Info_Seq_No (candidates for --link-table)")
    for r in _rows(conn, "SELECT OBJECT_NAME(a.object_id) AS t FROM sys.columns a JOIN sys.columns b "
                         "ON b.object_id = a.object_id AND b.name = 'Person_Info_Seq_No' "
                         "JOIN sys.tables t ON t.object_id = a.object_id WHERE a.name = 'Annuity_Seq_No'"):
        print(f"  {r['t']}")


def _sample(conn, link: str):
    """Retirees PBMS has worked an annuity for: (person, PBMS annual FAS, PBMS service)."""
    return _rows(conn, f"SELECT TOP {SAMPLE} x.Person_Info_Seq_No AS person, s.FAS_Monthly * 12 AS fas, "
                       f"s.MEAB_Svc AS svc FROM dbo.[{link}] x JOIN dbo.T_Annuity_Service_Salary s "
                       "ON s.Annuity_Seq_No = x.Annuity_Seq_No WHERE s.FAS_Monthly > 0 "
                       "ORDER BY x.Annuity_Seq_No DESC")


def _years(conn, person: int, status_filter: bool):
    where = "AND m.Status = 'A' AND d.Status = 'A'" if status_filter else ""
    return _rows(conn, "SELECT YEAR(d.PPE) AS y, SUM(d.Gross) AS gross, MAX(d.Ann_Sal) AS rate, "
                       "SUM(d.Capped_Salary) AS capped, "
                       "COUNT(DISTINCT CASE WHEN d.Ded > 0 THEN MONTH(d.PPE) END) AS months "
                       "FROM dbo.T_Pen_Ded_Master m JOIN dbo.T_Pen_Ded_Detail d "
                       "ON d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No "
                       f"WHERE m.Person_Info_Seq_No = ? {where} GROUP BY YEAR(d.PPE)", person)


def _best_four(by_year: dict[int, float]) -> float | None:
    """Highest average of 4 consecutive years within the last 10 years on file."""
    if not by_year:
        return None
    last = max(by_year)
    window = [y for y in range(last - 9, last + 1)]
    best = None
    for start in window[:-3]:
        run = [by_year.get(y) for y in range(start, start + 4)]
        if all(v is not None for v in run):
            avg = sum(run) / 4
            best = avg if best is None else max(best, avg)
    return best


def salary(conn, link: str) -> None:
    people = _sample(conn, link)
    print(f"== {len(people)} retirees; share whose best-4-year average is within 1% of PBMS's FAS")
    for status_filter in (True, False):
        hits = {k: 0 for k in _MONEY}
        for p in people:
            years = _years(conn, p["person"], status_filter)
            for k in _MONEY:
                fas = _best_four({r["y"]: float(r[k] or 0) for r in years if r[k]})
                if fas and abs(fas - float(p["fas"])) <= 0.01 * float(p["fas"]):
                    hits[k] += 1
        label = "Status='A' rows only" if status_filter else "all rows"
        print(f"  {label}: " + ", ".join(f"{k}={hits[k]}/{len(people)}" for k in _MONEY))


def service(conn, link: str) -> None:
    people = _sample(conn, link)
    pbms, counted, close = [], [], 0
    for p in people:
        months = sum(int(r["months"] or 0) for r in _years(conn, p["person"], True))
        if p["svc"] is None:
            continue
        pbms.append(float(p["svc"]))
        counted.append(months / 12)
        close += abs(float(p["svc"]) - months / 12) <= 0.5
    print(f"== {len(pbms)} retirees with a PBMS service figure")
    if pbms:
        print(f"  median PBMS MEAB_Svc: {statistics.median(pbms):.2f}")
        print(f"  median counted-from-payroll years: {statistics.median(counted):.2f}")
        print(f"  within half a year: {close}/{len(pbms)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["codes", "links", "salary", "service"])
    ap.add_argument("--link-table")
    args = ap.parse_args()
    conn = _connect()
    try:
        if args.what in ("salary", "service") and not args.link_table:
            raise SystemExit("--link-table is required; run `links` first")
        {"codes": lambda: codes(conn), "links": lambda: links(conn),
         "salary": lambda: salary(conn, args.link_table), "service": lambda: service(conn, args.link_table)}[args.what]()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run discovery**

Run these one at a time, and read each output:
```
uv run python scripts/pbms_discover.py codes
uv run python scripts/pbms_discover.py links
uv run python scripts/pbms_discover.py salary --link-table <a table printed by links>
uv run python scripts/pbms_discover.py service --link-table <same table>
```
Expected: counts only. If `links` prints nothing, record "no annuity link table: salary rule unverified" and use the defaults below.

- [ ] **Step 8: Record the findings in the spec (§6.2)**

Append to the spec after §6.1:

```markdown
### 6.2 Settled by Task 1 (date)

- **Salary rule:** `SALARY_RULE = "<gross|rate|capped>"`, because <the candidate with the most matches>/<n> retirees matched PBMS's FAS within 1% (<the numbers printed>). Status filter: <'A' rows only | all rows>.
- **Service:** `SERVICE_FROM_PAYROLL = <True|False>`, because <n>/<m> were within half a year of PBMS's `MEAB_Svc` (median PBMS <x>, counted <y>).
- **Status codes:** <codes printed> -- mapped: `A` -> active; <any other code the lookup table names clearly>.
```

**Decision rule, stated here so no judgement is needed:**
- Choose the salary candidate with the most matches. If no candidate reaches 50% of the sample, choose `"gross"`, and add to client question 38: "our three candidate salaries matched PBMS's FAS for at most N of M retirees".
- Set `SERVICE_FROM_PAYROLL = True` if at least 70% are within half a year. Otherwise set it to `False`.
- If the detail-row status filter matches worse than "all rows", note it. Task 2 then drops `AND d.Status = 'A'`.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/pension_chat/config.py scripts/pbms_discover.py tests/test_pbms_config.py docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md
git commit -m "PBMS: settings, the ODBC driver, and the discovery that settles which salary PBMS counts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The guard and the fixed SQL

**Files:**
- Create: `src/pension_chat/pbms/__init__.py` (empty for now; Task 3 fills it)
- Create: `src/pension_chat/pbms/queries.py`
- Create: `src/pension_chat/pbms/connection.py`
- Modify: `src/pension_chat/records.py` (add `RecordUnavailable`)
- Test: `tests/test_pbms_queries.py`, `tests/test_pbms_connection.py`

**Interfaces:**
- Consumes: `SALARY_RULE` and `SERVICE_FROM_PAYROLL` from spec §6.2 (Task 1).
- Produces:
  - `records.RecordUnavailable(Exception)`;
  - `pbms.queries.Query(sql: str, params: tuple[str, ...])`;
  - `REGISTRY: dict[str, Query]`, with the keys `"resolve_meab_id"`, `"resolve_employee_id"`, `"record"` and `"salary_years"`;
  - `ALLOWED_COLUMNS: frozenset[str]`, `check_statement(sql: str) -> None` (raises `ValueError`), `SALARY_RULE: str` and `SERVICE_FROM_PAYROLL: bool`;
  - `pbms.connection.PbmsUnavailable(RecordUnavailable)` and `UnknownQuery(Exception)`;
  - `connection_string(*, server, database, user, password) -> str`;
  - `PbmsClient(conn_str, *, query_timeout_s=5.0, max_connections=4, connect=None)`, with `async fetch(name, **params) -> list[dict]` and `async close()`.
- `salary_years` rows have the columns `Year`, `Salary` and `Contribution_Months`.
- `record` and resolve rows have the columns `MEAB_Id`, `First_Name`, `Last_Name`, `Birth_Date` (record only), `Hire_Date`, `First_Payroll_Date`, `Seperation_Date`, `Employee_Status_Cd`, `Tier_Type_Cd`, `Reciprocal_Flg`, `ER_Type_Cd`, `Er_Type_Short_Desc` and `Department_Short_Desc`. The last three and `Employee_Status_Cd` also appear on resolve rows.

- [ ] **Step 1: Write the failing tests for the SQL**

```python
# tests/test_pbms_queries.py
"""The only SQL the chatbot sends PBMS: read-only, allow-listed, and indexed."""
from __future__ import annotations

import re

import pytest

from pension_chat.pbms.queries import ALLOWED_COLUMNS, REGISTRY, check_statement


def test_the_registry_holds_exactly_the_four_statements() -> None:
    assert set(REGISTRY) == {"resolve_meab_id", "resolve_employee_id", "record", "salary_years"}


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_statement_passes_the_guard(name: str) -> None:
    check_statement(REGISTRY[name].sql)          # raises on failure


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_placeholders_match_declared_parameters(name: str) -> None:
    q = REGISTRY[name]
    assert q.sql.count("?") == len(q.params)


@pytest.mark.parametrize("bad", [
    "UPDATE dbo.T_Employees SET Status = 'I'",
    "SELECT 1; DROP TABLE x",
    "SELECT p.Ssn FROM dbo.T_Person_Info p",
    "SELECT e.SSN FROM dbo.T_Employees e",
    "SELECT p.CCLast FROM dbo.T_Person_Info p",
    "SELECT p.PbsEmail FROM dbo.T_Person_Info p",
    "SELECT p.Maiden_Name FROM dbo.T_Person_Info p",
    "SELECT p.Middle_Name FROM dbo.T_Person_Info p",       # not on the allow-list
    "SELECT l.MEAB_Id INTO #t FROM dbo.T_Pbs_Linker l",
    "EXEC sp_who",
])
def test_the_guard_refuses(bad: str) -> None:
    with pytest.raises(ValueError):
        check_statement(bad)


def test_salary_is_read_through_the_master_table() -> None:
    sql = REGISTRY["salary_years"].sql
    assert "m.Person_Info_Seq_No" in sql
    assert not re.search(r"\bd\.Person_Info_Seq_No\b", sql)
    assert "d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No" in sql


def test_no_personal_identifier_is_on_the_allow_list() -> None:
    for col in ALLOWED_COLUMNS:
        assert not re.fullmatch(r"(?i)e?ssn|cc\w*|pbsaddress\w*|pbsemail|pbs\w*_phone|maiden_name|memberid", col)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_pbms_queries.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.pbms'`.

- [ ] **Step 3: Write `queries.py`**

`SALARY_RULE` and `SERVICE_FROM_PAYROLL` take the values recorded in spec §6.2. If §6.2 says the detail-status filter hurt matching, remove `AND d.Status = 'A'` from `_PAY_JOIN`.

```python
# src/pension_chat/pbms/queries.py
"""The only SQL the chatbot sends PBMS: named, parameterised, read-only.

Nothing here is assembled from user input. Each statement is checked at import
by `check_statement`, and a test runs the same check, so a statement that
writes, names a column outside `ALLOWED_COLUMNS`, or reads a personal
identifier cannot ship.

Salary is read through `T_Pen_Ded_Master`, never by filtering `T_Pen_Ded_Detail`
on the person: that column has no index, and one member's read scanned 46.7M
rows and timed out (spec §6.1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    sql: str
    params: tuple[str, ...]


# How a year's salary is built from pay periods, and whether service is counted
# from them. Settled by measurement against PBMS's own annuity calculations
# (spec §6.2); client question 38 asks the fund to confirm.
SALARY_EXPRESSIONS = {"gross": "SUM(d.Gross)", "rate": "MAX(d.Ann_Sal)", "capped": "SUM(d.Capped_Salary)"}
SALARY_RULE = "gross"
SERVICE_FROM_PAYROLL = True

ALLOWED_COLUMNS: frozenset[str] = frozenset({
    "MEAB_Id", "Person_Info_Seq_No", "Employee_Seq_No",
    "First_Name", "Last_Name", "Birth_Date",
    "Hire_Date", "First_Payroll_Date", "Seperation_Date", "Employee_Status_Cd",
    "Tier_Type_Cd", "Reciprocal_Flg", "ER_Type_Cd", "Er_Type_Short_Desc",
    "Department_Seq_No", "Department_Short_Desc", "Employee_Id", "Status",
    "Pen_Ded_Master_Seq_No", "PPE", "Gross", "Ann_Sal", "Capped_Salary", "Ded",
})

_WRITE = re.compile(
    r"\b(insert|update|delete|merge|exec|execute|drop|alter|create|truncate|grant|revoke|into|openrowset|openquery)\b",
    re.IGNORECASE,
)
_PERSONAL = re.compile(r"\b(e?ssn|cc\w*|pbsaddress\w*|pbsemail|pbs\w*_phone|maiden_name|memberid)\b", re.IGNORECASE)
# Every column is written alias.Column; the aliases are one or two letters.
_QUALIFIED = re.compile(r"\b[a-z]{1,2}\.([A-Za-z_]+)\b")


def check_statement(sql: str) -> None:
    """Raise ValueError unless `sql` is one read-only, allow-listed SELECT."""
    body = sql.strip()
    if not re.match(r"(?is)^(select|with)\b", body):
        raise ValueError("not a SELECT")
    if ";" in body:
        raise ValueError("more than one statement")
    if found := _WRITE.search(body):
        raise ValueError(f"write keyword {found.group(0)!r}")
    if found := _PERSONAL.search(body):
        raise ValueError(f"personal identifier {found.group(0)!r}")
    outside = set(_QUALIFIED.findall(body)) - ALLOWED_COLUMNS
    if outside:
        raise ValueError(f"columns outside the allow-list: {sorted(outside)}")


_WHO = """
       l.MEAB_Id, p.First_Name, p.Last_Name,
       e.Employee_Status_Cd, e.ER_Type_Cd, et.Er_Type_Short_Desc, dp.Department_Short_Desc"""

_WHO_JOINS = """
  JOIN dbo.T_Person_Info p ON p.Person_Info_Seq_No = l.Person_Info_Seq_No
  LEFT JOIN dbo.T_Employer_Types et ON et.ER_Type_Cd = e.ER_Type_Cd
  LEFT JOIN dbo.T_Departments dp ON dp.Department_Seq_No = e.Department_Seq_No"""

_PAY_JOIN = """
  FROM dbo.T_Pbs_Linker l
  JOIN dbo.T_Pen_Ded_Master m ON m.Person_Info_Seq_No = l.Person_Info_Seq_No
  JOIN dbo.T_Pen_Ded_Detail d ON d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No
 WHERE l.MEAB_Id = ? AND m.Status = 'A' AND d.Status = 'A'"""

REGISTRY: dict[str, Query] = {
    "resolve_meab_id": Query(
        f"""SELECT{_WHO}
  FROM dbo.T_Pbs_Linker l
  LEFT JOIN dbo.T_Employees e ON e.Employee_Seq_No = l.Employee_Seq_No{_WHO_JOINS}
 WHERE l.MEAB_Id = ?""",
        ("meab_id",),
    ),
    "resolve_employee_id": Query(
        f"""SELECT{_WHO}
  FROM dbo.T_Employees e
  JOIN dbo.T_Pbs_Linker l ON l.Employee_Seq_No = e.Employee_Seq_No{_WHO_JOINS}
 WHERE e.Employee_Id = ? AND e.Status = 'A' AND l.MEAB_Id LIKE 'M-%'
   AND (? IS NULL OR e.ER_Type_Cd = ?)""",
        ("employee_id", "employer_cd", "employer_cd"),
    ),
    "record": Query(
        f"""SELECT{_WHO}, p.Birth_Date,
       e.Hire_Date, e.First_Payroll_Date, e.Seperation_Date, e.Tier_Type_Cd, e.Reciprocal_Flg
  FROM dbo.T_Pbs_Linker l
  JOIN dbo.T_Employees e ON e.Employee_Seq_No = l.Employee_Seq_No{_WHO_JOINS}
 WHERE l.MEAB_Id = ?""",
        ("meab_id",),
    ),
    "salary_years": Query(
        f"""SELECT YEAR(d.PPE) AS Year, {SALARY_EXPRESSIONS[SALARY_RULE]} AS Salary,
       COUNT(DISTINCT CASE WHEN d.Ded > 0 THEN MONTH(d.PPE) END) AS Contribution_Months{_PAY_JOIN}
 GROUP BY YEAR(d.PPE)
 ORDER BY YEAR(d.PPE)""",
        ("meab_id",),
    ),
}

for _name, _query in REGISTRY.items():
    check_statement(_query.sql)
```

Create `src/pension_chat/pbms/__init__.py` containing only `"""The client's PBMS database, read-only (Phase 1)."""`.

- [ ] **Step 4: Run the query tests**

Run: `uv run pytest tests/test_pbms_queries.py -v`
Expected: all pass. If `test_the_guard_refuses` passes a statement it should refuse, fix `check_statement`, not the test.

- [ ] **Step 5: Write the failing tests for the connection**

```python
# tests/test_pbms_connection.py
"""PbmsClient runs registry statements only, safely, on a small pool."""
from __future__ import annotations

import pytest

from pension_chat.pbms.connection import PbmsClient, PbmsUnavailable, UnknownQuery, connection_string
from pension_chat.records import RecordUnavailable

SECRET = "pa}ss-w0rd-XYZ"


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description = conn, None

    def execute(self, sql, args):
        self.conn.log.append((sql, args))
        if self.conn.fail:
            raise self.conn.fail
        self.description = [("MEAB_Id",), ("First_Name",)]

    def fetchall(self):
        return [("M-000001", "Abcde")]

    def close(self):
        pass


class FakeConn:
    def __init__(self, fail=None):
        self.log, self.fail, self.timeout, self.closed, self.setup = [], fail, None, False, []

    def cursor(self):
        return FakeCursor(self)

    def execute(self, sql):
        self.setup.append(sql)

    def close(self):
        self.closed = True


def _client(*conns):
    made = list(conns)
    opened = []

    def connect(conn_str, timeout):
        conn = made.pop(0)
        opened.append(conn)
        return conn

    return PbmsClient(connection_string(server="s", database="d", user="u", password=SECRET),
                      connect=connect), opened


async def test_a_registry_statement_returns_rows_as_dicts() -> None:
    client, opened = _client(FakeConn())
    rows = await client.fetch("resolve_meab_id", meab_id="M-000001")
    assert rows == [{"MEAB_Id": "M-000001", "First_Name": "Abcde"}]
    assert opened[0].setup == ["SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"]
    assert opened[0].timeout == 5


async def test_an_unknown_name_never_reaches_the_driver() -> None:
    client, opened = _client(FakeConn())
    with pytest.raises(UnknownQuery):
        await client.fetch("SELECT * FROM dbo.T_Person_Info")
    assert opened == []


async def test_a_missing_parameter_is_refused() -> None:
    client, _ = _client(FakeConn())
    with pytest.raises(TypeError):
        await client.fetch("record")


async def test_a_driver_failure_is_unavailable_and_carries_no_secret() -> None:
    boom = Exception("08001", f"login failed for DRIVER=...;PWD={SECRET}")
    client, _ = _client(FakeConn(fail=boom), FakeConn(fail=boom))
    with pytest.raises(PbmsUnavailable) as caught:
        await client.fetch("record", meab_id="M-000001")
    assert isinstance(caught.value, RecordUnavailable)
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert SECRET not in repr(client)


async def test_a_dropped_pooled_connection_is_retried_once_on_a_fresh_one() -> None:
    client, opened = _client(FakeConn(), FakeConn())
    await client.fetch("record", meab_id="M-000001")          # opens #1 and returns it to the pool
    opened[0].fail = Exception("08S01", "communication link failure")
    rows = await client.fetch("record", meab_id="M-000001")   # #1 fails, #2 answers
    assert rows and opened[0].closed and len(opened) == 2


def test_the_connection_string_is_read_only_and_named() -> None:
    cs = connection_string(server="tcp:h,2022", database="PbsMeabDev", user="sa", password=SECRET)
    assert "ApplicationIntent=ReadOnly" in cs
    assert "APP=pension-chat-readonly" in cs
    assert "PWD={pa}}ss-w0rd-XYZ}" in cs                     # braces escaped
```

- [ ] **Step 6: Run them to see them fail**

Run: `uv run pytest tests/test_pbms_connection.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.pbms.connection'`.

- [ ] **Step 7: Add `RecordUnavailable` to `records.py`**

Add it after the `EmployeeRecord` class:

```python
class RecordUnavailable(Exception):
    """The record source could not answer (unreachable, timed out, refused).

    Distinct from "no such employee", which is `None`. The agent reports this
    one to the user rather than answering as if there were no record.
    """
```

- [ ] **Step 8: Write `connection.py`**

```python
# src/pension_chat/pbms/connection.py
"""The one door to PBMS: fixed, named, read-only statements, and nothing else.

There is no method that takes SQL text. `fetch` takes a statement *name* from
`queries.REGISTRY`; anything else raises before a connection is touched. Every
connection declares itself read-only (`ApplicationIntent=ReadOnly`, the driver's
read-only access mode), names itself to the DBA (`APP=pension-chat-readonly`),
reads uncommitted so it never holds locks PBMS's own work could wait on, and
gives up on any statement after the query timeout.

Errors leave as `PbmsUnavailable` with the statement name and SQLSTATE only --
`from None`, so no driver message (which can echo connection details) rides
along in the traceback.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from typing import Any

from pension_chat.pbms.queries import REGISTRY, Query
from pension_chat.records import RecordUnavailable

APPLICATION_NAME = "pension-chat-readonly"
_READ_UNCOMMITTED = "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"


class PbmsUnavailable(RecordUnavailable):
    """PBMS could not answer. The message never carries credentials."""


class UnknownQuery(Exception):
    """A statement name that is not in the registry."""


def connection_string(*, server: str, database: str, user: str, password: str) -> str:
    def braced(value: str) -> str:
        return "{" + value.replace("}", "}}") + "}"

    return (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};DATABASE={braced(database)};UID={braced(user)};PWD={braced(password)};"
        "Encrypt=yes;TrustServerCertificate=yes;ApplicationIntent=ReadOnly;"
        f"APP={APPLICATION_NAME};"
    )


def _odbc_connect(conn_str: str, login_timeout: int) -> Any:
    # Imported here: a Postgres-only deployment never loads the driver manager.
    import pyodbc

    return pyodbc.connect(conn_str, timeout=login_timeout, autocommit=True, readonly=True)


def _sqlstate(exc: BaseException) -> str:
    first = exc.args[0] if exc.args else None
    return first if isinstance(first, str) and len(first) <= 8 else type(exc).__name__


def _close_quietly(conn: Any) -> None:
    try:
        conn.close()
    except Exception:
        pass


class PbmsClient:
    def __init__(
        self,
        conn_str: str,
        *,
        query_timeout_s: float = 5.0,
        max_connections: int = 4,
        connect: Callable[[str, int], Any] | None = None,
    ) -> None:
        self._conn_str = conn_str
        self._timeout = max(1, int(query_timeout_s))
        self._connect = connect or _odbc_connect
        self._idle: queue.LifoQueue[Any] = queue.LifoQueue()
        self._slots = threading.BoundedSemaphore(max_connections)

    def __repr__(self) -> str:
        return f"PbmsClient(app={APPLICATION_NAME!r})"

    async def fetch(self, name: str, **params: Any) -> list[dict[str, Any]]:
        query = REGISTRY.get(name)
        if query is None:
            raise UnknownQuery(name)
        missing = [p for p in query.params if p not in params]
        if missing:
            raise TypeError(f"{name} needs {missing}")
        args = tuple(params[p] for p in query.params)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run, name, query, args), timeout=self._timeout * 2 + 1
            )
        except TimeoutError:
            raise PbmsUnavailable(f"PBMS query {name} timed out") from None

    def _run(self, name: str, query: Query, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        if not self._slots.acquire(timeout=self._timeout):
            raise PbmsUnavailable(f"PBMS query {name}: no connection free")
        try:
            # A pooled connection may have been dropped by the server while
            # idle; that costs one retry on a fresh one, not the user's turn.
            for attempt in (1, 2):
                conn, reused = self._checkout(fresh=attempt == 2)
                try:
                    rows = self._execute(conn, query, args)
                except Exception as exc:
                    _close_quietly(conn)
                    if attempt == 1 and reused:
                        continue
                    raise PbmsUnavailable(f"PBMS query {name} failed ({_sqlstate(exc)})") from None
                self._idle.put(conn)
                return rows
            raise PbmsUnavailable(f"PBMS query {name} failed")  # unreachable
        finally:
            self._slots.release()

    def _checkout(self, *, fresh: bool) -> tuple[Any, bool]:
        if not fresh:
            try:
                return self._idle.get_nowait(), True
            except queue.Empty:
                pass
        try:
            conn = self._connect(self._conn_str, self._timeout)
            conn.timeout = self._timeout
            conn.execute(_READ_UNCOMMITTED)
        except Exception as exc:
            raise PbmsUnavailable(f"PBMS connection failed ({_sqlstate(exc)})") from None
        return conn, False

    @staticmethod
    def _execute(conn: Any, query: Query, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        cursor = conn.cursor()
        try:
            cursor.execute(query.sql, args)
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _close_all(self) -> None:
        while True:
            try:
                _close_quietly(self._idle.get_nowait())
            except queue.Empty:
                return

    async def close(self) -> None:
        await asyncio.to_thread(self._close_all)
```

- [ ] **Step 9: Run both test files**

Run: `uv run pytest tests/test_pbms_queries.py tests/test_pbms_connection.py -v`
Expected: all pass. The failure test uses two failing connections: the first attempt is fresh, so it is not retried and raises immediately; the second connection goes unused. That is expected.

- [ ] **Step 10: Lint, type-check and commit**

Run: `uv run ruff check src/pension_chat/pbms tests/test_pbms_*.py && uv run mypy src/pension_chat/pbms src/pension_chat/records.py`
Expected: no errors.

```bash
git add src/pension_chat/pbms src/pension_chat/records.py tests/test_pbms_queries.py tests/test_pbms_connection.py
git commit -m "PBMS: the guard -- named, read-only, allow-listed statements on a small read-only pool

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `PbmsProvider`, the mapping, and wiring it into the app

**Files:**
- Create: `src/pension_chat/pbms/provider.py`
- Modify: `src/pension_chat/pbms/__init__.py` (add `build_record_provider`)
- Modify: `src/pension_chat/records.py`:
  - `EmployeeMatch.department`;
  - `EmployeeRecord.source`;
  - `record_source` in `to_agent_record`;
  - the MEAB ID helpers;
  - `set_default_provider`, with `current_provider` falling back to it.
- Modify: `src/pension_chat/main.py` (lifespan, around line 97)
- Modify: `src/pension_chat/api/health.py`
- Modify: `scripts/pbms_discover.py` (add the `timing` subcommand)
- Test: `tests/test_pbms_provider.py`

**Interfaces:**
- Consumes: `PbmsClient.fetch` and the row columns from Task 2; `RecordUnavailable`.
- Produces:
  - `records.normalise_meab_id(text: str | None) -> str | None`;
  - `records.meab_ids_in(text: str) -> list[str]`;
  - `records.MEAB_ID_PATTERN = r"^M-[0-9]{6}$"`;
  - `records.set_default_provider(p: RecordProvider | None) -> None`;
  - `EmployeeMatch(key, display_name, employer=None, status=None, department=None)`;
  - `EmployeeRecord(..., source: str | None = None)`, whose agent record then carries `"record_source"`;
  - `pbms.provider.PbmsProvider(client, *, today=date.today)`, with `find_employees(query, scope=None)`, `get_record(key)`, `get_salary_history(key)` and `close()`;
  - the pure helpers `record_from_rows(row, salary_rows)`, `salary_from_rows(rows, *, as_of)`, `match_from_row(row)`, `reconcile_tier(code, start) -> tuple[int | None, str | None]` and `tier_from_participation(start) -> int | None`;
  - `pbms.build_record_provider(settings) -> PbmsProvider | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pbms_provider.py
"""PBMS rows become the EmployeeRecord the agent reads; gaps stay gaps. Synthetic rows only."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from pension_chat.pbms.provider import (
    PbmsProvider, match_from_row, reconcile_tier, record_from_rows, salary_from_rows,
)
from pension_chat.records import (
    RecordUnavailable, current_provider, meab_ids_in, normalise_meab_id, set_default_provider, use_provider,
)

ROW = {
    "MEAB_Id": "M-000123", "First_Name": "Qwxzt", "Last_Name": "Plmrb",
    "Birth_Date": datetime(1964, 9, 30), "Hire_Date": datetime(2012, 3, 1), "First_Payroll_Date": None,
    "Seperation_Date": None, "Employee_Status_Cd": "A", "Tier_Type_Cd": "2", "Reciprocal_Flg": "N",
    "ER_Type_Cd": "CITY", "Er_Type_Short_Desc": "City of Chicago", "Department_Short_Desc": "Finance",
}
PAY = [
    {"Year": 2024, "Salary": Decimal("88000.00"), "Contribution_Months": 12},
    {"Year": 2025, "Salary": Decimal("90000.00"), "Contribution_Months": 12},
    {"Year": 2026, "Salary": Decimal("67500.00"), "Contribution_Months": 9},
]


@pytest.mark.parametrize("raw, want", [
    ("M-012345", "M-012345"), (" m-012345 ", "M-012345"), ("M-12345", None), ("E-012345", None), (None, None),
])
def test_meab_ids_are_normalised(raw, want) -> None:
    assert normalise_meab_id(raw) == want


def test_ids_are_found_in_text_once_each() -> None:
    assert meab_ids_in("compare m-000001 with M-000002 and M-000001") == ["M-000001", "M-000002"]
    assert meab_ids_in("XM-0000012") == []


def test_a_full_row_maps_to_the_record() -> None:
    rec = record_from_rows(ROW, PAY)
    assert (rec.key, rec.display_name, rec.employer, rec.department) == (
        "M-000123", "Qwxzt Plmrb", "City of Chicago", "Finance")
    assert rec.birth_date == date(1964, 9, 30) and rec.hire_date == date(2012, 3, 1)
    assert (rec.tier, rec.status, rec.source) == (2, "active", "PBMS")
    assert rec.service_years == Decimal("2.75")                  # 33 months
    assert rec.service_source == "counted from payroll (months with a pension deduction)"
    assert rec.missing == {}


def test_no_hire_date_uses_the_first_payroll_date_and_says_so() -> None:
    rec = record_from_rows({**ROW, "Hire_Date": None, "First_Payroll_Date": datetime(2012, 4, 15)}, PAY)
    assert rec.hire_date == date(2012, 4, 15)
    assert "first payroll date (2012-04-15)" in rec.missing["hire_date"]


def test_no_dates_at_all_leaves_them_absent() -> None:
    rec = record_from_rows({**ROW, "Hire_Date": None, "First_Payroll_Date": None, "Birth_Date": None}, [])
    assert rec.hire_date is None and rec.birth_date is None
    assert set(rec.missing) >= {"hire_date", "birth_date", "service_years"}


@pytest.mark.parametrize("code, start, want_tier, note_has", [
    ("2", date(2012, 3, 1), 2, None),
    ("2", date(2019, 1, 1), 2, "indicates Tier 3"),          # disagreement: PBMS's value, reported
    ("0", date(2005, 1, 1), 1, "tier code '0'"),              # unmapped: from the dates, reported
    ("", date(2017, 8, 1), None, "does not settle it"),       # 2017 with no code: unknown
    ("3", date(2017, 8, 1), 3, None),                         # 2017, PBMS decides
    ("T", None, None, "not on file"),
])
def test_tier_is_reconciled_with_the_dates(code, start, want_tier, note_has) -> None:
    tier, note = reconcile_tier(code, start)
    assert tier == want_tier
    assert (note is None) if note_has is None else (note_has in note)


def test_an_unmapped_status_is_reported_not_guessed() -> None:
    rec = record_from_rows({**ROW, "Employee_Status_Cd": "DB4"}, PAY)
    assert rec.status is None and "DB4" in rec.missing["employment_status"]


def test_reciprocal_flag_is_reported_as_a_gap() -> None:
    assert "reciprocal" in record_from_rows({**ROW, "Reciprocal_Flg": "Y"}, PAY).missing["reciprocal_service_years"]


def test_the_current_year_is_left_out_of_salary_history() -> None:
    history = salary_from_rows(PAY, as_of=date(2026, 9, 30))
    assert [h.year for h in history] == [2024, 2025]


def test_a_match_carries_what_the_confirm_row_shows() -> None:
    m = match_from_row(ROW)
    assert (m.key, m.display_name, m.employer, m.department, m.status) == (
        "M-000123", "Qwxzt Plmrb", "City of Chicago", "Finance", "active")


class FakeClient:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def fetch(self, name, **params):
        self.calls.append((name, params))
        if self.fail:
            raise RecordUnavailable("down")
        if name == "salary_years":
            return PAY
        return [ROW] if params.get("meab_id") == "M-000123" or params.get("employee_id") == 77 else []

    async def close(self):
        pass


async def test_the_provider_reads_record_and_salary_and_caches_salary() -> None:
    client = FakeClient()
    provider = PbmsProvider(client, today=lambda: date(2026, 9, 30))
    record = await provider.get_record(" m-000123 ")
    history = await provider.get_salary_history("M-000123")
    assert record is not None and record.key == "M-000123"
    assert [h.year for h in history] == [2024, 2025]
    assert [c[0] for c in client.calls].count("salary_years") == 1


async def test_a_key_that_is_not_a_meab_id_is_no_record_and_no_query() -> None:
    client = FakeClient()
    provider = PbmsProvider(client)
    assert await provider.get_record("3f1c9e2a-uuid-of-a-profile") is None
    assert client.calls == []


async def test_find_by_employee_id_passes_the_employer_scope() -> None:
    client = FakeClient()
    matches = await PbmsProvider(client).find_employees("77", "CITY")
    assert matches[0].key == "M-000123"
    assert client.calls == [("resolve_employee_id", {"employee_id": 77, "employer_cd": "CITY"})]


async def test_unavailable_propagates() -> None:
    with pytest.raises(RecordUnavailable):
        await PbmsProvider(FakeClient(fail=True)).get_record("M-000123")


def test_a_context_provider_outranks_the_default() -> None:
    default, scoped = object(), object()
    set_default_provider(default)  # type: ignore[arg-type]
    try:
        assert current_provider() is default
        with use_provider(scoped):  # type: ignore[arg-type]
            assert current_provider() is scoped
    finally:
        set_default_provider(None)
    assert current_provider() is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_pbms_provider.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.pbms.provider'`.

- [ ] **Step 3: Extend `records.py`**

Add these changes:
- a `department: str | None = None` field, as the last field of `EmployeeMatch`;
- a `source: str | None = None` field, as the last field of `EmployeeRecord`;
- in `to_agent_record`, the line `if self.source: record["record_source"] = self.source` before `return record`.

At module level, add:

```python
import re

MEAB_ID_PATTERN = r"^M-[0-9]{6}$"
_MEAB_ID = re.compile(MEAB_ID_PATTERN)
_MEAB_ID_IN_TEXT = re.compile(r"(?<![\w-])M-\d{6}(?![\w-])", re.IGNORECASE)


def normalise_meab_id(text: str | None) -> str | None:
    """`M-######` in canonical form, or None if `text` is not one."""
    candidate = (text or "").strip().upper()
    return candidate if _MEAB_ID.fullmatch(candidate) else None


def meab_ids_in(text: str) -> list[str]:
    """Every MEAB ID written in `text`, canonical, first occurrence first."""
    found: list[str] = []
    for match in _MEAB_ID_IN_TEXT.finditer(text or ""):
        value = match.group(0).upper()
        if value not in found:
            found.append(value)
    return found
```

Replace `current_provider` and add the default:

```python
# The process-wide provider the app installs at startup (RECORD_SOURCE=pbms).
# A provider set with `use_provider` -- the eval suite's fixtures -- outranks it.
_default_provider: RecordProvider | None = None


def set_default_provider(provider: RecordProvider | None) -> None:
    global _default_provider
    _default_provider = provider


def current_provider() -> RecordProvider | None:
    return _provider.get() or _default_provider
```

- [ ] **Step 4: Write `provider.py`**

```python
# src/pension_chat/pbms/provider.py
"""PBMS as a RecordProvider: the fund's own records, read live.

The mapping is pure (`record_from_rows`, `salary_from_rows`) so it is tested on
synthetic rows. A field PBMS leaves empty is absent, with the reason in
`missing`: MEABF leaves the hire date empty on 80,763 live rows (spec §6.1), and
a plausible default there would be a wrong figure with a real citation on it.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from pension_chat.pbms.queries import SERVICE_FROM_PAYROLL
from pension_chat.records import EmployeeMatch, EmployeeRecord, SalaryYear, normalise_meab_id

_TIER_CODES = {"1": 1, "2": 2, "3": 3}
# Employee_Status_Cd -> the agent's employment_status. Extended only from the
# codes Task 1 found described (spec §6.2); anything else is reported, not guessed.
_STATUS = {"A": "active"}
SERVICE_FROM_PAYROLL_SOURCE = "counted from payroll (months with a pension deduction)"


class _Client(Protocol):
    async def fetch(self, name: str, **params: Any) -> list[dict[str, Any]]: ...

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
    return EmployeeMatch(
        key=key,
        display_name=_display_name(row) or key,
        employer=_employer(row),
        status=status or (f"PBMS status {code}" if code else None),
        department=_text(row.get("Department_Short_Desc")),
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

    async def find_employees(self, query: str, scope: object | None = None) -> list[EmployeeMatch]:
        meab_id = normalise_meab_id(query)
        if meab_id:
            rows = await self._client.fetch("resolve_meab_id", meab_id=meab_id)
        elif query.strip().isdigit() and len(query.strip()) <= 9:
            rows = await self._client.fetch(
                "resolve_employee_id", employee_id=int(query.strip()),
                employer_cd=scope if isinstance(scope, str) else None,
            )
        else:
            return []
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

    async def close(self) -> None:
        await self._client.close()
```

- [ ] **Step 5: Run the provider tests**

Run: `uv run pytest tests/test_pbms_provider.py tests/test_records_provider.py -v`
Expected: all pass. The existing `test_records_provider.py` still passes, because `current_provider()` returns `None` when neither provider is set.

- [ ] **Step 6: Add the factory, then wire it into startup and health**

`src/pension_chat/pbms/__init__.py`:

```python
"""The client's PBMS database, read-only (Phase 1)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pension_chat.config import Settings
    from pension_chat.pbms.provider import PbmsProvider


def build_record_provider(settings: Settings) -> PbmsProvider | None:
    """The PBMS provider when RECORD_SOURCE=pbms, else None. Refuses to start half-configured."""
    if settings.record_source != "pbms":
        return None
    from pension_chat.pbms.connection import PbmsClient, connection_string
    from pension_chat.pbms.provider import PbmsProvider

    names = ("pbms_sql_server", "pbms_sql_database", "pbms_sql_user", "pbms_sql_password")
    unset = [n.upper() for n in names if not getattr(settings, n)]
    if unset:
        raise RuntimeError(f"RECORD_SOURCE=pbms needs {', '.join(unset)} in .env")
    assert settings.pbms_sql_password is not None
    client = PbmsClient(
        connection_string(
            server=str(settings.pbms_sql_server), database=str(settings.pbms_sql_database),
            user=str(settings.pbms_sql_user), password=settings.pbms_sql_password.get_secret_value(),
        ),
        query_timeout_s=settings.pbms_query_timeout_s,
    )
    return PbmsProvider(client)
```

Add these tests to `tests/test_pbms_provider.py`:

```python
def test_no_provider_unless_pbms(monkeypatch) -> None:
    from pension_chat.pbms import build_record_provider
    from tests.test_pbms_config import _settings

    assert build_record_provider(_settings()) is None
    with pytest.raises(RuntimeError, match="PBMS_SQL_SERVER"):
        build_record_provider(_settings(record_source="pbms"))
    provider = build_record_provider(_settings(
        record_source="pbms", pbms_sql_server="s", pbms_sql_database="d",
        pbms_sql_user="u", pbms_sql_password="p"))
    assert provider is not None
```

If `tests` is not importable as a package (check whether `tests/__init__.py` exists), copy the `_settings` helper into this file instead of importing it.

In `src/pension_chat/main.py`, change `lifespan` to:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    from pension_chat.pbms import build_record_provider
    from pension_chat.records import set_default_provider

    logger.info("Starting Pension Chat API (env=%s)", settings.app_env)
    # RECORD_SOURCE=pbms: every turn reads employee records from PBMS, live and
    # read-only. Installed before warming so a misconfiguration fails at start.
    record_provider = build_record_provider(settings)
    set_default_provider(record_provider)
    logger.info("Employee records from %s", settings.record_source)
    await _warm_connections()
    yield
    from pension_chat.agent.llm import close_clients
    from pension_chat.agent.registry import drain_background_writes

    # Bookkeeping writes were moved off the member's critical path, which means
    # some may still be in flight when the process is asked to stop. Closing the
    # connection pool under them would lose the count they were carrying.
    await drain_background_writes()
    await close_clients()
    set_default_provider(None)
    if record_provider is not None:
        await record_provider.close()
    logger.info("Shutting down Pension Chat API")
```

In `src/pension_chat/api/health.py`, add `from pension_chat.config import get_settings`, and add `"record_source": get_settings().record_source,` to the returned dict. The UI shows the Employee bar only when this value is `"pbms"`.

- [ ] **Step 7: Add the `timing` subcommand to discovery, and run it**

Add this to `scripts/pbms_discover.py`: a `"timing"` choice, and a `timing()` function that times the real registry statements through `PbmsClient`, three runs each, for three MEAB IDs sampled by count. It prints timings only.

```python
def timing(conn) -> None:
    import asyncio
    import time as _t

    from pension_chat.config import get_settings
    from pension_chat.pbms import build_record_provider

    ids = [r["id"] for r in _rows(conn, "SELECT TOP 3 l.MEAB_Id AS id FROM dbo.T_Pbs_Linker l "
                                        "JOIN dbo.T_Employees e ON e.Employee_Seq_No = l.Employee_Seq_No "
                                        "WHERE e.Status = 'A' AND e.Employee_Status_Cd = 'A' "
                                        "AND l.MEAB_Id LIKE 'M-%' ORDER BY l.Person_Info_Seq_No DESC")]
    settings = get_settings().model_copy(update={"record_source": "pbms"})
    provider = build_record_provider(settings)
    assert provider is not None

    async def run() -> None:
        base = _t.perf_counter()
        await provider._client.fetch("resolve_meab_id", meab_id=ids[0])  # warm the pool
        print(f"  first call (connect + query): {(_t.perf_counter() - base) * 1000:.0f} ms")
        for name in ("resolve_meab_id", "record", "salary_years"):
            spent = []
            for meab in ids:
                t = _t.perf_counter()
                await provider._client.fetch(name, meab_id=meab)
                spent.append((_t.perf_counter() - t) * 1000)
            print(f"  {name}: " + ", ".join(f"{s:.0f}" for s in spent) + " ms")
        t = _t.perf_counter()
        await provider.get_record(ids[1])
        print(f"  get_record (record + salary in parallel): {(_t.perf_counter() - t) * 1000:.0f} ms")
        await provider.close()

    print("== timings from this machine (a bare round trip here is ~240 ms)")
    asyncio.run(run())
```

Run: `uv run python scripts/pbms_discover.py timing`
Expected: each statement takes roughly one round trip (about 240–300 ms from this machine). If `salary_years` is more than 1 s above the round trip, stop: the statement is not using the master index. Check `_PAY_JOIN` against spec §6.1.

Append the four timing lines to spec §6.2 as "Timings (dev machine, 240 ms round trip)".

- [ ] **Step 8: Run the suite and commit**

Run: `uv run pytest -q` (the full suite) and `uv run ruff check . && uv run mypy .`
Expected: all pass, with no new lint or type errors.

```bash
git add src/pension_chat/pbms src/pension_chat/records.py src/pension_chat/main.py src/pension_chat/api/health.py scripts/pbms_discover.py tests/test_pbms_provider.py docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md
git commit -m "PBMS: PbmsProvider -- the fund's records mapped to the agent's, gaps kept as gaps; installed when RECORD_SOURCE=pbms

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The agent reads the conversation's subject

**Files:**
- Modify: `src/pension_chat/agent/state.py`:
  - add `subject_meab_id`, `subject_suggestion` and `record_unavailable` to the `PensionAgentState` TypedDict and to `initial_state()`;
  - add a `subject_meab_id` parameter to `initial_state` and `turn_input`.
- Modify: `src/pension_chat/agent/graph.py:208-248`, the `subject_meab_id` parameter of `run_agent_stream`.
- Modify: `src/pension_chat/agent/nodes.py:290-337` (`fetch_employee_record`).
- Modify: `src/pension_chat/agent/loop.py:141-183` (`_context`).
- Modify: `src/pension_chat/agent/card.py:45-100`, which adds `record_label`.
- Test: `tests/test_subject_record.py`.

**Interfaces:**
- Consumes: `records.current_provider` and `RecordUnavailable` (Task 3), and the agent record's `record_source`, `display_name`, `employer`, `department`, `service_source` and `missing`.
- Produces:
  - `initial_state(question, session_id, employee_id=None, history=None, *, subject_meab_id=None)`;
  - `turn_input(question, session_id, employee_id=None, *, subject_meab_id=None)`;
  - `run_agent_stream(..., subject_meab_id: str | None = None)`;
  - the state keys `subject_meab_id: str | None`, `subject_suggestion: dict | None` and `record_unavailable: bool`, all per-turn;
  - `loop._record_notes(record: dict) -> str`;
  - the calculation card's `"record_label": str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_subject_record.py
"""A conversation's subject is the record the agent reads; the principal stays the principal."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pension_chat.agent import nodes
from pension_chat.agent.card import calculation_card
from pension_chat.agent.loop import _context, _record_notes
from pension_chat.agent.state import initial_state, turn_input
from pension_chat.agent.worksheet import WorkSheet
from pension_chat.records import EmployeeRecord, RecordUnavailable, SalaryYear, use_provider


class _Stub:
    def __init__(self, fail=False):
        self.asked, self.fail = [], fail

    async def find_employees(self, query, scope=None):
        return []

    async def get_record(self, key):
        self.asked.append(key)
        if self.fail:
            raise RecordUnavailable("down")
        if key != "M-000123":
            return None
        return EmployeeRecord(key="M-000123", tier=2, birth_date=date(1964, 9, 30), hire_date=date(2012, 3, 1),
                              display_name="Qwxzt Plmrb", department="Finance", status="active",
                              service_years=Decimal("14"), service_source="counted from payroll",
                              missing={"tier": "PBMS records Tier 2, but ..."}, source="PBMS")

    async def get_salary_history(self, key):
        return [SalaryYear(2025, Decimal("90000"))]


def test_the_subject_is_carried_on_every_turn() -> None:
    assert initial_state("q", "s", "principal", subject_meab_id="M-000123")["subject_meab_id"] == "M-000123"
    assert turn_input("q", "s", "principal", subject_meab_id="M-000123")["subject_meab_id"] == "M-000123"
    assert turn_input("q", "s", "principal")["subject_meab_id"] is None


async def test_the_subject_outranks_the_principal_when_a_provider_is_in_force() -> None:
    stub = _Stub()
    with use_provider(stub):
        out = await nodes.fetch_employee_record({"employee_id": "principal-uuid", "subject_meab_id": "M-000123"})
    assert stub.asked == ["M-000123"]
    assert out["employee_record"]["record_source"] == "PBMS"


async def test_switching_subject_reloads_instead_of_reusing_the_pin() -> None:
    stub = _Stub()
    pinned = {"record": {"id": "M-000999", "article_number": 8, "tier": 1}, "salary_history": []}
    with use_provider(stub):
        await nodes.fetch_employee_record({"subject_meab_id": "M-000123", "pinned_record": pinned})
    assert stub.asked == ["M-000123"]


async def test_an_unreachable_source_is_reported_not_treated_as_no_record() -> None:
    with use_provider(_Stub(fail=True)):
        out = await nodes.fetch_employee_record({"subject_meab_id": "M-000123"})
    assert out["employee_record"] is None and out["record_unavailable"] is True


def test_the_context_says_the_record_could_not_be_read() -> None:
    text = _context({**initial_state("q", "s"), "record_unavailable": True})
    assert "could not be read" in text


def test_the_record_notes_name_the_employee_and_every_gap() -> None:
    notes = _record_notes({"id": "M-000123", "display_name": "Qwxzt Plmrb", "department": "Finance",
                           "record_source": "PBMS", "service_source": "counted from payroll",
                           "missing": {"tier": "PBMS records Tier 2, but ..."}})
    assert "Qwxzt Plmrb" in notes and "M-000123" in notes and "counted from payroll" in notes
    assert "tier: PBMS records Tier 2" in notes


def test_the_card_names_the_pbms_record() -> None:
    state = {**initial_state("q", "s"),
             "employee_record": {"id": "M-000123", "article_number": 8, "record_source": "PBMS"},
             "calculation_result": {"r": {"base": {"ok": True, "inputs_used": {}, "annual_amount": "1"}}}}
    card = calculation_card(state, WorkSheet.from_state(state))
    assert card is not None and card["record_label"] == "PBMS record (M-000123)"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_subject_record.py -v`
Expected: FAIL, with `TypeError: initial_state() got an unexpected keyword argument 'subject_meab_id'`.

- [ ] **Step 3: Update the state**

In `state.py`, add to `PensionAgentState` (next to `employee_id`):

```python
    # The employee this conversation is about (a MEAB ID), set by the employer
    # and read from `sessions.subject_meab_id` on every turn. Not the principal:
    # `employee_id` still scopes row-level security and the checkpointer.
    subject_meab_id: str | None
    # A MEAB ID the user typed that is not the subject: resolved, not loaded,
    # and put to the user to confirm (`triage`).
    subject_suggestion: dict[str, Any] | None
    # The record source could not be read this turn -- distinct from "no record".
    record_unavailable: bool
```

`initial_state` gains a keyword-only parameter `subject_meab_id: str | None = None`, and its returned dict gains `"subject_meab_id": subject_meab_id, "subject_suggestion": None, "record_unavailable": False,`. `turn_input` gains the same parameter and adds `subject_meab_id=subject_meab_id,` to its `fresh.update(...)`. The keys stay out of `DURABLE_KEYS`: the session row is the durable home of the subject.

In `graph.py`, `run_agent_stream` gains `subject_meab_id: str | None = None` after `thread_owner`, and passes it through:

```python
        turn: dict[str, Any] = initial_state(question, session_id, employee_id, history,
                                             subject_meab_id=subject_meab_id)
    else:
        turn = turn_input(question, session_id, employee_id, subject_meab_id=subject_meab_id)
```

- [ ] **Step 4: Update `fetch_employee_record`**

Replace the lines from `employee_id = state.get("employee_id")` through the `record, history = loaded` assignment with:

```python
    employee_id = state.get("employee_id")
    provider = current_provider()
    # With a record provider in force (PBMS, or the eval fixtures) the record is
    # the conversation's subject when one is set; the principal otherwise, which
    # is how the evals name their employee. Without one, the Postgres profile.
    key = (state.get("subject_meab_id") or employee_id) if provider else employee_id
    if not key:
        return {"employee_record": None, "salary_history": []}

    pinned = state.get("pinned_record") or {}
    if (pinned.get("record") or {}).get("id") == str(key):
        record, history = pinned["record"], pinned["salary_history"]
    else:
        try:
            loaded = await _from_provider(str(key)) if provider else await _load_record(key)
        except RecordUnavailable:
            return {"employee_record": None, "salary_history": [], "record_unavailable": True}
        if loaded is None:
            return {"employee_record": None, "salary_history": []}
        record, history = loaded
```

Add `RecordUnavailable` to the existing `from pension_chat.records import …` line in `nodes.py`.

- [ ] **Step 5: Update the context and the card**

In `loop.py`, change the final `else:` branch of the facts block (line 182–183) to:

```python
    elif state.get("record_unavailable"):
        lines.append(
            "The fund's records could not be read for this employee just now. "
            "Say so plainly, and ask for any value you need rather than assuming it."
        )
    else:
        lines.append("No member record is available; answer from the Code alone.")
    record = state.get("employee_record") or {}
    if record.get("record_source"):
        lines.append(_record_notes(record))
```

Add this function directly above `_context`:

```python
def _record_notes(record: dict[str, Any]) -> str:
    """Who this is and what their record could not say -- only for a fund record (PBMS).

    An employer is asking about someone else, so the answer names them; and a
    gap the record reports (no hire date, a tier PBMS and the dates disagree on)
    is something to tell the employer, not something to fill.
    """
    who = ", ".join(
        str(v) for v in (record.get("display_name"), record.get("id"),
                         record.get("department") or record.get("employer")) if v
    )
    parts = [f"This conversation is about {who}, from the fund's own records ({record['record_source']})."]
    if record.get("service_source"):
        parts.append(f"Their service is {record['service_source']}.")
    gaps = record.get("missing") or {}
    if gaps:
        parts.append(
            "What the record could not say, and why -- report these rather than filling them: "
            + "; ".join(f"{name}: {why}" for name, why in sorted(gaps.items())) + "."
        )
    return " ".join(parts)
```

In `card.py`, add this entry to the returned dict, after `"support_level"`:

```python
            # Whose record the "Record" inputs came from, when it was the fund's.
            "record_label": _record_label(state),
```

Add the function below `calculation_card`:

```python
def _record_label(state: Mapping[str, Any]) -> str | None:
    record = state.get("employee_record") or {}
    return f"{record['record_source']} record ({record['id']})" if record.get("record_source") else None
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_subject_record.py tests/test_records_provider.py -v`, then `uv run pytest -q`
Expected: all pass. Tests that snapshot `_context` are unaffected, because fixture and Postgres records carry no `record_source`.

- [ ] **Step 7: Commit**

```bash
git add src/pension_chat/agent/state.py src/pension_chat/agent/graph.py src/pension_chat/agent/nodes.py src/pension_chat/agent/loop.py src/pension_chat/agent/card.py tests/test_subject_record.py
git commit -m "Agent: the conversation's subject is the record it reads; an unreadable record is said, not hidden

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: A MEAB ID in a message becomes a suggestion, never a load

**Files:**
- Modify: `src/pension_chat/agent/nodes.py:116-144` (`triage`)
- Modify: `src/pension_chat/agent/graph.py:101-115` (`decline`)
- Test: `tests/test_subject_suggestion.py`

**Interfaces:**
- Consumes: `records.meab_ids_in`, `current_provider`, `RecordUnavailable` and `EmployeeMatch` (Task 3), and the state keys `subject_meab_id` and `subject_suggestion` (Task 4).
- Produces:
  - `nodes._subject_suggestion(state, question) -> dict | None`, whose dict has the keys `meab_id`, `found`, `unavailable`, and when found `display_name`, `employer`, `department` and `status`;
  - `graph.subject_suggestion_message(s: dict) -> str`;
  - triage's `out_of_scope_reason == "subject_suggestion"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_subject_suggestion.py
"""An ID typed in a message is put to the user to confirm; nobody is loaded on a mention."""
from __future__ import annotations

from pension_chat.agent import nodes
from pension_chat.agent.graph import decline, subject_suggestion_message
from pension_chat.records import EmployeeMatch, RecordUnavailable, use_provider


class _Stub:
    def __init__(self, fail=False):
        self.found, self.fail = [], fail

    async def find_employees(self, query, scope=None):
        self.found.append(query)
        if self.fail:
            raise RecordUnavailable("down")
        if query == "M-000123":
            return [EmployeeMatch("M-000123", "Qwxzt Plmrb", "City of Chicago", "active", "Finance")]
        return []

    async def get_record(self, key):
        raise AssertionError("a mention must not load a record")

    async def get_salary_history(self, key):
        raise AssertionError("a mention must not load a record")


def _state(q, subject=None):
    return {"question": q, "messages": [q], "subject_meab_id": subject}


async def test_a_new_id_becomes_a_suggestion() -> None:
    with use_provider(_Stub()):
        out = await nodes.triage(_state("what about m-000123's pension?"))
    assert out["in_scope"] is False and out["out_of_scope_reason"] == "subject_suggestion"
    assert out["subject_suggestion"]["found"] is True
    assert out["subject_suggestion"]["meab_id"] == "M-000123"


async def test_the_current_subject_passes_straight_through() -> None:
    stub = _Stub()
    with use_provider(stub):
        out = await nodes.triage(_state("and M-000123 at 62?", subject="M-000123"))
    assert out.get("subject_suggestion") is None and stub.found == []


async def test_without_a_provider_ids_mean_nothing() -> None:
    out = await nodes.triage(_state("M-000123"))
    assert out.get("subject_suggestion") is None


async def test_unknown_and_unavailable_are_told_apart() -> None:
    with use_provider(_Stub()):
        unknown = (await nodes.triage(_state("M-000999")))["subject_suggestion"]
    with use_provider(_Stub(fail=True)):
        down = (await nodes.triage(_state("M-000999")))["subject_suggestion"]
    assert unknown == {"meab_id": "M-000999", "found": False, "unavailable": False}
    assert down == {"meab_id": "M-000999", "found": False, "unavailable": True}


def test_the_messages() -> None:
    found = {"meab_id": "M-000123", "found": True, "unavailable": False, "display_name": "Qwxzt Plmrb",
             "employer": "City of Chicago", "department": "Finance", "status": "active"}
    assert subject_suggestion_message(found).startswith("Did you mean Qwxzt Plmrb (M-000123, Finance, active)?")
    assert "couldn't find" in subject_suggestion_message({"meab_id": "M-000999", "found": False, "unavailable": False})
    assert "not answering" in subject_suggestion_message({"meab_id": "M-000999", "found": False, "unavailable": True})


async def test_decline_answers_the_suggestion_without_a_model() -> None:
    s = {"meab_id": "M-000999", "found": False, "unavailable": False}
    out = await decline({"out_of_scope_reason": "subject_suggestion", "subject_suggestion": s})
    assert "M-000999" in out["final_answer"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_subject_suggestion.py -v`
Expected: FAIL, with `ImportError: cannot import name 'subject_suggestion_message'`.

- [ ] **Step 3: Implement**

In `nodes.py`, extend the records import to include `current_provider`, `meab_ids_in` and `RecordUnavailable`. Add this above `triage`:

```python
async def _subject_suggestion(state: PensionAgentState, question: str) -> dict[str, Any] | None:
    """A MEAB ID the user typed that is not this conversation's subject.

    Resolved and put to them -- never loaded on a mention: a mistyped ID in
    passing would otherwise silently answer about the wrong person. Only when a
    record provider is in force; in the Postgres path an M- number means nothing.
    """
    provider = current_provider()
    if provider is None:
        return None
    current = state.get("subject_meab_id")
    mentioned = [m for m in meab_ids_in(question) if m != current]
    if not mentioned:
        return None
    meab_id = mentioned[0]
    try:
        matches = await provider.find_employees(meab_id)
    except RecordUnavailable:
        return {"meab_id": meab_id, "found": False, "unavailable": True}
    if not matches:
        return {"meab_id": meab_id, "found": False, "unavailable": False}
    match = matches[0]
    return {"meab_id": match.key, "found": True, "unavailable": False,
            "display_name": match.display_name, "employer": match.employer,
            "department": match.department, "status": match.status}
```

In `triage`, just before the final `return`, add:

```python
    suggestion = await _subject_suggestion(state, question)
    if suggestion is not None:
        return {
            "in_scope": False,
            "out_of_scope_reason": "subject_suggestion",
            "subject_suggestion": suggestion,
            "timings": {"triage": time.perf_counter() - started},
        }
```

In `graph.py`, add this above `decline`:

```python
def subject_suggestion_message(suggestion: dict[str, Any]) -> str:
    """The reply to a MEAB ID typed in a message: confirm, not found, or not checkable."""
    meab_id = suggestion.get("meab_id", "that ID")
    if suggestion.get("found"):
        about = ", ".join(
            str(v) for v in (meab_id, suggestion.get("department") or suggestion.get("employer"),
                             suggestion.get("status")) if v
        )
        return (f"Did you mean {suggestion.get('display_name') or meab_id} ({about})? "
                "Confirm below to make them this conversation's employee, then ask your question.")
    if suggestion.get("unavailable"):
        return (f"I couldn't check {meab_id} just now: the fund's records are not answering. "
                "Please try again in a moment.")
    return f"I couldn't find an employee with MEAB ID {meab_id}. Please check the number."
```

In `decline`, replace the `answer = …` line with:

```python
    if reason == "subject_suggestion":
        answer = subject_suggestion_message(state.get("subject_suggestion") or {})
    else:
        answer = GREETING_MESSAGE if reason == "greeting" else OUT_OF_SCOPE_MESSAGE
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_subject_suggestion.py -v`, then `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/nodes.py src/pension_chat/agent/graph.py tests/test_subject_suggestion.py
git commit -m "Agent: a MEAB ID in a message is put to the user to confirm, never loaded on a mention

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The session's subject — migration, API and audit

**Files:**
- Create: `alembic/versions/009_session_subject.py`
- Modify: `src/pension_chat/db/models.py:228-270` (the `Session` columns and check constraint)
- Create: `src/pension_chat/api/employees.py` (the lookup helper and route)
- Modify: `src/pension_chat/api/sessions.py`:
  - the subject routes;
  - `send_message`, which passes the subject;
  - the audit trail.
- Modify: `src/pension_chat/main.py` (register the employees router)
- Test: `tests/test_subject_api.py`

**Interfaces:**
- Consumes: `records.normalise_meab_id`, `MEAB_ID_PATTERN`, `current_provider`, `RecordUnavailable` and `EmployeeMatch.department` (Task 3); `run_agent_stream(subject_meab_id=…)` (Task 4); the state keys `subject_suggestion` and `employee_record.record_source` (Tasks 4 and 5).
- Produces:
  - `api.employees.lookup(provider, *, meab_id, employee_id, employer) -> list[dict]`, which raises `HTTPException` 400, 404 or 503;
  - `api.employees.match_payload(m) -> dict`, with the keys `meab_id`, `display_name`, `employer`, `department` and `status`;
  - the routes `GET /api/v1/sessions/{id}/employees/lookup`, and `GET`, `PUT` (body `{"meab_id": str}`) and `DELETE` on `/api/v1/sessions/{id}/subject`;
  - `sessions._subject_trail(state) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_subject_api.py
"""Lookup and subject rules, tested at the helpers the routes are thin wrappers over."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from pension_chat.api.employees import lookup
from pension_chat.api.sessions import _subject_trail
from pension_chat.records import MEAB_ID_PATTERN, EmployeeMatch, RecordUnavailable


class _Stub:
    def __init__(self, matches=(), fail=False):
        self.matches, self.fail, self.asked = list(matches), fail, []

    async def find_employees(self, query, scope=None):
        self.asked.append((query, scope))
        if self.fail:
            raise RecordUnavailable("down")
        return self.matches


A = EmployeeMatch("M-000123", "Qwxzt Plmrb", "City of Chicago", "active", "Finance")
B = EmployeeMatch("M-000456", "Zrtyq Lomnb", "Board of Education", "active", "Schools")


async def test_a_meab_id_is_normalised_and_returned() -> None:
    stub = _Stub([A])
    out = await lookup(stub, meab_id=" m-000123 ", employee_id=None, employer=None)
    assert stub.asked == [("M-000123", None)]
    assert out == [{"meab_id": "M-000123", "display_name": "Qwxzt Plmrb", "employer": "City of Chicago",
                    "department": "Finance", "status": "active"}]


async def test_an_employee_id_shared_across_employers_returns_every_match() -> None:
    out = await lookup(_Stub([A, B]), meab_id=None, employee_id="77", employer=None)
    assert [m["meab_id"] for m in out] == ["M-000123", "M-000456"]


@pytest.mark.parametrize("kwargs, status", [
    ({"meab_id": "M-12", "employee_id": None, "employer": None}, 400),
    ({"meab_id": None, "employee_id": "abc", "employer": None}, 400),
    ({"meab_id": None, "employee_id": None, "employer": None}, 400),
    ({"meab_id": "M-000999", "employee_id": None, "employer": None}, 404),
])
async def test_bad_or_unknown_input(kwargs, status) -> None:
    with pytest.raises(HTTPException) as caught:
        await lookup(_Stub([]), **kwargs)
    assert caught.value.status_code == status


async def test_unavailable_is_503_and_no_provider_is_404() -> None:
    with pytest.raises(HTTPException) as down:
        await lookup(_Stub(fail=True), meab_id="M-000123", employee_id=None, employer=None)
    with pytest.raises(HTTPException) as off:
        await lookup(None, meab_id="M-000123", employee_id=None, employer=None)
    assert (down.value.status_code, off.value.status_code) == (503, 404)


def test_the_audit_trail_names_the_subject_and_any_suggestion() -> None:
    state = {"employee_record": {"id": "M-000123", "record_source": "PBMS"},
             "subject_suggestion": {"meab_id": "M-000456", "found": True}}
    assert _subject_trail(state) == [
        {"type": "subject", "meab_id": "M-000123"},
        {"type": "subject_suggestion", "meab_id": "M-000456", "found": True},
    ]
    assert _subject_trail({"employee_record": {"id": "x"}}) == []


def test_the_migration_checks_the_same_pattern_the_code_uses() -> None:
    text = Path("alembic/versions/009_session_subject.py").read_text(encoding="utf-8")
    assert "subject_meab_id" in text and "down_revision = \"008_answer_evidence\"" in text
    assert MEAB_ID_PATTERN in text
    assert re.fullmatch(MEAB_ID_PATTERN, "M-000123") and not re.fullmatch(MEAB_ID_PATTERN, "m-000123")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_subject_api.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.api.employees'`.

- [ ] **Step 3: Write the migration and update the model**

```python
# alembic/versions/009_session_subject.py
"""A conversation's subject: the employee (MEAB ID) an employer is asking about.

Not the principal. `sessions.employee_id` is who is chatting and scopes
row-level security; `subject_meab_id` is who they are asking about, read from
PBMS. The existing `sessions` policies cover the new columns, so there is no
new policy. The check is the same pattern as `records.MEAB_ID_PATTERN`.

Revision ID: 009_session_subject
Revises: 008_answer_evidence
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "009_session_subject"
down_revision = "008_answer_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("subject_meab_id", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("subject_set_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_sessions_subject_meab_id", "sessions", "subject_meab_id ~ '^M-[0-9]{6}$'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_sessions_subject_meab_id", "sessions", type_="check")
    op.drop_column("sessions", "subject_set_at")
    op.drop_column("sessions", "subject_meab_id")
```

In `models.py`, add these to `Session` after `status`:

```python
    # The employee this conversation is about (migration 009). Not the
    # principal: `employee_id` is who is chatting and scopes RLS.
    subject_meab_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Add `CheckConstraint("subject_meab_id ~ '^M-[0-9]{6}$'", name="ck_sessions_subject_meab_id"),` to its `__table_args__`.

- [ ] **Step 4: Write `api/employees.py`**

```python
# src/pension_chat/api/employees.py
"""Finding an employee by MEAB ID or Employee ID, for the Employee bar.

Reads only, through the record provider in force. Scoped to a session the
caller owns (plan ruling R3): no names are readable without one. Several
matches -- an Employee ID repeated across employers -- are all returned and the
user picks; nothing is selected on their behalf.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from pension_chat.api.auth import TokenData, get_current_user
from pension_chat.records import EmployeeMatch, RecordProvider, RecordUnavailable, current_provider, normalise_meab_id

router = APIRouter(prefix="/api/v1", tags=["employees"])


def match_payload(match: EmployeeMatch) -> dict[str, Any]:
    return {"meab_id": match.key, "display_name": match.display_name, "employer": match.employer,
            "department": match.department, "status": match.status}


async def lookup(provider: RecordProvider | None, *, meab_id: str | None, employee_id: str | None,
                 employer: str | None) -> list[dict[str, Any]]:
    if provider is None:
        raise HTTPException(status_code=404, detail="Employee lookup is not enabled")
    scope: str | None = None
    if meab_id is not None:
        query = normalise_meab_id(meab_id)
        if query is None:
            raise HTTPException(status_code=400, detail="A MEAB ID is M- followed by six digits")
    elif employee_id is not None and employee_id.strip().isdigit() and len(employee_id.strip()) <= 9:
        query, scope = employee_id.strip(), employer
    else:
        raise HTTPException(status_code=400, detail="Give a MEAB ID or an Employee ID")
    try:
        matches = await provider.find_employees(query, scope)
    except RecordUnavailable:
        raise HTTPException(status_code=503, detail="The fund's records are not answering just now") from None
    if not matches:
        raise HTTPException(status_code=404, detail="No employee with that ID")
    return [match_payload(m) for m in matches]


@router.get("/sessions/{session_id}/employees/lookup")
async def lookup_employees(
    session_id: str,
    request: Request,
    meab_id: str | None = None,
    employee_id: str | None = None,
    employer: str | None = None,
    user: TokenData | None = Depends(get_current_user),
) -> list[dict[str, Any]]:
    from pension_chat.api.sessions import _authorised_session

    await _authorised_session(uuid.UUID(session_id), request, user)
    return await lookup(current_provider(), meab_id=meab_id, employee_id=employee_id, employer=employer)
```

In `main.py`, add `from pension_chat.api.employees import router as employees_router  # noqa: E402` beside the other router imports, and `app.include_router(employees_router)` after `sessions_router`.

- [ ] **Step 5: Add the subject routes and pass the subject to each turn**

In `sessions.py`:

```python
class SubjectRequest(BaseModel):
    meab_id: str


def _subject_trail(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Who this turn was about, and any ID it put to the user -- for the audit and the UI."""
    trail: list[dict[str, Any]] = []
    record = state.get("employee_record") or {}
    if record.get("record_source"):
        trail.append({"type": "subject", "meab_id": record.get("id")})
    if suggestion := state.get("subject_suggestion"):
        trail.append({"type": "subject_suggestion", **suggestion})
    return trail


async def _store_subject(session: Session, meab_id: str | None) -> None:
    async with get_db_session(**_thread_owner(session)) as db:
        await db.execute(
            update(Session).where(Session.id == session.id).values(
                subject_meab_id=meab_id, subject_set_at=datetime.now(UTC) if meab_id else None,
            )
        )
        await db.commit()


@router.get("/sessions/{session_id}/subject")
async def get_subject(session_id: str, request: Request,
                      user: TokenData | None = Depends(get_current_user)) -> dict[str, Any] | None:
    """The conversation's employee, re-read from the record source so the chip is current."""
    from pension_chat.api.employees import lookup

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    if not session.subject_meab_id:
        return None
    try:
        return (await lookup(current_provider(), meab_id=session.subject_meab_id,
                             employee_id=None, employer=None))[0]
    except HTTPException:
        # The key is still the subject even when the name cannot be read now.
        return {"meab_id": session.subject_meab_id, "display_name": None, "employer": None,
                "department": None, "status": None}


@router.put("/sessions/{session_id}/subject")
async def put_subject(session_id: str, body: SubjectRequest, request: Request,
                      user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    """Set the conversation's employee: re-resolved in PBMS first, MEAB ID only."""
    from pension_chat.api.employees import lookup

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    match = (await lookup(current_provider(), meab_id=body.meab_id, employee_id=None, employer=None))[0]
    await _store_subject(session, match["meab_id"])
    return match


@router.delete("/sessions/{session_id}/subject")
async def delete_subject(session_id: str, request: Request,
                         user: TokenData | None = Depends(get_current_user)) -> dict[str, bool]:
    session = await _authorised_session(uuid.UUID(session_id), request, user)
    await _store_subject(session, None)
    return {"cleared": True}
```

Add `from pension_chat.records import current_provider` to the imports.

In `send_message`, add `subject_meab_id=session.subject_meab_id,` to the `run_agent_stream(...)` call. Then change the audit line to:

```python
        tool_calls = _audit_trail(final_state) + ([card] if card else []) + _subject_trail(final_state)
```

`session` here is the ORM object read at the top of `send_message`, whose attributes were loaded before its database session closed. `subject_meab_id` is a plain column, so it is available. `get_db_session(**_thread_owner(session))` passes `employee_id` and `session_token` as strings; `get_db_session` already takes both keyword arguments (see `_scoped` in `send_message`).

- [ ] **Step 6: Run the tests and apply the migration**

Run: `uv run pytest tests/test_subject_api.py -v`, then `uv run pytest -q`
Expected: all pass.

Run: `uv run alembic upgrade head`
Expected: `Running upgrade 008_answer_evidence -> 009_session_subject`. This touches the project's own Postgres, never PBMS.

- [ ] **Step 7: Lint, type-check and commit**

Run: `uv run ruff check . && uv run mypy .`

```bash
git add alembic/versions/009_session_subject.py src/pension_chat/db/models.py src/pension_chat/api/employees.py src/pension_chat/api/sessions.py src/pension_chat/main.py tests/test_subject_api.py
git commit -m "API: a conversation's subject by MEAB ID -- lookup, set, clear -- passed to every turn and kept in the audit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The Employee bar, the suggestion card, and the card label

**Files:**
- Create: `frontend/src/lib/subject.ts`, `frontend/src/lib/subject.test.ts`
- Modify: `frontend/src/lib/api.ts` (after `forgetScenario`)
- Modify: `frontend/src/lib/card.ts`:
  - add `record_label` to `CalculationCardData`;
  - add a `recordLabel` parameter to `sourceLabel`.
- Modify: `frontend/src/components/chat/CalculationCard.tsx:63`, and any other `sourceLabel(` caller (`grep -rn "sourceLabel(" frontend/src`).
- Create: `frontend/src/components/chat/SubjectBar.tsx`, `frontend/src/components/chat/SubjectSuggestion.tsx`
- Modify: `frontend/src/components/chat/ChatHeader.tsx`, `ChatMessage.tsx` and `ChatInterface.tsx`

**Interfaces:**
- Consumes: the Task 6 routes and payload `{meab_id, display_name, employer, department, status}`; `tool_calls` entries `{"type": "subject_suggestion", meab_id, found, unavailable, …}`; the card's `record_label`; `/health`'s `record_source`.
- Produces:
  - `EmployeeMatch` (TypeScript);
  - `parseLookup(raw: string): { meab_id: string } | { employee_id: string } | null`;
  - `chipLabel(m: EmployeeMatch): string`;
  - `suggestionFrom(toolCalls: unknown): Suggestion | null`;
  - `lookupEmployee`, `setSubject`, `clearSubject`, `fetchSubject` and `fetchRecordSource`.

Before writing components, read `frontend/AGENTS.md` and the relevant `node_modules/next/dist/docs/` page on client components. These components are `"use client"` leaf components in the same style as `ScenarioChips.tsx`. Use only semantic token utilities (no raw palette classes, gradients or blur), per CLAUDE.md's design-system rule.

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/lib/subject.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";

import { chipLabel, parseLookup, suggestionFrom } from "./subject.ts";

test("a MEAB ID is normalised, an Employee ID is digits, anything else is refused", () => {
  assert.deepEqual(parseLookup(" m-012345 "), { meab_id: "M-012345" });
  assert.deepEqual(parseLookup("4471"), { employee_id: "4471" });
  assert.equal(parseLookup("M-1234"), null);
  assert.equal(parseLookup("Smith"), null);
  assert.equal(parseLookup("1234567890"), null);
});

test("the chip reads name, ID, then department or employer, without repeats", () => {
  const m = { meab_id: "M-000123", display_name: "Qwxzt Plmrb", employer: "City", department: "Finance", status: "active" };
  assert.equal(chipLabel(m), "Qwxzt Plmrb · M-000123 · Finance");
  assert.equal(chipLabel({ ...m, display_name: null, department: null }), "M-000123 · City");
});

test("a suggestion is read from tool_calls", () => {
  const calls = [{ type: "card" }, { type: "subject_suggestion", meab_id: "M-000123", found: true, unavailable: false, display_name: "Q" }];
  assert.equal(suggestionFrom(calls)?.meab_id, "M-000123");
  assert.equal(suggestionFrom([{ type: "card" }]), null);
  assert.equal(suggestionFrom(undefined), null);
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npm test`
Expected: FAIL, with `Cannot find module './subject.ts'`.

- [ ] **Step 3: Write `subject.ts`**

```ts
// frontend/src/lib/subject.ts
/** The conversation's employee: who an employer is asking about (Phase 1). */

export interface EmployeeMatch {
  meab_id: string;
  display_name: string | null;
  employer: string | null;
  department: string | null;
  status: string | null;
}

export interface Suggestion extends Partial<EmployeeMatch> {
  meab_id: string;
  found: boolean;
  unavailable: boolean;
}

const MEAB_ID = /^M-\d{6}$/;

/** What the Employee field holds: a MEAB ID (canonical), an Employee ID, or neither. */
export function parseLookup(raw: string): { meab_id: string } | { employee_id: string } | null {
  const text = raw.trim().toUpperCase();
  if (MEAB_ID.test(text)) return { meab_id: text };
  if (/^\d{1,9}$/.test(text)) return { employee_id: text };
  return null;
}

export function chipLabel(m: EmployeeMatch): string {
  const parts = [m.display_name, m.meab_id, m.department ?? m.employer].filter(
    (part): part is string => Boolean(part),
  );
  return [...new Set(parts)].join(" · ");
}

export function suggestionFrom(toolCalls: unknown): Suggestion | null {
  if (!Array.isArray(toolCalls)) return null;
  const hit = toolCalls.find(
    (t) => t && typeof t === "object" && (t as { type?: unknown }).type === "subject_suggestion",
  ) as (Suggestion & { type: string }) | undefined;
  if (!hit || typeof hit.meab_id !== "string") return null;
  const { type: _type, ...rest } = hit;
  return rest;
}
```

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npm test`
Expected: all pass, including the existing suites.

- [ ] **Step 5: Add the API functions and the card label**

Append to `api.ts`, after `forgetScenario`, and add `import type { EmployeeMatch } from "./subject";` at the top:

```ts
export type LookupResult = { ok: true; matches: EmployeeMatch[] } | { ok: false; message: string };

async function detail(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : fallback;
}

/** Find an employee by MEAB ID or Employee ID, within a conversation the caller owns. */
export async function lookupEmployee(
  sessionId: string,
  query: { meab_id: string } | { employee_id: string },
): Promise<LookupResult> {
  const params = new URLSearchParams(query as Record<string, string>);
  const response = await fetchWithAuth(`/sessions/${sessionId}/employees/lookup?${params}`);
  if (response.ok) return { ok: true, matches: await response.json() };
  return { ok: false, message: await detail(response, "The lookup failed.") };
}

export async function setSubject(sessionId: string, meabId: string): Promise<EmployeeMatch | null> {
  const response = await fetchWithAuth(`/sessions/${sessionId}/subject`, {
    method: "PUT",
    body: JSON.stringify({ meab_id: meabId }),
  });
  return response.ok ? response.json() : null;
}

export async function clearSubject(sessionId: string): Promise<boolean> {
  return (await fetchWithAuth(`/sessions/${sessionId}/subject`, { method: "DELETE" })).ok;
}

export async function fetchSubject(sessionId: string): Promise<EmployeeMatch | null> {
  const response = await fetchWithAuth(`/sessions/${sessionId}/subject`);
  return response.ok ? response.json() : null;
}

/** "pbms" when employee records come from the fund; the Employee bar shows only then. */
export async function fetchRecordSource(): Promise<string> {
  try {
    const response = await fetch(`${API_BASE_URL}/health`);
    return response.ok ? ((await response.json()).record_source ?? "postgres") : "postgres";
  } catch {
    return "postgres";
  }
}
```

In `card.ts`:
- add `record_label?: string | null;` to `CalculationCardData`;
- change `sourceLabel` to take `(source: string | null | undefined, recordLabel?: string | null)`, returning `recordLabel` when `source === "record" && recordLabel`.

Add this test to `card.test.ts`:

```ts
test("a record input names the fund's record when the card carries one", () => {
  assert.equal(sourceLabel("record", "PBMS record (M-000123)"), "PBMS record (M-000123)");
  assert.equal(sourceLabel("record"), "Record");
});
```

Update every `sourceLabel(input.source)` call to `sourceLabel(input.source, card.record_label)`.

- [ ] **Step 6: Write the components and wire them in**

`SubjectBar.tsx`:
- Props: `{ sessionId: string | null; subject: EmployeeMatch | null; onChange: (s: EmployeeMatch | null) => void; disabled?: boolean }`.
- Local state: `text`, `matches: EmployeeMatch[]`, `message: string | null` and `busy`.
- **Set:** renders the chip `chipLabel(subject)` with **Change** (clears the local chip view and shows the input; the stored subject stays until a new one is used) and **Clear** (`clearSubject`, then `onChange(null)`).
- **Empty:** renders an `Input` (placeholder `M-012345 or Employee ID`, `aria-label="Employee"`) and a **Look up** `Button`.
  - On submit, `parseLookup(text)`. `null` sets `message = "Enter a MEAB ID (M-012345) or an Employee ID"`. Otherwise it calls `lookupEmployee`: on failure it shows `message`; on success it sets `matches`.
  - For each match it renders one row showing `chipLabel(m)` and `m.status`, with **Use** (`setSubject(sessionId, m.meab_id)`, then `onChange(result)` and clear the matches) and one **Cancel** for the list.
  - One match still shows the row and requires **Use**: nothing is auto-selected.

`SubjectSuggestion.tsx`:
- Props: `{ suggestion: Suggestion; onConfirm: (meabId: string) => void; confirmed: boolean }`.
- It renders nothing when `!suggestion.found`, because the message text already says why.
- Otherwise it shows a bordered row with `chipLabel` and a **Use this employee** button, disabled once `confirmed`.

`ChatHeader.tsx` gains an optional `subjectBar?: React.ReactNode` prop, rendered at the right end of the header (`ml-auto`). On narrow screens it wraps below the header row, which avoids horizontal scroll at 360 px.

`ChatMessage.tsx`:
- reads `const suggestion = suggestionFrom(message.tool_calls);`;
- renders `<SubjectSuggestion>` under the message body when it is set;
- takes a new prop, `onConfirmSubject?: (meabId: string) => void`, plus `subjectMeabId?: string | null`, which drives `confirmed`.

`ChatInterface.tsx`:
- `const [recordSource, setRecordSource] = useState("postgres")`, loaded once with `fetchRecordSource()` in a mount effect;
- `const [subject, setSubjectState] = useState<EmployeeMatch | null>(null)`;
- whenever `sessionId` changes, call `setSubjectState(null)`. When `recordSource === "pbms"` and `sessionId` is set, call `fetchSubject(sessionId).then(setSubjectState)`;
- pass `subjectBar={recordSource === "pbms" ? <SubjectBar sessionId={sessionId} subject={subject} onChange={setSubjectState} disabled={isStreaming} /> : undefined}` to `ChatHeader`;
- pass `onConfirmSubject={async (id) => { if (sessionId) { const s = await setSubject(sessionId, id); if (s) setSubjectState(s); } }}` and `subjectMeabId={subject?.meab_id ?? null}` to each `ChatMessage`.

- [ ] **Step 7: Build, lint and test**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: tests pass, lint shows 0 errors (the existing 2 warnings are allowed), and the build succeeds. Grep the new components for raw palette classes (`bg-(red|blue|gray|slate|zinc)-`), `gradient` or `blur`. Expected: no hits.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/subject.ts frontend/src/lib/subject.test.ts frontend/src/lib/api.ts frontend/src/lib/card.ts frontend/src/lib/card.test.ts frontend/src/components/chat
git commit -m "UI: the Employee bar and the confirm-first suggestion; record inputs name the PBMS record

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Eval fixtures, the live smoke test, the demo check, and docs

**Files:**
- Modify: `src/pension_chat/evals/fixtures.py` (read an optional `source`)
- Create: `evals/fixtures/employees/pbms_tier_disagreement.yaml`, `evals/fixtures/employees/pbms_payroll_service.yaml`
- Create: `evals/cases/S4/S4-010.yaml`
- Modify: `pyproject.toml` (register the `pbms` pytest marker, excluded by default)
- Create: `tests/test_pbms_live.py`
- Modify: `CLAUDE.md` (a short "PBMS read layer" subsection under Architecture)
- Modify: `.env.example` (document `RECORD_SOURCE` and the four `PBMS_SQL_*` keys, with empty values)

**Interfaces:**
- Consumes: everything above.
- Produces: fixtures with `source: PBMS`, which exercise `_record_notes` in evals; a manual demo checklist in the spec.

- [ ] **Step 1: Write the failing test for fixture `source`**

Add this to `tests/test_evals_fixtures.py`, or create it if it does not exist:

```python
from datetime import date
from pathlib import Path

from pension_chat.evals.fixtures import load_fixtures


def test_a_pbms_shaped_fixture_carries_its_source_and_gaps() -> None:
    fixtures = load_fixtures(Path("evals/fixtures/employees"), date(2026, 9, 30))
    record, _ = fixtures["pbms_tier_disagreement"]
    agent = record.to_agent_record([])
    assert agent["record_source"] == "PBMS"
    assert "indicates Tier 3" in agent["missing"]["tier"]
```

Run: `uv run pytest tests/test_evals_fixtures.py -v`
Expected: FAIL, with `KeyError: 'pbms_tier_disagreement'`.

- [ ] **Step 2: Add the fixtures and `source` support**

In `load_fixtures`, pass `source=raw.get("source"),` to `EmployeeRecord(...)`.

`evals/fixtures/employees/pbms_tier_disagreement.yaml`:

```yaml
key: pbms_tier_disagreement
source: PBMS
display_name: "Qwxzt Plmrb"
employer: City of Chicago
department: Streets and Sanitation
age: 62
hire_date: 2019-02-01
tier: 2
tier_basis: "PBMS's tier code disagrees with the hire date; the record keeps PBMS's value and reports it"
status: active
service_years: 7.5
service_source: counted from payroll (months with a pension deduction)
missing:
  tier: "PBMS records Tier 2, but participation beginning 2019-02-01 indicates Tier 3"
salary: {current: 72000, growth: 0.02, years: 8}
```

`evals/fixtures/employees/pbms_payroll_service.yaml`:

```yaml
key: pbms_payroll_service
source: PBMS
display_name: "Lkjhg Mnbvc"
employer: City of Chicago
department: Finance
age: 63
hire_date: 2012-05-01
tier: 2
status: active
service_years: 14.25
service_source: counted from payroll (months with a pension deduction)
missing:
  hire_date: "PBMS holds no hire date; the first payroll date (2012-05-01) is used in its place"
salary: {current: 88000, growth: 0.02, years: 10}
```

`evals/cases/S4/S4-010.yaml`:

```yaml
id: S4-010
family: S4
areas:
- calculation
- records
tiers:
- full
employee: pbms_tier_disagreement
turns:
- q: What would this employee's annuity be if they retired at the end of this year?
  goal: An answer about this PBMS employee that reports the tier discrepancy instead of choosing silently.
  reference: 'The record names the employee. PBMS records Tier 2, but participation from 2019 indicates Tier 3; the
    answer says so and states which tier any figure uses. Under 10 years of service (7.5), no retirement annuity is
    payable under §1-160 / §8-138(g-1); the answer says so rather than giving an annuity figure.'
  checks:
    ttft_class: heavy
    cites_any:
    - 1-160
    - 8-138
```

Run: `uv run pytest tests/test_evals_fixtures.py -v` and `uv run pytest tests -q -k evals`
Expected: pass. `_check_tier` accepts the disagreement fixture because it states `tier_basis`.

- [ ] **Step 3: Add the live smoke test (on demand, never in CI)**

In `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = ["pbms: reads the client's PBMS live; run on demand with -m pbms"]
addopts = "-m 'not pbms'"
```

If `addopts` already exists, append ` -m 'not pbms'` to it.

```python
# tests/test_pbms_live.py
"""On demand only: `uv run pytest -m pbms`. Reads one real record; asserts shapes; prints nothing."""
from __future__ import annotations

import os
from datetime import date

import pytest

pytestmark = pytest.mark.pbms


async def test_one_real_record_has_the_shape_the_agent_reads() -> None:
    from pension_chat.config import get_settings
    from pension_chat.pbms import build_record_provider

    meab_id = os.environ.get("PBMS_SMOKE_MEAB_ID")
    if not meab_id:
        pytest.skip("set PBMS_SMOKE_MEAB_ID")
    provider = build_record_provider(get_settings().model_copy(update={"record_source": "pbms"}))
    assert provider is not None
    try:
        record = await provider.get_record(meab_id)
        history = await provider.get_salary_history(meab_id)
    finally:
        await provider.close()
    assert record is not None and record.key == meab_id.strip().upper()
    assert record.source == "PBMS" and record.article_number == 8
    assert record.birth_date is None or isinstance(record.birth_date, date)
    assert record.tier in (None, 1, 2, 3)
    assert all(h.year < date.today().year and h.annual_salary > 0 for h in history)
```

Run: `uv run pytest -q`
Expected: the live test is deselected.

Then run it once by hand, with an active MEAB ID picked by count (none is printed): `PBMS_SMOKE_MEAB_ID=<an M- id> uv run pytest -m pbms -v`
Expected: 1 passed.

- [ ] **Step 4: Docs**

`.env.example`: add `RECORD_SOURCE=postgres`, `PBMS_SQL_SERVER=`, `PBMS_SQL_DATABASE=`, `PBMS_SQL_USER=` and `PBMS_SQL_PASSWORD=`, with a comment that the read-only login replaces the `sa` credentials with no code change.

`CLAUDE.md`: add a subsection **"PBMS read layer (`pbms/`)"** under Architecture, of at most 12 lines. It states:
- `RECORD_SOURCE=pbms` installs `PbmsProvider` process-wide, and evals still use `FixtureProvider`;
- the guard (named statements only, the allow-list, never SSN or `CC*`);
- salary is read through `T_Pen_Ded_Master`;
- the subject (`sessions.subject_meab_id`) is not the principal;
- a mention is a suggestion, never a load;
- gaps go in `missing` and reach the answer through `_record_notes`.

Also add `uv run python scripts/pbms_discover.py timing` to the Commands block.

- [ ] **Step 5: The demo check (manual, recorded in the spec)**

With `RECORD_SOURCE=pbms` in `.env`, run `uv run uvicorn pension_chat.main:app --reload` and `cd frontend && npm run dev`, then in the browser:
1. The Employee bar is visible. Type a MEAB ID and confirm that **Look up** shows one confirm row, then **Use** shows the chip.
2. Ask "What would their pension be if they retired at 62?". The answer names the employee (masked name), and the calculation card's record inputs read "PBMS record (M-…)".
3. In a new chat, type "what about M-<another id>?". The reply is the confirm question with a **Use this employee** card, and no record is loaded.
4. Stop the network (or set a wrong `PBMS_SQL_SERVER` and restart). A question says the fund's records could not be read, and the lookup shows "The fund's records are not answering just now".

Append "§9 Demo check (date): 1 ✓ 2 ✓ 3 ✓ 4 ✓" to the spec, with a note on anything that failed.

- [ ] **Step 6: Run everything and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy . && (cd frontend && npm test && npm run lint)`
Expected: all green.

```bash
git add src/pension_chat/evals/fixtures.py evals/fixtures/employees/pbms_*.yaml evals/cases/S4/S4-010.yaml pyproject.toml tests/test_pbms_live.py tests/test_evals_fixtures.py CLAUDE.md .env.example docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md
git commit -m "Phase 1: PBMS-shaped eval fixtures, an on-demand live smoke test, docs, and the demo check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Then run the calculation evals that touch records: `uv run python scripts/run_evals.py --changed`. Record the report's pass counts and heavy-TTFT median in the ledger. No threshold is gated here; the numbers are for comparison with the last run.
