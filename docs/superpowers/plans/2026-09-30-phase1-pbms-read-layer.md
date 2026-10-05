# Phase 1: MEABF Read Layer, People in Context, and Comparisons — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An employer names employees by first and last name in the chat and chooses who is meant when a name is shared. A conversation holds up to 4 people, shown as live chips, and the agent answers about one of them or compares several in one turn. Every record is read live and read-only from PBMS.

**Architecture:**
- A new package, `src/pension_chat/pbms/`, holds four parts:
  - a guard: `PbmsClient`, which runs only named, read-only, allow-listed statements;
  - the SQL: `queries.py`;
  - the in-memory name directory: `names.py`;
  - the provider: `PbmsProvider`, which implements `RecordProvider` and adds `names_in` and `find_by_name`.
- A new pure module, `agent/people.py`, decides deterministically who each turn is about (spec §4.2):
  - familiar names: no query;
  - an unfamiliar name: a choice turn;
  - no name: the previous turn's people;
  - a singular pronoun after a comparison: a which-one turn.
- `fetch_employee_record` loads every person in the turn in parallel. The first person also fills today's single-member keys, so one-person turns are unchanged.
- `compute_benefit` takes a `person` when a turn has several, and runs the existing engine chain on that person's record only.
- A code-rendered `PEOPLE` table carries the comparison.
- The session stores the people (`sessions.subjects`, at most 4, most recent first). A `people` SSE event updates the header chips before any answer text arrives.

**Tech Stack:**
- Python 3.12, FastAPI, LangGraph, SQLAlchemy/Alembic (Postgres);
- `pyodbc` with ODBC Driver 18 for SQL Server;
- Next.js 16 / React 19 with `node --test`;
- pytest (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md`, revised 2026-10-01. Read all of it, especially §3.4, §4 and §5.

## Global Constraints

- **Credentials.** The `sa` login is used **for reads only**. Code enforces this, not convention. Credentials come from `PBMS_SQL_SERVER`, `PBMS_SQL_DATABASE`, `PBMS_SQL_USER` and `PBMS_SQL_PASSWORD`. They are never logged, printed, or placed in an exception message or `repr`.
- **No real data in the repo.** No real PBMS data may appear in any fixture, test, log or commit, and that includes the masked names. Scripts that touch PBMS print only counts, rates, codes, shapes and timings.
- **Never write to PBMS.** Nothing is ever written to it.
- **Never read personal identifiers.** No statement ever names `Ssn`, `SSN`, `eSSN`, any `CC*` column, `PbsAddress_*`, `Pbs*_Phone`, `PbsEmail`, `Maiden_Name`, `Middle_Name` or `MemberID`.
- **Session settings.** Every PBMS connection uses `ApplicationIntent=ReadOnly` and `APP=pension-chat-readonly`, and runs at `READ UNCOMMITTED`. Queries time out after 5 s by default; the name directory load is allowed 30 s.
- **Employees are found by first and last name only.** The MEAB ID (`^M-[0-9]{6}$`) is internal. It is never shown in the UI or written into an answer, and it travels in API payloads only as the opaque `ref`.
- **Active members only.** An active member is a linker row with `MEAB_Id LIKE 'M-%'`, a live employee row (`e.Status = 'A'`) and `e.Employee_Status_Cd = 'A'`.
- **Salary is read through `T_Pen_Ded_Master`.** No statement filters `T_Pen_Ded_Detail` by `Person_Info_Seq_No`.
- **Gaps stay gaps.** A field PBMS leaves empty is absent, with a reason in `missing`. It is never defaulted, and nothing falls back to Postgres.
- **Nobody is chosen for the user.** A shared name is always offered as a choice, department first above `MAX_LISTED = 12`. A singular pronoun after a turn about several people is asked about, never guessed.
- **At most `MAX_PEOPLE = 4` people** in a conversation and in a turn.
- **One person's record never feeds another's figure.** Each `compute_benefit` call in a multi-person turn sees only its own person's record. Comparison numbers are code-rendered (the `PEOPLE` view), never written by the model.
- **Default behaviour is unchanged.** With `RECORD_SOURCE` unset (`postgres`), and in eval cases without `people:`, everything behaves exactly as today. A one-person turn fills exactly today's state keys.
- **Commits.** Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Two people with different salaries compared in one turn:** each figure comes from its own record. A test gives them distinct salaries and checks both figures (Task 7).
2. **"What would she get at 65?" after a comparison:** a which-one card, never a guess (Task 6).
3. **A name typed as "ka_ls ma_lp's", "MA_LP, KA_LS" or "…KA_LS MA_LP?":** recognised. `XKA_LS` is not (Task 4).
4. **A name shared by 109 people:** offered department-first, and nobody is auto-selected (Tasks 6 and 9).
5. **One of three records failing to load:** the other two are answered, and the answer says whose record could not be read (Task 5).
6. **A fifth person chosen:** the least recently used one leaves, and the chips show it (Task 8).
7. **A pooled connection dropped by the server:** retried once. A driver message echoing the connection string never reaches the exception text (Task 2).
8. **A question asked in September:** the part-year salary is not reported as the "current annual salary" (Task 3).

---

### Task 1: Settings, the driver, and the discovery script

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `src/pension_chat/config.py` (after `deployment_article`, about line 166)
- Create: `scripts/pbms_discover.py`
- Modify: `.env` (local only, never committed): uncomment the four `PBMS_SQL_*` lines (lines 92–95)
- Modify: the spec (add §6.2)
- Test: `tests/test_pbms_config.py`

**Interfaces:**
- Produces:
  - `Settings.record_source: Literal["postgres", "pbms"] = "postgres"`;
  - `Settings.pbms_sql_server`, `pbms_sql_database` and `pbms_sql_user: str | None`;
  - `Settings.pbms_sql_password: SecretStr | None`;
  - `Settings.pbms_query_timeout_s: float = 5.0`;
  - the spec's §6.2, which records `SALARY_RULE` (`"gross"`, `"rate"` or `"capped"`) and `SERVICE_FROM_PAYROLL` (bool). Task 2 reads both.

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
Expected: FAIL, with `AttributeError: 'Settings' object has no attribute 'record_source'`.

- [ ] **Step 3: Add the settings and the driver**

In `config.py`, change the pydantic import to `from pydantic import SecretStr, model_validator` and add `from typing import Literal`. Then insert this directly after `deployment_article: int | None = None`:

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

Remove the leading `#` from the four `#PBMS_SQL_…=` lines in `.env`. Edit only those characters, and never print the file. Confirm that `git check-ignore .env` prints `.env`.

- [ ] **Step 6: Write the discovery script**

```python
# scripts/pbms_discover.py
"""Phase 1 discovery against PBMS: which salary PBMS uses, how it holds service, how fast reads are.

Read-only and aggregate-only. It prints counts, rates, codes, medians and
timings -- never a name, an ID, a date of birth or an amount belonging to one
person. Personal values live in memory only while they are compared.

    uv run python scripts/pbms_discover.py codes
    uv run python scripts/pbms_discover.py links
    uv run python scripts/pbms_discover.py salary --link-table <table from `links`>
    uv run python scripts/pbms_discover.py service --link-table <table from `links`>
    uv run python scripts/pbms_discover.py timing        # after Task 4
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SAMPLE = 60
_MONEY = ("gross", "rate", "capped")


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
    print("== tables that describe Employee_Status_Cd")
    for r in _rows(conn, "SELECT OBJECT_NAME(c.object_id) AS t FROM sys.columns c JOIN sys.tables t "
                         "ON t.object_id = c.object_id WHERE c.name = 'Employee_Status_Cd' "
                         "AND OBJECT_NAME(c.object_id) LIKE '%Type%'"):
        print(f"  {r['t']}")
    print("== Status codes on T_Pen_Ded_Master for 2024, and on a server-side sample of its detail rows")
    for r in _rows(conn, "SELECT m.Status AS code, COUNT(*) AS n FROM dbo.T_Pen_Ded_Master m "
                         "WHERE m.Year = 2024 GROUP BY m.Status"):
        print(f"  master {r['code']!r}: {r['n']}")
    for r in _rows(conn, "SELECT x.code, COUNT(*) AS n FROM (SELECT TOP 200000 d.Status AS code "
                         "FROM dbo.T_Pen_Ded_Master m JOIN dbo.T_Pen_Ded_Detail d "
                         "ON d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No WHERE m.Year = 2024) x "
                         "GROUP BY x.code"):
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
    best = None
    for start in range(last - 9, last - 2):
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
        if p["svc"] is None:
            continue
        months = sum(int(r["months"] or 0) for r in _years(conn, p["person"], True))
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
    if args.what in ("salary", "service") and not args.link_table:
        raise SystemExit("--link-table is required; run `links` first")
    conn = _connect()
    try:
        {"codes": lambda: codes(conn), "links": lambda: links(conn),
         "salary": lambda: salary(conn, args.link_table),
         "service": lambda: service(conn, args.link_table)}[args.what]()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run discovery**

```
uv run python scripts/pbms_discover.py codes
uv run python scripts/pbms_discover.py links
uv run python scripts/pbms_discover.py salary --link-table <a table printed by links>
uv run python scripts/pbms_discover.py service --link-table <same table>
```
Expected: counts only. If `links` prints nothing, record "no annuity link table: salary rule unverified" and use the defaults below.

- [ ] **Step 8: Record the findings as spec §6.2**

Append to the spec after §6.1:

```markdown
### 6.2 Settled by Task 1 (date)

- **Salary rule:** `SALARY_RULE = "<gross|rate|capped>"` -- <n>/<m> retirees matched PBMS's FAS within 1% (<all numbers printed>). Status filter: <'A' rows only | all rows>.
- **Service:** `SERVICE_FROM_PAYROLL = <True|False>` -- <n>/<m> within half a year of `MEAB_Svc` (median PBMS <x>, counted <y>).
- **Status codes:** <codes printed>; mapped: `A` -> active.
```

**Decision rule:**
- Choose the salary candidate with the most matches. If none reaches 50% of the sample, choose `"gross"`, and add to client question 38: "our three candidate salaries matched PBMS's FAS for at most N of M retirees".
- Set `SERVICE_FROM_PAYROLL = True` if at least 70% are within half a year, otherwise `False`.
- If "all rows" matched better than "Status='A' rows only", note it. Task 2 then drops `AND d.Status = 'A'`.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/pension_chat/config.py scripts/pbms_discover.py tests/test_pbms_config.py docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md
git commit -m "PBMS: settings, the ODBC driver, and the discovery that settles which salary PBMS counts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The guard and the fixed SQL

**Files:**
- Create: `src/pension_chat/pbms/__init__.py` (a docstring only for now)
- Create: `src/pension_chat/pbms/queries.py`, `src/pension_chat/pbms/connection.py`
- Modify: `src/pension_chat/records.py` (add `RecordUnavailable`)
- Test: `tests/test_pbms_queries.py`, `tests/test_pbms_connection.py`

**Interfaces:**
- Consumes: `SALARY_RULE` and `SERVICE_FROM_PAYROLL` from spec §6.2.
- Produces:
  - `records.RecordUnavailable(Exception)`;
  - `pbms.queries.Query(sql: str, params: tuple[str, ...], timeout_s: float | None = None)`;
  - `REGISTRY: dict[str, Query]`, with the keys `"name_directory"`, `"resolve_name"`, `"resolve_meab_id"`, `"record"` and `"salary_years"`;
  - `ALLOWED_COLUMNS`, `check_statement(sql)`, `SALARY_RULE` and `SERVICE_FROM_PAYROLL`;
  - `pbms.connection.PbmsUnavailable(RecordUnavailable)` and `UnknownQuery`;
  - `connection_string(*, server, database, user, password)`;
  - `PbmsClient(conn_str, *, query_timeout_s=5.0, max_connections=4, connect=None)`, with `async fetch(name, **params) -> list[dict]` and `async close()`.
- Row columns:
  - `name_directory`: `First_Name`, `Last_Name`;
  - `resolve_name` and `resolve_meab_id`: `MEAB_Id`, `First_Name`, `Last_Name`, `Birth_Year`, `Employee_Status_Cd`, `ER_Type_Cd`, `Er_Type_Short_Desc`, `Department_Short_Desc`;
  - `record`: those plus `Birth_Date`, `Hire_Date`, `First_Payroll_Date`, `Seperation_Date`, `Tier_Type_Cd` and `Reciprocal_Flg`;
  - `salary_years`: `Year`, `Salary`, `Contribution_Months`.

- [ ] **Step 1: Write the failing tests for the SQL**

```python
# tests/test_pbms_queries.py
"""The only SQL the chatbot sends PBMS: read-only, allow-listed, and indexed."""
from __future__ import annotations

import re

import pytest

from pension_chat.pbms.queries import ALLOWED_COLUMNS, REGISTRY, check_statement


def test_the_registry_holds_exactly_the_five_statements() -> None:
    assert set(REGISTRY) == {"name_directory", "resolve_name", "resolve_meab_id", "record", "salary_years"}


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_statement_passes_the_guard(name: str) -> None:
    check_statement(REGISTRY[name].sql)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_placeholders_match_declared_parameters(name: str) -> None:
    assert REGISTRY[name].sql.count("?") == len(REGISTRY[name].params)


@pytest.mark.parametrize("name", ["name_directory", "resolve_name", "resolve_meab_id"])
def test_lookups_cover_active_members_only(name: str) -> None:
    sql = REGISTRY[name].sql
    assert "e.Employee_Status_Cd = 'A'" in sql and "e.Status = 'A'" in sql and "LIKE 'M-%'" in sql


def test_the_directory_load_may_take_longer() -> None:
    assert REGISTRY["name_directory"].timeout_s == 30


@pytest.mark.parametrize("bad", [
    "UPDATE dbo.T_Employees SET Status = 'I'",
    "SELECT 1; DROP TABLE x",
    "SELECT p.Ssn FROM dbo.T_Person_Info p",
    "SELECT e.SSN FROM dbo.T_Employees e",
    "SELECT p.CCLast FROM dbo.T_Person_Info p",
    "SELECT p.PbsEmail FROM dbo.T_Person_Info p",
    "SELECT p.Middle_Name FROM dbo.T_Person_Info p",
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
        assert not re.fullmatch(
            r"(?i)e?ssn|cc\w*|pbsaddress\w*|pbsemail|pbs\w*_phone|maiden_name|middle_name|memberid", col)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_pbms_queries.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.pbms'`.

- [ ] **Step 3: Write `queries.py`**

Set `SALARY_RULE` and `SERVICE_FROM_PAYROLL` to the §6.2 values. If §6.2 says the detail-status filter hurt matching, remove `AND d.Status = 'A'` from `_PAY_JOIN`.

```python
# src/pension_chat/pbms/queries.py
"""The only SQL the chatbot sends PBMS: named, parameterised, read-only.

Nothing here is assembled from user input. Each statement is checked at import
by `check_statement`, and a test runs the same check, so a statement that
writes, names a column outside `ALLOWED_COLUMNS`, or reads a personal
identifier cannot ship.

Employees are found by first and last name among *active* members (spec §3.2);
the MEAB ID is the internal key a choice resolves to. Salary is read through
`T_Pen_Ded_Master`, never by filtering `T_Pen_Ded_Detail` on the person: that
column has no index, and one member's read scanned 46.7M rows (spec §6.1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    sql: str
    params: tuple[str, ...]
    # Seconds; None means the client's default (5 s).
    timeout_s: float | None = None


# How a year's salary is built from pay periods, and whether service is counted
# from them -- settled against PBMS's own annuity calculations (spec §6.2).
SALARY_EXPRESSIONS = {"gross": "SUM(d.Gross)", "rate": "MAX(d.Ann_Sal)", "capped": "SUM(d.Capped_Salary)"}
SALARY_RULE = "gross"
SERVICE_FROM_PAYROLL = True

ALLOWED_COLUMNS: frozenset[str] = frozenset({
    "MEAB_Id", "Person_Info_Seq_No", "Employee_Seq_No",
    "First_Name", "Last_Name", "Birth_Date",
    "Hire_Date", "First_Payroll_Date", "Seperation_Date", "Employee_Status_Cd",
    "Tier_Type_Cd", "Reciprocal_Flg", "ER_Type_Cd", "Er_Type_Short_Desc",
    "Department_Seq_No", "Department_Short_Desc", "Status",
    "Pen_Ded_Master_Seq_No", "PPE", "Gross", "Ann_Sal", "Capped_Salary", "Ded",
})

_WRITE = re.compile(
    r"\b(insert|update|delete|merge|exec|execute|drop|alter|create|truncate|grant|revoke|into|openrowset|openquery)\b",
    re.IGNORECASE,
)
_PERSONAL = re.compile(
    r"\b(e?ssn|cc\w*|pbsaddress\w*|pbsemail|pbs\w*_phone|maiden_name|middle_name|memberid)\b", re.IGNORECASE
)
# Every column is written alias.Column; the aliases are one or two letters.
_QUALIFIED = re.compile(r"\b[a-z]{1,2}\.([A-Za-z_]+)\b")


def check_statement(sql: str) -> None:
    """Raise ValueError unless `sql` is one read-only, allow-listed SELECT."""
    body = sql.strip()
    if not re.match(r"(?is)^select\b", body):
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


_ACTIVE = "l.MEAB_Id LIKE 'M-%' AND e.Status = 'A' AND e.Employee_Status_Cd = 'A'"

_PEOPLE = """
  FROM dbo.T_Pbs_Linker l
  JOIN dbo.T_Person_Info p ON p.Person_Info_Seq_No = l.Person_Info_Seq_No
  JOIN dbo.T_Employees e ON e.Employee_Seq_No = l.Employee_Seq_No"""

_DESCRIBED = """
  LEFT JOIN dbo.T_Employer_Types et ON et.ER_Type_Cd = e.ER_Type_Cd
  LEFT JOIN dbo.T_Departments dp ON dp.Department_Seq_No = e.Department_Seq_No"""

_WHO = """
       l.MEAB_Id, p.First_Name, p.Last_Name, YEAR(p.Birth_Date) AS Birth_Year,
       e.Employee_Status_Cd, e.ER_Type_Cd, et.Er_Type_Short_Desc, dp.Department_Short_Desc"""

_PAY_JOIN = """
  FROM dbo.T_Pbs_Linker l
  JOIN dbo.T_Pen_Ded_Master m ON m.Person_Info_Seq_No = l.Person_Info_Seq_No
  JOIN dbo.T_Pen_Ded_Detail d ON d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No
 WHERE l.MEAB_Id = ? AND m.Status = 'A' AND d.Status = 'A'"""

REGISTRY: dict[str, Query] = {
    "name_directory": Query(
        f"""SELECT DISTINCT p.First_Name, p.Last_Name{_PEOPLE}
 WHERE {_ACTIVE} AND p.First_Name IS NOT NULL AND p.Last_Name IS NOT NULL""",
        (),
        timeout_s=30,
    ),
    "resolve_name": Query(
        f"""SELECT{_WHO}{_PEOPLE}{_DESCRIBED}
 WHERE {_ACTIVE} AND p.First_Name = ? AND p.Last_Name = ?""",
        ("first_name", "last_name"),
    ),
    "resolve_meab_id": Query(
        f"""SELECT{_WHO}{_PEOPLE}{_DESCRIBED}
 WHERE {_ACTIVE} AND l.MEAB_Id = ?""",
        ("meab_id",),
    ),
    "record": Query(
        f"""SELECT{_WHO}, p.Birth_Date,
       e.Hire_Date, e.First_Payroll_Date, e.Seperation_Date, e.Tier_Type_Cd, e.Reciprocal_Flg{_PEOPLE}{_DESCRIBED}
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

`record` does not filter on active status. A subject chosen while active stays readable if their status changes mid-conversation.

Create `src/pension_chat/pbms/__init__.py` containing `"""The client's PBMS database, read-only (Phase 1)."""`.

- [ ] **Step 4: Run the query tests**

Run: `uv run pytest tests/test_pbms_queries.py -v`
Expected: all pass.

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
        self.conn.log.append((sql, args, self.conn.timeout))    # the timeout in force for this statement
        if self.conn.fail:
            raise self.conn.fail
        self.description = [("MEAB_Id",), ("First_Name",)]

    def fetchall(self):
        return [("M-000001", "AB_CD")]

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
    made, opened = list(conns), []

    def connect(conn_str, timeout):
        conn = made.pop(0)
        opened.append(conn)
        return conn

    return PbmsClient(connection_string(server="s", database="d", user="u", password=SECRET),
                      connect=connect), opened


async def test_a_registry_statement_returns_rows_as_dicts() -> None:
    client, opened = _client(FakeConn())
    rows = await client.fetch("resolve_meab_id", meab_id="M-000001")
    assert rows == [{"MEAB_Id": "M-000001", "First_Name": "AB_CD"}]
    assert opened[0].setup == ["SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"]
    assert opened[0].log[0][2] == 5


async def test_a_statement_with_its_own_timeout_uses_it_then_restores_the_default() -> None:
    client, opened = _client(FakeConn())
    await client.fetch("name_directory")
    assert opened[0].log[0][2] == 30
    assert opened[0].timeout == 5


async def test_an_unknown_name_never_reaches_the_driver() -> None:
    client, opened = _client(FakeConn())
    with pytest.raises(UnknownQuery):
        await client.fetch("SELECT * FROM dbo.T_Person_Info")
    assert opened == []


async def test_a_missing_parameter_is_refused() -> None:
    client, _ = _client(FakeConn())
    with pytest.raises(TypeError):
        await client.fetch("resolve_name", first_name="AB_CD")


async def test_a_driver_failure_is_unavailable_and_carries_no_secret() -> None:
    boom = Exception("08001", f"login failed for DRIVER=...;PWD={SECRET}")
    client, _ = _client(FakeConn(fail=boom))
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
    assert "ApplicationIntent=ReadOnly" in cs and "APP=pension-chat-readonly" in cs
    assert "PWD={pa}}ss-w0rd-XYZ}" in cs
```

- [ ] **Step 6: Run them to see them fail**

Run: `uv run pytest tests/test_pbms_connection.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.pbms.connection'`.

- [ ] **Step 7: Add `RecordUnavailable` to `records.py`**

Add it after `EmployeeRecord`:

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
connection declares itself read-only (`ApplicationIntent=ReadOnly` and the
driver's read-only access mode), names itself to the DBA
(`APP=pension-chat-readonly`), reads uncommitted so it never holds locks PBMS's
own work could wait on, and gives up on a statement after its timeout.

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
        timeout = max(1, int(query.timeout_s or self._timeout))
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run, name, query, args, timeout), timeout=timeout * 2 + 1
            )
        except TimeoutError:
            raise PbmsUnavailable(f"PBMS query {name} timed out") from None

    def _run(self, name: str, query: Query, args: tuple[Any, ...], timeout: int) -> list[dict[str, Any]]:
        if not self._slots.acquire(timeout=self._timeout):
            raise PbmsUnavailable(f"PBMS query {name}: no connection free")
        try:
            # A pooled connection may have been dropped by the server while
            # idle; that costs one retry on a fresh one, not the user's turn.
            for attempt in (1, 2):
                conn, reused = self._checkout(fresh=attempt == 2)
                conn.timeout = timeout
                try:
                    rows = self._execute(conn, query, args)
                except Exception as exc:
                    _close_quietly(conn)
                    if attempt == 1 and reused:
                        continue
                    raise PbmsUnavailable(f"PBMS query {name} failed ({_sqlstate(exc)})") from None
                conn.timeout = self._timeout
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
Expected: all pass.

- [ ] **Step 10: Lint, type-check and commit**

Run: `uv run ruff check src/pension_chat/pbms tests/test_pbms_*.py && uv run mypy src/pension_chat/pbms src/pension_chat/records.py`

```bash
git add src/pension_chat/pbms src/pension_chat/records.py tests/test_pbms_queries.py tests/test_pbms_connection.py
git commit -m "PBMS: the guard -- named, read-only, allow-listed statements on a small read-only pool

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `PbmsProvider`: mapping a record, and wiring it into the app

**Files:**
- Create: `src/pension_chat/pbms/provider.py`
- Modify: `src/pension_chat/pbms/__init__.py` (`build_record_provider`)
- Modify: `src/pension_chat/records.py`:
  - add `EmployeeMatch.department` and `EmployeeMatch.birth_year`;
  - add `EmployeeRecord.source`, carried into the agent record as `record_source`;
  - add `normalise_meab_id` and `MEAB_ID_PATTERN`;
  - add `set_default_provider`, with `current_provider` falling back to it.
- Modify: `src/pension_chat/main.py` (lifespan), `src/pension_chat/api/health.py`
- Test: `tests/test_pbms_provider.py`

**Interfaces:**
- Consumes: `PbmsClient.fetch` and the row columns (Task 2), and `RecordUnavailable`.
- Produces:
  - `records.MEAB_ID_PATTERN = r"^M-[0-9]{6}$"` and `normalise_meab_id(text) -> str | None`;
  - `records.set_default_provider(p)`;
  - `EmployeeMatch(key, display_name, employer=None, status=None, department=None, birth_year=None)`;
  - `EmployeeRecord(..., source=None)`;
  - the pure helpers `record_from_rows(row, salary_rows)`, `salary_from_rows(rows, *, as_of)`, `match_from_row(row)`, `reconcile_tier(code, start)` and `tier_from_participation(start)`;
  - `PbmsProvider(client, *, today=date.today)`, with `find_employees(query, scope=None)` (MEAB ID only), `get_record(key)`, `get_salary_history(key)` and `close()`;
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
    RecordUnavailable, current_provider, normalise_meab_id, set_default_provider, use_provider,
)

ROW = {
    "MEAB_Id": "M-000123", "First_Name": "QW_XZ", "Last_Name": "PL_MR", "Birth_Year": 1964,
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


def test_a_full_row_maps_to_the_record() -> None:
    rec = record_from_rows(ROW, PAY)
    assert (rec.key, rec.display_name, rec.employer, rec.department) == (
        "M-000123", "QW_XZ PL_MR", "City of Chicago", "Finance")
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
    ("2", date(2019, 1, 1), 2, "indicates Tier 3"),
    ("0", date(2005, 1, 1), 1, "tier code '0'"),
    ("", date(2017, 8, 1), None, "does not settle it"),
    ("3", date(2017, 8, 1), 3, None),
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
    assert [h.year for h in salary_from_rows(PAY, as_of=date(2026, 9, 30))] == [2024, 2025]


def test_a_match_carries_what_the_choice_card_shows() -> None:
    m = match_from_row(ROW)
    assert (m.key, m.display_name, m.employer, m.department, m.status, m.birth_year) == (
        "M-000123", "QW_XZ PL_MR", "City of Chicago", "Finance", "active", 1964)


class FakeClient:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def fetch(self, name, **params):
        self.calls.append((name, params))
        if self.fail:
            raise RecordUnavailable("down")
        if name == "salary_years":
            return PAY
        return [ROW] if params.get("meab_id") == "M-000123" else []

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
    assert await PbmsProvider(client).get_record("3f1c9e2a-a-profile-uuid") is None
    assert await PbmsProvider(client).find_employees("QW_XZ PL_MR") == []
    assert client.calls == []


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


def _settings(**overrides):
    from pension_chat.config import Settings

    base = dict(azure_openai_api_key="k", azure_openai_endpoint="https://x", azure_search_api_key="k",
                azure_search_endpoint="https://x", pghost="h", pguser="u", pgpassword="p", _env_file=None)
    return Settings(**{**base, **overrides})


def test_no_provider_unless_pbms_and_never_half_configured() -> None:
    from pension_chat.pbms import build_record_provider

    assert build_record_provider(_settings()) is None
    with pytest.raises(RuntimeError, match="PBMS_SQL_SERVER"):
        build_record_provider(_settings(record_source="pbms"))
    assert build_record_provider(_settings(
        record_source="pbms", pbms_sql_server="s", pbms_sql_database="d",
        pbms_sql_user="u", pbms_sql_password="p")) is not None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_pbms_provider.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.pbms.provider'`.

- [ ] **Step 3: Extend `records.py`**

Add these changes:
- `department: str | None = None` and `birth_year: int | None = None` as the last fields of `EmployeeMatch`;
- `source: str | None = None` as the last field of `EmployeeRecord`;
- in `to_agent_record`, the line `if self.source: record["record_source"] = self.source` before `return record`.

At module level, add:

```python
import re

MEAB_ID_PATTERN = r"^M-[0-9]{6}$"
_MEAB_ID = re.compile(MEAB_ID_PATTERN)


def normalise_meab_id(text: str | None) -> str | None:
    """`M-######` in canonical form, or None if `text` is not one. Internal key only."""
    candidate = (text or "").strip().upper()
    return candidate if _MEAB_ID.fullmatch(candidate) else None
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

from pension_chat.pbms.queries import SERVICE_FROM_PAYROLL
from pension_chat.records import EmployeeMatch, EmployeeRecord, SalaryYear, normalise_meab_id

_TIER_CODES = {"1": 1, "2": 2, "3": 3}
# Employee_Status_Cd -> the agent's employment_status; extended only from codes
# spec §6.2 found described. Anything else is reported, not guessed.
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
    birth_year = row.get("Birth_Year")
    return EmployeeMatch(
        key=key,
        display_name=_display_name(row) or "(no name on file)",
        employer=_employer(row),
        status=status or (f"PBMS status {code}" if code else None),
        department=_text(row.get("Department_Short_Desc")),
        birth_year=int(birth_year) if birth_year else None,
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
        """The active member a MEAB ID (an internal `ref`) names; nothing else is searched here."""
        meab_id = normalise_meab_id(query)
        if meab_id is None:
            return []
        return [match_from_row(r) for r in await self._client.fetch("resolve_meab_id", meab_id=meab_id)]

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

- [ ] **Step 5: Add the factory, then wire it into startup and health**

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

In `main.py`, change `lifespan`. Task 4 adds the name refresh here as well.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    from pension_chat.pbms import build_record_provider
    from pension_chat.records import set_default_provider

    logger.info("Starting Pension Chat API (env=%s)", settings.app_env)
    # RECORD_SOURCE=pbms: every turn reads employee records from PBMS, live and
    # read-only. Built before warming so a misconfiguration fails at start.
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

In `api/health.py`, add `from pension_chat.config import get_settings`, and add `"record_source": get_settings().record_source,` to the returned dict.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_pbms_provider.py tests/test_records_provider.py -v`, then `uv run pytest -q`
Expected: all pass. `evals/fixtures.py` builds `EmployeeMatch` positionally with four arguments, which still works because the new fields default to `None`.

- [ ] **Step 7: Lint, type-check and commit**

Run: `uv run ruff check . && uv run mypy .`

```bash
git add src/pension_chat/pbms src/pension_chat/records.py src/pension_chat/main.py src/pension_chat/api/health.py tests/test_pbms_provider.py
git commit -m "PBMS: PbmsProvider -- the fund's records mapped to the agent's, gaps kept as gaps; installed when RECORD_SOURCE=pbms

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The name directory and name search

**Files:**
- Create: `src/pension_chat/pbms/names.py`
- Modify: `src/pension_chat/pbms/provider.py` (add `load_names`, `names_ready`, `names_in` and `find_by_name`)
- Modify: `src/pension_chat/pbms/__init__.py` (add `refresh_names_forever`)
- Modify: `src/pension_chat/main.py` (start and cancel the refresh task)
- Modify: `src/pension_chat/records.py` (the `NameSearch` protocol)
- Modify: `scripts/pbms_discover.py` (add `timing`)
- Test: `tests/test_pbms_names.py`

**Interfaces:**
- Consumes: the `name_directory` and `resolve_name` statements, `match_from_row`, and `RecordUnavailable`.
- Produces:
  - `pbms.names.NameDirectory(pairs: Iterable[tuple[str, str]])`, with `names_in(text) -> list[tuple[str, str]]` and `__len__`;
  - `PbmsProvider.load_names() -> int`, `names_ready: bool`, `names_in(text) -> list[tuple[str, str]]` and `find_by_name(first, last) -> list[EmployeeMatch]`;
  - `records.NameSearch` (a `runtime_checkable` Protocol with `names_in` and `find_by_name`);
  - `pbms.refresh_names_forever(provider, *, every_s=21600, retry_s=60)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pbms_names.py
"""Which employees a message names -- matched in memory; PBMS is asked only on a hit. Synthetic names."""
from __future__ import annotations

import asyncio

import pytest

from pension_chat.pbms.names import NameDirectory
from pension_chat.pbms.provider import PbmsProvider
from pension_chat.records import NameSearch, RecordUnavailable

D = NameDirectory([("KA_LS", "MA_LP"), ("Qw_Xz", "PL_MR"), ("A _BC", "DE_FG"), ("O'_BR", "NO_LA"),
                   ("A._BC", "XY_ZW"), (" ", "ZZ_ZZ")])


@pytest.mark.parametrize("text, want", [
    ("What would KA_LS MA_LP's pension be at 62?", [("KA_LS", "MA_LP")]),
    ("pension for ka_ls ma_lp", [("KA_LS", "MA_LP")]),
    ("MA_LP, KA_LS -- when can they retire?", [("KA_LS", "MA_LP")]),
    ("is it KA_LS MA_LP?", [("KA_LS", "MA_LP")]),
    ("compare qw_xz pl_mr and KA_LS MA_LP", [("Qw_Xz", "PL_MR"), ("KA_LS", "MA_LP")]),
    ("A _BC DE_FG retiring", [("A _BC", "DE_FG")]),
    ("what about O'_BR NO_LA", [("O'_BR", "NO_LA")]),
    ("A._BC XY_ZW.", [("A._BC", "XY_ZW")]),
    ("XKA_LS MA_LP", []),
    ("KA_LS alone", []),
    ("What is the Tier 2 formula?", []),
    ("", []),
])
def test_names_in_a_message(text, want) -> None:
    assert D.names_in(text) == want


def test_blank_names_are_not_in_the_directory() -> None:
    assert len(D) == 5


class FakeClient:
    def __init__(self):
        self.calls, self.fail = [], False

    async def fetch(self, name, **params):
        self.calls.append((name, params))
        if self.fail:
            raise RecordUnavailable("down")
        if name == "name_directory":
            return [{"First_Name": "KA_LS", "Last_Name": "MA_LP"}]
        if name == "resolve_name":
            return [{"MEAB_Id": f"M-00000{i}", "First_Name": "KA_LS", "Last_Name": "MA_LP", "Birth_Year": 1960 + i,
                     "Employee_Status_Cd": "A", "ER_Type_Cd": "CITY", "Er_Type_Short_Desc": "City",
                     "Department_Short_Desc": "Finance"} for i in range(3)]
        return []

    async def close(self):
        pass


async def test_the_provider_matches_in_memory_and_asks_pbms_only_on_a_hit() -> None:
    client = FakeClient()
    provider = PbmsProvider(client)
    assert isinstance(provider, NameSearch)
    assert provider.names_in("KA_LS MA_LP") == [] and not provider.names_ready
    assert await provider.load_names() == 1
    assert provider.names_in("is KA_LS MA_LP eligible?") == [("KA_LS", "MA_LP")]
    assert [c[0] for c in client.calls] == ["name_directory"]
    matches = await provider.find_by_name("KA_LS", "MA_LP")
    assert [m.birth_year for m in matches] == [1960, 1961, 1962]
    assert client.calls[-1] == ("resolve_name", {"first_name": "KA_LS", "last_name": "MA_LP"})


async def test_a_failed_reload_keeps_the_directory() -> None:
    client = FakeClient()
    provider = PbmsProvider(client)
    await provider.load_names()
    client.fail = True
    with pytest.raises(RecordUnavailable):
        await provider.load_names()
    assert provider.names_in("KA_LS MA_LP") == [("KA_LS", "MA_LP")]


async def test_refresh_retries_soon_after_a_failed_first_load() -> None:
    from pension_chat.pbms import refresh_names_forever

    client = FakeClient()
    client.fail = True
    provider = PbmsProvider(client)
    task = asyncio.create_task(refresh_names_forever(provider, every_s=3600, retry_s=0.01))
    await asyncio.sleep(0.05)
    client.fail = False
    await asyncio.sleep(0.05)
    task.cancel()
    assert provider.names_ready
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_pbms_names.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.pbms.names'`.

- [ ] **Step 3: Write `names.py`**

```python
# src/pension_chat/pbms/names.py
"""Which employees a message names: active members' first and last names, held in memory.

A message cannot be sent to SQL Server to ask whether it contains a name, so
the directory of names -- and nothing else -- is loaded once and matched here,
at no database cost; PBMS is asked only when a name actually matches (spec §3.4).

Matching is deterministic and case-insensitive. A name is a run of 2-4
whitespace-separated tokens (names may hold spaces, `_`, `'` and `.`), written
"FIRST LAST" or "LAST, FIRST", with surrounding punctuation and a possessive
"'s" tolerated. A token only ever matches whole: "XKA_LS" is not "KA_LS".
"""
from __future__ import annotations

import re
from collections.abc import Iterable

# Trimmed from token edges. `_`, `'` and `.` inside a token stay: names hold them.
_EDGE = ".,;:!?\"()[]{}<>*`“”‘’"
_POSSESSIVE = re.compile(r"(?:'|’)S$")
_MAX_TOKENS = 4


def _norm(part: str) -> str:
    return " ".join(part.split()).upper()


class NameDirectory:
    def __init__(self, pairs: Iterable[tuple[str, str]] = ()) -> None:
        self._by_full: dict[str, tuple[str, str]] = {}
        for first, last in pairs:
            first, last = (first or "").strip(), (last or "").strip()
            if first and last:
                self._by_full.setdefault(f"{_norm(first)} {_norm(last)}", (first, last))

    def __len__(self) -> int:
        return len(self._by_full)

    def names_in(self, text: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, bool]] = []          # (token, followed by a comma)
        for raw in (text or "").split():
            token = raw.strip(_EDGE).upper()
            if token:
                tokens.append((token, raw.rstrip(".!?;:)\"”").endswith(",")))
        found: list[tuple[str, str]] = []
        for i in range(len(tokens)):
            for j in range(i + 1, min(i + _MAX_TOKENS, len(tokens))):
                pair = self._match(tokens[i:j + 1])
                if pair is not None and pair not in found:
                    found.append(pair)
        return found

    def _match(self, run: list[tuple[str, bool]]) -> tuple[str, str] | None:
        words = [t for t, _ in run]
        variants = [words]
        if _POSSESSIVE.search(words[-1]):
            variants.append(words[:-1] + [_POSSESSIVE.sub("", words[-1])])
        for variant in variants:
            if pair := self._by_full.get(" ".join(variant)):
                return pair
            # "LAST, FIRST": the comma marks where the last name ends.
            for k in range(1, len(variant)):
                if run[k - 1][1]:
                    last, first = " ".join(variant[:k]), " ".join(variant[k:])
                    if pair := self._by_full.get(f"{first} {last}"):
                        return pair
        return None
```

With the comma form, "MA_LP, KA_LS" tokenises to `[("MA_LP", True), ("KA_LS", False)]`, and `k=1` checks `"KA_LS MA_LP"`. The possessive is removed from the *last token of the run* only: "KA_LS MA_LP's" becomes "KA_LS MA_LP". A run never has more than 4 tokens.

- [ ] **Step 4: Add name search to the provider, and the protocol**

In `records.py`, add below `RecordProvider`:

```python
from typing import runtime_checkable  # add to the existing typing import


@runtime_checkable
class NameSearch(Protocol):
    """A record source that can say which employees a message names (PBMS)."""

    def names_in(self, text: str) -> list[tuple[str, str]]: ...

    async def find_by_name(self, first: str, last: str) -> list[EmployeeMatch]: ...
```

In `provider.py`, add `from pension_chat.pbms.names import NameDirectory`, then add `self._names: NameDirectory | None = None` in `__init__`, and these methods:

```python
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
```

In `pbms/__init__.py`, add:

```python
import asyncio
import logging

logger = logging.getLogger(__name__)


async def refresh_names_forever(provider: PbmsProvider, *, every_s: float = 6 * 3600,
                                retry_s: float = 60) -> None:
    """Keep the name directory loaded: every 6 hours, or a minute after a failure with none loaded."""
    from pension_chat.records import RecordUnavailable

    while True:
        try:
            count = await provider.load_names()
            logger.info("PBMS name directory loaded: %d names", count)
            wait = every_s
        except RecordUnavailable:
            logger.warning("PBMS name directory could not be loaded; names will not be recognised until it is"
                           if not provider.names_ready else
                           "PBMS name directory could not be refreshed; keeping the previous one")
            wait = every_s if provider.names_ready else retry_s
        await asyncio.sleep(wait)
```

In `main.py`'s `lifespan`, after `set_default_provider(record_provider)`, add:

```python
    names_task = None
    if record_provider is not None:
        from pension_chat.pbms import refresh_names_forever

        names_task = asyncio.create_task(refresh_names_forever(record_provider))
```

Before `set_default_provider(None)` at shutdown, add `if names_task is not None: names_task.cancel()`. Also make sure `import asyncio` is at the top of `main.py`; `_warm_connections` already uses it.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_pbms_names.py tests/test_pbms_provider.py -v`
Expected: all pass. If a parametrised name case fails, fix `NameDirectory`, not the case. Every case there is a form an employer will type.

- [ ] **Step 6: Add `timing` to discovery, and run it**

In `pbms_discover.py`, add `"timing"` to the choices and this function. Its output is timings and counts only.

```python
def timing(conn) -> None:
    import asyncio
    import random
    import time as _t

    from pension_chat.config import get_settings
    from pension_chat.pbms import build_record_provider

    provider = build_record_provider(get_settings().model_copy(update={"record_source": "pbms"}))
    assert provider is not None

    async def run() -> None:
        t = _t.perf_counter()
        count = await provider.load_names()
        print(f"  name_directory: {count} names in {(_t.perf_counter() - t) * 1000:.0f} ms")
        pairs = random.sample(list(provider._names._by_full.values()), 3)
        for first, last in pairs:
            t = _t.perf_counter()
            matches = await provider.find_by_name(first, last)
            print(f"  resolve_name: {len(matches)} matches in {(_t.perf_counter() - t) * 1000:.0f} ms")
        t = _t.perf_counter()
        await provider.get_record(matches[0].key)
        print(f"  get_record (record + salary in parallel): {(_t.perf_counter() - t) * 1000:.0f} ms")
        t = _t.perf_counter()
        provider.names_in("What would " + " ".join(pairs[0]) + "'s pension be at 62?")
        print(f"  names_in (in memory): {(_t.perf_counter() - t) * 1e6:.0f} µs")
        await provider.close()

    print("== timings from this machine (a bare round trip here is ~250 ms)")
    asyncio.run(run())
```

Run: `uv run python scripts/pbms_discover.py timing`
Expected: `name_directory` takes under 30 s and loads about 12,920 names. `resolve_name` takes roughly 400 ms plus one round trip. `get_record` takes about one round trip. `names_in` takes well under 1 ms. If `resolve_name` exceeds 2 s, stop and report: it would push first-token latency past the 3 s target on name turns.

Append the timings to spec §6.2.

- [ ] **Step 7: Lint, type-check and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy .`

```bash
git add src/pension_chat/pbms src/pension_chat/records.py src/pension_chat/main.py scripts/pbms_discover.py tests/test_pbms_names.py docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md
git commit -m "PBMS: the name directory -- which employees a message names, matched in memory; PBMS asked only on a hit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: People in the agent's state, and loading their records in parallel

**Files:**
- Create: `src/pension_chat/agent/people.py` (pure: labels, constants; Task 6 adds `decide`)
- Modify: `src/pension_chat/agent/state.py` (new keys; `subjects` parameter; `DURABLE_KEYS`)
- Modify: `src/pension_chat/agent/graph.py:208-300` (`run_agent_stream`: the `subjects` parameter and the `people` event)
- Modify: `src/pension_chat/agent/nodes.py:290-337` (`fetch_employee_record`)
- Modify: `src/pension_chat/agent/loop.py:141-183` (`_context`: `_record_notes`, the unavailable note)
- Modify: `src/pension_chat/agent/card.py` (`record_label`)
- Test: `tests/test_people_loading.py`

**Interfaces:**
- Consumes: `current_provider` and `RecordUnavailable` (Tasks 2–3), and the agent record's `record_source`, `display_name`, `employer`, `department`, `service_source` and `missing`.
- Produces:
  - `people.MAX_PEOPLE = 4`, `people.MAX_LISTED = 12` and `people.norm_name(s) -> str`;
  - `people.labels(subjects: list[dict]) -> dict[str, str]`, mapping ref to label, with the department added only when two people share a name;
  - the state keys:
    - `subjects: list[dict]` (per turn; the roster, most recent first; entries `{"ref", "display_name", "first_name", "last_name", "department", "employer", "birth_year"}`);
    - `turn_people: list[str]` (per turn);
    - `last_people: list[str]` (durable);
    - `people: dict[str, dict]` (per turn; `{ref: {"record", "salary_history", "member_facts", "label"}}`);
    - `pinned_people: dict[str, dict]` (durable; `{ref: {"record", "salary_history"}}`);
    - `subject_suggestion: dict | None` (per turn);
    - `unavailable_people: list[str]` (per turn);
  - `initial_state(..., *, subjects=None)` and `turn_input(..., *, subjects=None)`;
  - `run_agent_stream(..., subjects: list[dict] | None = None)`, which yields `{"type": "people", "active": [refs]}` when `triage` reports `turn_people`;
  - `loop._record_notes(record) -> str`;
  - the card's `"record_label": "PBMS record" | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_people_loading.py
"""People in context: labels, parallel loading, per-person pins, and one-person turns unchanged."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from pension_chat.agent import nodes
from pension_chat.agent.card import calculation_card
from pension_chat.agent.loop import _context, _record_notes
from pension_chat.agent.people import labels
from pension_chat.agent.state import DURABLE_KEYS, initial_state, turn_input
from pension_chat.agent.worksheet import WorkSheet
from pension_chat.records import EmployeeRecord, RecordUnavailable, SalaryYear, use_provider

A = {"ref": "M-000001", "display_name": "KA_LS MA_LP", "first_name": "KA_LS", "last_name": "MA_LP",
     "department": "Finance", "employer": "City", "birth_year": 1964}
B = {"ref": "M-000002", "display_name": "QW_XZ PL_MR", "first_name": "QW_XZ", "last_name": "PL_MR",
     "department": "Water", "employer": "City", "birth_year": 1970}
A2 = {**A, "ref": "M-000003", "department": "Streets"}


class _Stub:
    def __init__(self, fail=()):
        self.asked, self.fail, self.concurrent, self.peak = [], set(fail), 0, 0

    async def find_employees(self, query, scope=None):
        return []

    async def get_record(self, key):
        self.asked.append(key)
        self.concurrent += 1
        self.peak = max(self.peak, self.concurrent)
        await asyncio.sleep(0.01)
        self.concurrent -= 1
        if key in self.fail:
            raise RecordUnavailable("down")
        salary = {"M-000001": 90000, "M-000002": 60000}.get(key, 70000)
        return EmployeeRecord(key=key, tier=2, birth_date=date(1964, 9, 30), hire_date=date(2012, 3, 1),
                              display_name="X", department="D", status="active",
                              service_years=Decimal("14"), service_source="counted from payroll",
                              source="PBMS", missing={"tier": "note"} if key == "M-000001" else {})

    async def get_salary_history(self, key):
        return [SalaryYear(2025, Decimal({"M-000001": 90000, "M-000002": 60000}.get(key, 70000)))]


def test_labels_add_the_department_only_when_names_collide() -> None:
    assert labels([A, B]) == {"M-000001": "KA_LS MA_LP", "M-000002": "QW_XZ PL_MR"}
    assert labels([A, A2]) == {"M-000001": "KA_LS MA_LP (Finance)", "M-000003": "KA_LS MA_LP (Streets)"}


def test_the_roster_is_carried_every_turn_and_last_people_is_durable() -> None:
    assert initial_state("q", "s", subjects=[A])["subjects"] == [A]
    assert turn_input("q", "s", subjects=[A])["subjects"] == [A]
    assert {"last_people", "pinned_people"} <= DURABLE_KEYS
    assert "turn_people" not in DURABLE_KEYS and "people" not in DURABLE_KEYS


async def test_several_people_load_in_parallel_and_the_first_fills_the_single_member_keys() -> None:
    stub = _Stub()
    with use_provider(stub):
        out = await nodes.fetch_employee_record({"turn_people": ["M-000001", "M-000002"], "subjects": [A, B]})
    assert stub.peak == 2
    assert set(out["people"]) == {"M-000001", "M-000002"}
    assert out["people"]["M-000002"]["label"] == "QW_XZ PL_MR"
    assert out["employee_record"]["id"] == "M-000001"
    assert out["last_people"] == ["M-000001", "M-000002"]
    assert set(out["pinned_people"]) == {"M-000001", "M-000002"}


async def test_a_pinned_person_is_not_read_again() -> None:
    stub = _Stub()
    pinned = {"M-000001": {"record": {"id": "M-000001", "article_number": 8, "tier": 2}, "salary_history": []}}
    with use_provider(stub):
        await nodes.fetch_employee_record({"turn_people": ["M-000001", "M-000002"], "subjects": [A, B],
                                           "pinned_people": pinned})
    assert stub.asked == ["M-000002"]


async def test_one_unreadable_record_is_reported_and_the_others_load() -> None:
    with use_provider(_Stub(fail={"M-000002"})):
        out = await nodes.fetch_employee_record({"turn_people": ["M-000001", "M-000002"], "subjects": [A, B]})
    assert set(out["people"]) == {"M-000001"} and out["unavailable_people"] == ["M-000002"]


async def test_without_turn_people_the_existing_path_runs(monkeypatch) -> None:
    called = []

    async def fake_load(employee_id):
        called.append(employee_id)
        return None

    monkeypatch.setattr(nodes, "_load_record", fake_load)
    await nodes.fetch_employee_record({"employee_id": "abc"})
    assert called == ["abc"]


def test_the_context_names_whose_record_could_not_be_read() -> None:
    state = {**initial_state("q", "s", subjects=[A, B]), "unavailable_people": ["M-000002"]}
    assert "QW_XZ PL_MR" in _context(state) and "could not be read" in _context(state)


def test_the_record_notes_name_the_employee_not_the_internal_key() -> None:
    notes = _record_notes({"id": "M-000123", "display_name": "QW_XZ PL_MR", "department": "Finance",
                           "record_source": "PBMS", "service_source": "counted from payroll",
                           "missing": {"tier": "PBMS records Tier 2, but ..."}})
    assert "QW_XZ PL_MR" in notes and "Finance" in notes and "tier: PBMS records Tier 2" in notes
    assert "M-000123" not in notes


def test_the_card_names_the_pbms_record_without_its_key() -> None:
    state = {**initial_state("q", "s"),
             "employee_record": {"id": "M-000123", "article_number": 8, "record_source": "PBMS"},
             "calculation_result": {"r": {"base": {"ok": True, "inputs_used": {}, "annual_amount": "1"}}}}
    card = calculation_card(state, WorkSheet.from_state(state))
    assert card is not None and card["record_label"] == "PBMS record"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_people_loading.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.agent.people'`.

- [ ] **Step 3: Write `people.py` (labels and constants)**

```python
# src/pension_chat/agent/people.py
"""Who a conversation is about: the people chosen in it, and who each turn concerns (spec §4).

Pure and deterministic -- no model and no database. The people are the
conversation's own list (`sessions.subjects`, most recent first, at most
MAX_PEOPLE); each is keyed by an internal MEAB ID (`ref`) that is never shown.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

MAX_PEOPLE = 4
# More candidates than this and a choice starts with the department.
MAX_LISTED = 12


def norm_name(text: str | None) -> str:
    return " ".join((text or "").split()).upper()


def labels(subjects: list[dict[str, Any]]) -> dict[str, str]:
    """How each person is named to the model and in tables: their name, plus the
    department only when two people in context share that name."""
    counts = Counter(norm_name(s.get("display_name")) for s in subjects)
    out: dict[str, str] = {}
    for s in subjects:
        name = s.get("display_name") or "Employee"
        if counts[norm_name(name)] > 1:
            name = f"{name} ({s.get('department') or s.get('employer') or 'no department on file'})"
        out[s["ref"]] = name
    return out
```

- [ ] **Step 4: Update the state and `run_agent_stream`**

In `state.py`, add these to `PensionAgentState` next to `employee_id`:

```python
    # The conversation's people (sessions.subjects), most recent first, passed
    # in every turn. Each is keyed by an internal MEAB ID (`ref`), never shown.
    # Not the principal: `employee_id` still scopes row-level security.
    subjects: list[dict[str, Any]]
    # Who this turn is about (refs), decided deterministically by `triage`.
    turn_people: list[str]
    # Who the previous turn was about: a turn that names nobody continues with them.
    last_people: list[str]
    # This turn's people, loaded: {ref: {"record", "salary_history", "member_facts", "label"}}.
    people: dict[str, dict[str, Any]]
    # Each person's first-loaded record, reused on later turns (spec §4.3).
    pinned_people: dict[str, dict[str, Any]]
    # A choice put to the user: unfamiliar names' candidates, or which of several.
    subject_suggestion: dict[str, Any] | None
    # People whose record could not be read this turn -- distinct from "no record".
    unavailable_people: list[str]
```

- Add `"last_people"` and `"pinned_people"` to `DURABLE_KEYS`.
- `initial_state` gains a keyword-only parameter `subjects: list[dict[str, Any]] | None = None`. Its dict gains:

  ```python
  "subjects": list(subjects or []), "turn_people": [], "last_people": [], "people": {},
  "pinned_people": {}, "subject_suggestion": None, "unavailable_people": [],
  ```
- `turn_input` gains the same parameter and adds `subjects=list(subjects or []),` to `fresh.update(...)`.

In `graph.py`, `run_agent_stream` gains `subjects: list[dict[str, Any]] | None = None` after `thread_owner`, and passes `subjects=subjects` to both `initial_state(...)` and `turn_input(...)`. Then, in the `updates` handling, replace `for node_name in payload:` with:

```python
            for node_name in payload:
                # Who this turn is about is known the moment triage finishes --
                # before any record is read or token written -- so the chips
                # change then, not after the answer (spec §4.4).
                if node_name == "triage":
                    active = (payload[node_name] or {}).get("turn_people")
                    if active is not None:
                        yield {"type": "people", "active": list(active)}
                label = STAGE_LABELS.get(node_name)
                if label:
                    yield {"type": "status", "stage": label, "node": node_name}
```

- [ ] **Step 5: Load every person in parallel**

In `nodes.py`, add `import asyncio`, add `RecordUnavailable` to the records import, and add `from pension_chat.agent import people as people_mod`. At the top of `fetch_employee_record`, after `started = time.perf_counter()`, add:

```python
    refs = list(state.get("turn_people") or [])
    if refs and current_provider() is not None:
        return await _fetch_people(state, refs, started)
```

Then add below `fetch_employee_record`:

```python
async def _fetch_people(state: PensionAgentState, refs: list[str], started: float) -> dict:
    """Every person this turn is about, read in parallel (spec §4.3).

    A pinned person is not read again. One unreadable record does not sink the
    others: it is listed in `unavailable_people` and the answer says whose. The
    first person also fills the single-member keys, so a one-person turn is
    exactly the path every other turn has always taken.
    """
    pinned: dict[str, dict] = dict(state.get("pinned_people") or {})
    names = people_mod.labels(state.get("subjects") or [])

    async def load(ref: str) -> dict | None:
        if ref in pinned:
            return pinned[ref]
        loaded = await _from_provider(ref)
        return None if loaded is None else {"record": loaded[0], "salary_history": loaded[1]}

    results = await asyncio.gather(*(load(ref) for ref in refs), return_exceptions=True)
    loaded_people: dict[str, dict] = {}
    unavailable: list[str] = []
    for ref, result in zip(refs, results, strict=True):
        if isinstance(result, RecordUnavailable) or result is None:
            unavailable.append(ref)
            continue
        if isinstance(result, BaseException):
            raise result
        pinned[ref] = result
        facts = build_member_facts(
            result["record"],
            [(date.fromisoformat(h["effective_date"]), Decimal(str(h["annual_salary"])))
             for h in result["salary_history"]],
        )
        loaded_people[ref] = {**result, "member_facts": facts,
                              "label": names.get(ref) or result["record"].get("display_name") or "Employee"}

    update: dict[str, Any] = {
        "people": loaded_people,
        "unavailable_people": unavailable,
        "pinned_people": pinned,
        "last_people": refs,
        "timings": {"fetch_employee_record": time.perf_counter() - started},
    }
    first = next((loaded_people[r] for r in refs if r in loaded_people), None)
    if first is None:
        update.update(employee_record=None, salary_history=[])
    else:
        update.update(employee_record=first["record"], salary_history=first["salary_history"],
                      member_facts=first["member_facts"])
    return update
```

- [ ] **Step 6: Update the context and the card**

In `loop.py`, add `from pension_chat.agent.people import labels as people_labels`. Change the final `else:` of the facts block (lines 182–183) to:

```python
    else:
        lines.append("No member record is available; answer from the Code alone.")
    if state.get("unavailable_people"):
        names = people_labels(state.get("subjects") or [])
        lines.append(
            "The fund's records could not be read just now for: "
            + ", ".join(names.get(r, "one employee") for r in state["unavailable_people"])
            + ". Say so plainly, and do not state figures from their records."
        )
    record = state.get("employee_record") or {}
    if record.get("record_source") and len(state.get("people") or {}) <= 1:
        lines.append(_record_notes(record))
```

Add this above `_context`:

```python
def _record_notes(record: dict[str, Any]) -> str:
    """Who this is and what their record could not say -- only for a fund record (PBMS).

    Named by name and department, never by the internal MEAB ID. A gap the
    record reports is something to tell the employer, not something to fill.
    """
    who = ", ".join(
        str(v) for v in (record.get("display_name"), record.get("department") or record.get("employer")) if v
    )
    parts = [f"This conversation is about {who or 'an employee'}, from the fund's own records "
             f"({record['record_source']})."]
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

In `card.py`, add `"record_label": _record_label(state),` after `"support_level"`, and add:

```python
def _record_label(state: Mapping[str, Any]) -> str | None:
    """Whose record the "Record" inputs came from -- the fund's, named without its internal key."""
    record = state.get("employee_record") or {}
    return f"{record['record_source']} record" if record.get("record_source") else None
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_people_loading.py tests/test_records_provider.py -v`, then `uv run pytest -q`
Expected: all pass. Existing tests are unaffected, because `turn_people` is empty unless a later task sets it.

- [ ] **Step 8: Commit**

```bash
git add src/pension_chat/agent/people.py src/pension_chat/agent/state.py src/pension_chat/agent/graph.py src/pension_chat/agent/nodes.py src/pension_chat/agent/loop.py src/pension_chat/agent/card.py tests/test_people_loading.py
git commit -m "Agent: people in context -- each turn's people loaded in parallel, pinned per person, gaps and failures said

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Who a turn is about: familiar names, choices, which-one

**Files:**
- Modify: `src/pension_chat/agent/people.py` (add `TurnPeople` and `decide`)
- Modify: `src/pension_chat/agent/nodes.py:116-144` (`triage`)
- Modify: `src/pension_chat/agent/graph.py:101-115` (`decline`, `subject_suggestion_message`)
- Test: `tests/test_turn_people.py`

**Interfaces:**
- Consumes:
  - `records.NameSearch` and `current_provider` (Task 4), and `RecordUnavailable`;
  - `EmployeeMatch.first_name`, `last_name`, `department`, `employer`, `birth_year` and `status` (Task 3);
  - `pbms.names.NameDirectory` (Task 4), reused to find roster names in the text;
  - the state keys from Task 5.
- Produces:
  - `people.TurnPeople(people: list[str], unfamiliar: list[tuple[str, str]], which_of: list[str])`;
  - `people.decide(question, subjects, last_people, directory_names) -> TurnPeople`;
  - `nodes._suggestion(...)`, which returns the suggestion `{"kind": "choose" | "which_one", "question": str, "choices": [{"first_name", "last_name", "unavailable": bool, "candidates": [{"ref", "display_name", "first_name", "last_name", "employer", "department", "status", "birth_year"}]}]}`;
  - `graph.subject_suggestion_message(s) -> str`;
  - triage's `turn_people` (always set when there is a roster or name search) and `out_of_scope_reason == "subject_suggestion"` for choice and which-one turns.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_turn_people.py
"""Who a turn is about -- spec §4.2, one test per rule -- and the choice replies."""
from __future__ import annotations

from pension_chat.agent import nodes
from pension_chat.agent.graph import decline, subject_suggestion_message
from pension_chat.agent.people import decide
from pension_chat.records import EmployeeMatch, RecordUnavailable, use_provider


def _p(ref, first, last, dept="Finance"):
    return {"ref": ref, "display_name": f"{first} {last}", "first_name": first, "last_name": last,
            "department": dept, "employer": "City", "birth_year": 1964}


A, B, C = _p("M-1", "KA_LS", "MA_LP"), _p("M-2", "QW_XZ", "PL_MR", "Water"), _p("M-3", "RO_BK", "TA_NE")


def test_familiar_names_need_no_query() -> None:
    d = decide("compare qw_xz pl_mr and KA_LS MA_LP", [A, B], [], [])
    assert (d.people, d.unfamiliar, d.which_of) == (["M-2", "M-1"], [], [])


def test_an_unfamiliar_name_makes_a_choice_turn_and_familiar_ones_ride_along() -> None:
    d = decide("compare KA_LS MA_LP and LI_MO GA_RD", [A], [], [("KA_LS", "MA_LP"), ("LI_MO", "GA_RD")])
    assert d.unfamiliar == [("LI_MO", "GA_RD")] and d.people == ["M-1"]


def test_no_name_continues_with_the_previous_turns_people() -> None:
    assert decide("and at 65?", [A, B], ["M-1", "M-2"], []).people == ["M-1", "M-2"]


def test_a_singular_pronoun_after_a_comparison_asks_which() -> None:
    d = decide("what would she get at 65?", [A, B], ["M-1", "M-2"], [])
    assert d.people == [] and d.which_of == ["M-1", "M-2"]
    assert decide("what would she get at 65?", [A, B], ["M-1"], []).people == ["M-1"]


def test_nothing_before_means_the_most_recent_person_or_nobody() -> None:
    assert decide("what is their tier?", [B, A], [], []).people == ["M-2"]
    assert decide("what is Tier 2?", [], [], []).people == []


def test_a_removed_person_is_not_continued_with() -> None:
    assert decide("and at 65?", [A], ["M-1", "M-2"], []).people == ["M-1"]


def test_two_people_in_context_with_the_same_name_ask_which() -> None:
    A2 = _p("M-9", "KA_LS", "MA_LP", "Streets")
    assert decide("KA_LS MA_LP at 62?", [A, A2], [], []).which_of == ["M-1", "M-9"]


def test_at_most_four_people_in_a_turn() -> None:
    many = [_p(f"M-{i}", f"A{i}_X", f"B{i}_Y") for i in range(5)]
    text = " and ".join(p["display_name"] for p in many)
    assert len(decide(text, many, [], []).people) == 4


class _Stub:
    def __init__(self, count=1, fail=False):
        self.count, self.fail, self.searched = count, fail, []

    def names_in(self, text):
        return [("LI_MO", "GA_RD")] if "LI_MO GA_RD" in text.upper() else []

    async def find_by_name(self, first, last):
        self.searched.append((first, last))
        if self.fail:
            raise RecordUnavailable("down")
        return [EmployeeMatch(f"M-{i:06d}", f"{first} {last}", "City", "active", f"Dept {i % 3}", 1960 + i,
                              first, last) for i in range(self.count)]

    async def find_employees(self, query, scope=None):
        return []

    async def get_record(self, key):
        raise AssertionError("a choice turn must not load a record")

    async def get_salary_history(self, key):
        raise AssertionError("a choice turn must not load a record")


def _state(q, subjects=(), last=()):
    return {"question": q, "messages": [q], "subjects": list(subjects), "last_people": list(last)}


async def test_triage_offers_one_card_per_unfamiliar_name() -> None:
    with use_provider(_Stub(count=3)):
        out = await nodes.triage(_state("compare KA_LS MA_LP and LI_MO GA_RD at 62", [A]))
    s = out["subject_suggestion"]
    assert out["out_of_scope_reason"] == "subject_suggestion" and out["turn_people"] == ["M-1"]
    assert s["kind"] == "choose" and s["question"].startswith("compare")
    assert [(c["first_name"], len(c["candidates"])) for c in s["choices"]] == [("LI_MO", 3)]


async def test_triage_decides_familiar_people_without_a_query() -> None:
    stub = _Stub()
    with use_provider(stub):
        out = await nodes.triage(_state("and KA_LS MA_LP at 65?", [A, B]))
    assert out["turn_people"] == ["M-1"] and out.get("subject_suggestion") is None and stub.searched == []


async def test_triage_which_one_uses_the_roster_and_no_query() -> None:
    stub = _Stub()
    with use_provider(stub):
        out = await nodes.triage(_state("what would he get?", [A, B], ["M-1", "M-2"]))
    s = out["subject_suggestion"]
    assert s["kind"] == "which_one" and [c["ref"] for c in s["choices"][0]["candidates"]] == ["M-1", "M-2"]
    assert stub.searched == []


async def test_without_roster_or_name_search_triage_is_unchanged() -> None:
    out = await nodes.triage(_state("What is Tier 2?"))
    assert "turn_people" not in out and out.get("subject_suggestion") is None


def _choice(count, unavailable=False, first="LI_MO", last="GA_RD"):
    return {"first_name": first, "last_name": last, "unavailable": unavailable,
            "candidates": [{"ref": f"M-{i}", "display_name": f"{first} {last}", "department": "Finance",
                            "employer": "City", "birth_year": 1964} for i in range(count)]}


def test_the_messages() -> None:
    one = subject_suggestion_message({"kind": "choose", "question": "q", "choices": [_choice(1)]})
    assert one.startswith("Did you mean LI_MO GA_RD (Finance, born 1964)?") and "M-0" not in one
    assert "There are 9 active employees named LI_MO GA_RD" in subject_suggestion_message(
        {"kind": "choose", "question": "q", "choices": [_choice(9)]})
    assert "which department" in subject_suggestion_message({"kind": "choose", "question": "q", "choices": [_choice(40)]})
    assert "couldn't find an active employee" in subject_suggestion_message(
        {"kind": "choose", "question": "q", "choices": [_choice(0)]})
    assert "not answering" in subject_suggestion_message({"kind": "choose", "question": "q", "choices": [_choice(0, True)]})
    two = subject_suggestion_message({"kind": "choose", "question": "q", "choices": [_choice(1), _choice(3, first="RO_BK")]})
    assert "LI_MO GA_RD" in two and "RO_BK GA_RD" in two and "Once you've chosen" in two
    which = subject_suggestion_message({"kind": "which_one", "question": "q",
                                        "choices": [{"candidates": [A, B], "unavailable": False}]})
    assert which == "Which of them do you mean: KA_LS MA_LP or QW_XZ PL_MR? Choose below."


async def test_decline_answers_the_suggestion_without_a_model() -> None:
    out = await decline({"out_of_scope_reason": "subject_suggestion",
                         "subject_suggestion": {"kind": "choose", "question": "q", "choices": [_choice(9)]}})
    assert "9 active employees" in out["final_answer"]
```

`EmployeeMatch` gains `first_name` and `last_name` in this task (Step 3), as its last two fields. The stub above passes them positionally.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_turn_people.py -v`
Expected: FAIL, with `ImportError: cannot import name 'decide'`.

- [ ] **Step 3: Carry first and last names on matches**

In `records.py`, add `first_name: str | None = None` and `last_name: str | None = None` as the last fields of `EmployeeMatch`. In `pbms/provider.py`'s `match_from_row`, pass `first_name=_text(row.get("First_Name")), last_name=_text(row.get("Last_Name")),`. Extend Task 3's `test_a_match_carries_what_the_choice_card_shows` to also assert `(m.first_name, m.last_name) == ("QW_XZ", "PL_MR")`.

- [ ] **Step 4: Add `decide` to `people.py`**

```python
import re
from dataclasses import dataclass, field

# A singular personal pronoun: after a turn about several people, it is asked about.
_SINGULAR = re.compile(r"\b(he|she|him|her|his|hers)\b", re.IGNORECASE)


@dataclass
class TurnPeople:
    people: list[str] = field(default_factory=list)                # refs this turn is about
    unfamiliar: list[tuple[str, str]] = field(default_factory=list)  # names to offer a choice for
    which_of: list[str] = field(default_factory=list)              # refs to ask "which of them?"


def decide(question: str, subjects: list[dict[str, Any]], last_people: list[str],
           directory_names: list[tuple[str, str]]) -> TurnPeople:
    """Spec §4.2. Familiar names first; an unfamiliar name is a choice; no name
    continues with the previous turn's people; a singular pronoun after a turn
    about several is asked about; otherwise the most recent person, or nobody."""
    from pension_chat.pbms.names import NameDirectory

    roster_refs = [s["ref"] for s in subjects]
    by_name: dict[str, list[str]] = {}
    for s in subjects:
        by_name.setdefault(norm_name(f"{s.get('first_name') or ''} {s.get('last_name') or ''}"), []).append(s["ref"])

    roster_dir = NameDirectory((s.get("first_name") or "", s.get("last_name") or "") for s in subjects)
    familiar: list[str] = []
    for first, last in roster_dir.names_in(question):
        refs = by_name.get(norm_name(f"{first} {last}"), [])
        if len(refs) > 1:
            return TurnPeople(which_of=refs)
        familiar += [r for r in refs if r not in familiar]
    known = set(by_name)
    unfamiliar = [(f, la) for f, la in directory_names if norm_name(f"{f} {la}") not in known]

    if unfamiliar:
        return TurnPeople(people=familiar[:MAX_PEOPLE], unfamiliar=unfamiliar[:MAX_PEOPLE])
    if familiar:
        return TurnPeople(people=familiar[:MAX_PEOPLE])
    continuing = [r for r in last_people if r in roster_refs]
    if continuing:
        if len(continuing) > 1 and _SINGULAR.search(question):
            return TurnPeople(which_of=continuing)
        return TurnPeople(people=continuing[:MAX_PEOPLE])
    return TurnPeople(people=roster_refs[:1])
```

`pbms.names` imports nothing from the agent, so the local import creates no cycle. It is local only so `people.py` can be imported in environments that never touch PBMS.

- [ ] **Step 5: Wire it into `triage`**

In `nodes.py`, extend the records import with `NameSearch`. Add this above `triage`:

```python
_MAX_CANDIDATES = 200


def _candidate(m: EmployeeMatch) -> dict[str, Any]:
    return {"ref": m.key, "display_name": m.display_name, "first_name": m.first_name, "last_name": m.last_name,
            "employer": m.employer, "department": m.department, "status": m.status, "birth_year": m.birth_year}


async def _suggestion(provider: Any, decided: people_mod.TurnPeople, subjects: list[dict],
                      question: str) -> dict[str, Any] | None:
    """The choice a turn puts to the user instead of answering (spec §4.2), or None."""
    if decided.which_of:
        roster = {s["ref"]: s for s in subjects}
        return {"kind": "which_one", "question": question,
                "choices": [{"first_name": None, "last_name": None, "unavailable": False,
                             "candidates": [roster[r] for r in decided.which_of if r in roster]}]}
    if not decided.unfamiliar:
        return None

    async def choices_for(first: str, last: str) -> dict[str, Any]:
        choice: dict[str, Any] = {"first_name": first, "last_name": last, "unavailable": False, "candidates": []}
        try:
            matches = await provider.find_by_name(first, last)
        except RecordUnavailable:
            choice["unavailable"] = True
            return choice
        choice["candidates"] = [_candidate(m) for m in matches[:_MAX_CANDIDATES]]
        return choice

    choices = await asyncio.gather(*(choices_for(f, la) for f, la in decided.unfamiliar))
    return {"kind": "choose", "question": question, "choices": list(choices)}
```

In `triage`, just before its final `return`, add:

```python
    # Who this turn is about (spec §4.2) -- only where there are people to be
    # about: a conversation with people in it, or a record source that can find
    # them by name. Everywhere else this turn is exactly as it was.
    subjects = list(state.get("subjects") or [])
    provider = current_provider()
    name_search = provider if isinstance(provider, NameSearch) else None
    if subjects or name_search is not None:
        decided = people_mod.decide(
            question, subjects, list(state.get("last_people") or []),
            name_search.names_in(question) if name_search is not None else [],
        )
        suggestion = await _suggestion(name_search, decided, subjects, question)
        if suggestion is not None:
            return {
                "in_scope": False,
                "out_of_scope_reason": "subject_suggestion",
                "subject_suggestion": suggestion,
                "turn_people": decided.people,
                "timings": {"triage": time.perf_counter() - started},
            }
        return {
            "in_scope": scope.in_scope,
            "out_of_scope_reason": scope.reason,
            "override_signal": guardrails.detect_instruction_override(question).to_dict(),
            "explicit_sections": extract_section_references(question),
            "explicit_articles": extract_article_references(question),
            "turn_people": decided.people,
            "timings": {"triage": time.perf_counter() - started},
        }
```

The existing final `return` stays as it is, for turns with no people.

A `which_one` turn needs no name search. `_suggestion` returns before it touches `provider`, so a roster with the fixtures provider works.

- [ ] **Step 6: The replies**

In `graph.py`, add `from pension_chat.agent.people import MAX_LISTED`. Add this above `decline`:

```python
def _choice_sentence(choice: dict[str, Any]) -> str:
    name = f"{choice.get('first_name') or ''} {choice.get('last_name') or ''}".strip()
    if choice.get("unavailable"):
        return f"I couldn't look up {name} just now: the fund's records are not answering."
    candidates = choice.get("candidates") or []
    if not candidates:
        return f"I couldn't find an active employee named {name}."
    if len(candidates) == 1:
        one = candidates[0]
        about = ", ".join(v for v in (one.get("department") or one.get("employer"),
                                      f"born {one['birth_year']}" if one.get("birth_year") else None) if v)
        return f"Did you mean {one.get('display_name') or name}{f' ({about})' if about else ''}?"
    if len(candidates) <= MAX_LISTED:
        return f"There are {len(candidates)} active employees named {name}; which one do you mean?"
    return f"There are {len(candidates)} active employees named {name}; which department do they work in?"


def subject_suggestion_message(suggestion: dict[str, Any]) -> str:
    """The reply to a choice turn -- people named by name, department and birth year,
    never by the internal key the cards carry as `ref`."""
    choices = suggestion.get("choices") or []
    if suggestion.get("kind") == "which_one":
        names = [c.get("display_name") or "this employee" for c in (choices[0].get("candidates") if choices else [])]
        listed = ", ".join(names[:-1]) + f" or {names[-1]}" if len(names) > 1 else "".join(names)
        return f"Which of them do you mean: {listed}? Choose below."
    text = " ".join(_choice_sentence(c) for c in choices)
    if any(c.get("candidates") for c in choices):
        if len(choices) == 1 and len(choices[0]["candidates"]) == 1:
            text += " Choose below and I'll answer your question about them."
        else:
            text += " Once you've chosen below, I'll answer your question."
    return text
```

This gives two exact strings, and the test's `startswith` expects the first: a single choice with one candidate ends "Did you mean LI_MO GA_RD (Finance, born 1964)? Choose below and I'll answer your question about them.", and several cards end with "Once you've chosen below, I'll answer your question."

In `decline`, replace the `answer = …` line with:

```python
    if reason == "subject_suggestion":
        answer = subject_suggestion_message(state.get("subject_suggestion") or {})
    else:
        answer = GREETING_MESSAGE if reason == "greeting" else OUT_OF_SCOPE_MESSAGE
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_turn_people.py tests/test_pbms_provider.py -v`, then `uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/pension_chat/agent/people.py src/pension_chat/agent/nodes.py src/pension_chat/agent/graph.py src/pension_chat/records.py src/pension_chat/pbms/provider.py tests/test_turn_people.py tests/test_pbms_provider.py
git commit -m "Agent: who a turn is about -- familiar names, a choice for new ones, which-one for an ambiguous pronoun

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Several people in one loop: per-person calculation, the comparison table, verification, cards

**Files:**
- Modify: `src/pension_chat/agent/tools.py:160-230` (the `person` parameter) and `:822-873` (`_compute`)
- Modify: `src/pension_chat/agent/loop.py` (`_context`: the people block; `_views` and `MARKED_VIEWS`: `PEOPLE`)
- Modify: `src/pension_chat/agent/graph.py` (the verifier's `facts`; `calculation_cards`; the `done` event's `cards`)
- Modify: `src/pension_chat/agent/card.py` (`calculation_cards`)
- Modify: `src/pension_chat/agent/graph.py::turn_record` (scenario labels per person)
- Modify: `src/pension_chat/evals/trace.py:93-103` (`person` on figures), `src/pension_chat/api/sessions.py` (cards into `tool_calls`)
- Test: `tests/test_people_calculation.py`

**Interfaces:**
- Consumes: `state["people"]`, `turn_people` and `labels` (Task 5).
- Produces:
  - `compute_benefit`'s `person: str` argument, required when `len(people) > 1`;
  - result entries carrying `person`, `person_ref` and `input_sources`, with keys prefixed `"{label} · "`;
  - `loop.render_people(state) -> str` and the view slot `"PEOPLE"`;
  - `graph._people_facts(state) -> dict`;
  - `card.calculation_cards(state, sheet) -> list[dict]`, each card with `"person"` when the turn had several;
  - the `done` event's `"cards": list[dict]`, with `"card"` kept as `cards[0]` for compatibility;
  - trace figures carrying `"person"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_people_calculation.py
"""Several people, one loop: each figure from its own record; numbers only in a code-rendered table."""
from __future__ import annotations

from pension_chat.agent import tools
from pension_chat.agent.card import calculation_cards
from pension_chat.agent.graph import _people_facts
from pension_chat.agent.loop import render_people
from pension_chat.agent.worksheet import WorkSheet


def _bundle(ref, label, salary, tier=2, dept="Finance"):
    return {"label": label,
            "record": {"id": ref, "article_number": 8, "tier": tier, "department": dept, "record_source": "PBMS"},
            "salary_history": [], "member_facts": {"final_average_salary": salary, "age": 62}}


PEOPLE = {"M-1": _bundle("M-1", "KA_LS MA_LP", 90000), "M-2": _bundle("M-2", "QW_XZ PL_MR", 60000, dept="Water")}


async def test_each_call_computes_from_that_persons_record_only(monkeypatch) -> None:
    seen = []

    async def fake_node(slice_):
        seen.append((slice_["employee_record"]["id"], slice_["member_facts"]["final_average_salary"]))
        return {}

    async def fake_execute(slice_):
        salary = slice_["member_facts"]["final_average_salary"]
        return {"calculation_result": {"retirement_annuity": {"base": {"ok": True, "annual_amount": salary * 0.3}}},
                "input_sources": {"final_average_salary": "record"}}

    for name in ("resolve_recipes", "resolve_inputs", "validate_recipes"):
        monkeypatch.setattr(tools, name, fake_node)
    monkeypatch.setattr(tools, "execute_calculations", fake_execute)

    state = {"people": PEOPLE, "turn_people": ["M-1", "M-2"], "calculation_result": {}}
    _, first = await tools._compute({"benefit": "retirement_annuity", "person": "ka_ls ma_lp"}, state)
    state["calculation_result"] = first["calculation_result"]
    _, second = await tools._compute({"benefit": "retirement_annuity", "person": "QW_XZ PL_MR"}, state)

    assert [s for s in seen if s[0] == "M-1"][0][1] == 90000
    assert [s for s in seen if s[0] == "M-2"][0][1] == 60000
    result = second["calculation_result"]
    assert set(result) == {"KA_LS MA_LP · retirement_annuity", "QW_XZ PL_MR · retirement_annuity"}
    assert result["KA_LS MA_LP · retirement_annuity"]["base"]["annual_amount"] == 27000
    assert result["QW_XZ PL_MR · retirement_annuity"]["person_ref"] == "M-2"


async def test_a_missing_or_unknown_person_is_refused_with_the_labels() -> None:
    state = {"people": PEOPLE, "turn_people": ["M-1", "M-2"]}
    text, update = await tools._compute({"benefit": "retirement_annuity"}, state)
    assert update == {} and "KA_LS MA_LP" in text and "QW_XZ PL_MR" in text
    text, update = await tools._compute({"benefit": "retirement_annuity", "person": "Nobody"}, state)
    assert update == {}


def _results():
    return {
        "KA_LS MA_LP · retirement_annuity": {"person": "KA_LS MA_LP", "person_ref": "M-1", "section": "8-138",
                                            "subsection": "(g-1)", "input_sources": {},
                                            "base": {"ok": True, "annual_amount": "27000.00", "monthly_amount": "2250.00",
                                                     "inputs_used": {}}},
        "QW_XZ PL_MR · retirement_annuity": {"person": "QW_XZ PL_MR", "person_ref": "M-2", "section": "8-138",
                                            "subsection": "(g-1)", "input_sources": {},
                                            "base": {"ok": True, "annual_amount": "18000.00", "monthly_amount": "1500.00",
                                                     "inputs_used": {}}},
    }


def test_the_people_table_is_one_row_per_person_in_turn_order() -> None:
    table = render_people({"people": PEOPLE, "turn_people": ["M-2", "M-1"], "calculation_result": _results()})
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line][1:]
    assert rows[0].startswith("| QW_XZ PL_MR | Water | 2 |") and "$18,000.00" in rows[0]
    assert rows[1].startswith("| KA_LS MA_LP | Finance | 2 |") and "$27,000.00" in rows[1]
    assert "M-1" not in table


def test_one_person_has_no_people_table() -> None:
    one = {k: v for k, v in _results().items() if v["person_ref"] == "M-1"}
    assert render_people({"people": PEOPLE, "turn_people": ["M-1"], "calculation_result": one}) == ""


def test_every_persons_facts_reach_the_verifier_without_colliding() -> None:
    facts = _people_facts({"people": PEOPLE})
    assert facts["M-1:final_average_salary"] == 90000 and facts["M-2:final_average_salary"] == 60000
    assert _people_facts({"people": {"M-1": PEOPLE["M-1"]}}) == {}


def test_one_card_per_person() -> None:
    state = {"people": PEOPLE, "turn_people": ["M-1", "M-2"], "calculation_result": _results(),
             "employee_record": PEOPLE["M-1"]["record"]}
    cards = calculation_cards(state, WorkSheet.from_state(state))
    assert [c["person"] for c in cards] == ["KA_LS MA_LP", "QW_XZ PL_MR"]
    assert [c["annual_amount"] for c in cards] == ["27000.00", "18000.00"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_people_calculation.py -v`
Expected: FAIL, with `ImportError: cannot import name 'calculation_cards'`.

- [ ] **Step 3: `compute_benefit` takes a person**

In `tools.py`, add this to `compute_benefit`'s `properties` (line ~176):

```python
                    "person": {
                        "type": "string",
                        "description": (
                            "Whose figure, by the label the context lists. Required when "
                            "the question is about more than one employee -- call once "
                            "per person; omit when it is about one."
                        ),
                    },
```

Add `from pension_chat.agent.people import norm_name` to the imports. In `_compute`, directly after the benefit check, insert:

```python
    # Several people in this turn: whose figure, and only their record (spec §5).
    # The slice below carries this person's record, history and facts and no one
    # else's, so another person's salary cannot reach this figure.
    people = state.get("people") or {}
    person_ref: str | None = None
    if len(people) > 1:
        wanted = norm_name(str(arguments.get("person") or ""))
        person_ref = next((ref for ref, b in people.items() if norm_name(b["label"]) == wanted), None)
        if person_ref is None:
            return ("This question is about more than one employee. Call compute_benefit once for each, "
                    "with person set to one of: " + "; ".join(b["label"] for b in people.values()) + ".", {})
```

After `slice_` is built, insert:

```python
    if person_ref is not None:
        mine = people[person_ref]
        slice_.update(employee_record=mine["record"], salary_history=mine["salary_history"],
                      member_facts=mine["member_facts"])
```

Replace the lines from `stale = _supersede(...)` to `update["stale_figures"] = stale` with:

```python
    if person_ref is None:
        stale = _supersede(state, update, slice_["scenarios"])
    else:
        # A comparison's figures are fresh side-by-side figures; superseding is
        # tracked for one person's figure across turns, not between people.
        stale = []
        label = people[person_ref]["label"]
        mine_now = {
            f"{label} · {key}": {**entry, "person": label, "person_ref": person_ref,
                                  "input_sources": dict(update.get("input_sources") or {})}
            for key, entry in (update.get("calculation_result") or {}).items()
        }
        others = {key: entry for key, entry in (state.get("calculation_result") or {}).items()
                  if (entry or {}).get("person_ref") not in (None, person_ref)}
        update["calculation_result"] = {**others, **mine_now}
    update["stale_figures"] = stale
```

`_describe(...)` below still reads `update.get("calculation_result")`. For a person call it now describes both people's results so far, which is what the model should see.

- [ ] **Step 4: The people context block and the `PEOPLE` view**

In `loop.py`, after the `_record_notes` line added in Task 5, add:

```python
    if len(state.get("people") or {}) > 1:
        lines.append(_people_context(state))
```

Add these functions:

```python
def _people_context(state: dict[str, Any]) -> str:
    """Several people in one turn: who each is, and how to compute for each (spec §5)."""
    people = state.get("people") or {}
    lines = [
        f"This question is about {len(people)} employees. Call compute_benefit once for each, with "
        "person set to the label shown. Their figures are put side by side for you at <<<PEOPLE>>>; "
        "do not write the figures yourself."
    ]
    for ref in state.get("turn_people") or list(people):
        bundle = people.get(ref)
        if not bundle:
            continue
        record, facts = bundle["record"], bundle.get("member_facts") or {}
        known = ", ".join(f"{k}={facts[k]}" for k in ("age", "service_years") if facts.get(k) is not None)
        line = (f"- {bundle['label']}: {record.get('department') or record.get('employer') or 'department not on file'}; "
                f"Tier {record.get('tier') or 'not settled'}; {known or 'no age or service on file'}")
        if record.get("missing"):
            line += "; record gaps -- " + "; ".join(f"{k}: {v}" for k, v in sorted(record["missing"].items()))
        lines.append(line)
    return "\n".join(lines)


def _money(value: Any) -> str:
    if value in (None, ""):
        return "—"
    return f"${Decimal(str(value).replace(',', '').replace('$', '')):,.2f}"


def render_people(state: dict[str, Any]) -> str:
    """One row per person with a figure, in the turn's order -- code-rendered so the
    model can compare in prose but can never swap two people's numbers."""
    people = state.get("people") or {}
    first_figure: dict[str, dict] = {}
    for entry in (state.get("calculation_result") or {}).values():
        ref = (entry or {}).get("person_ref")
        if ref and ref not in first_figure and ((entry.get("base") or {}).get("ok")):
            first_figure[ref] = entry
    if len(first_figure) < 2:
        return ""
    rows = ["| Employee | Department | Tier | Worked from | Annual | Monthly |",
            "|---|---|---|---|---|---|"]
    for ref in state.get("turn_people") or list(people):
        entry, bundle = first_figure.get(ref), people.get(ref)
        if not entry or not bundle:
            continue
        record, base = bundle["record"], entry["base"]
        section = f"§{entry['section']}{entry.get('subsection') or ''}" if entry.get("section") else ""
        rows.append(f"| {bundle['label']} | {record.get('department') or record.get('employer') or ''} | "
                    f"{record.get('tier') or ''} | {section} | {_money(base.get('annual_amount'))} | "
                    f"{_money(base.get('monthly_amount'))} |")
    return "\n".join(rows)
```

Make sure `from decimal import Decimal` is imported in `loop.py`. Add `"PEOPLE"` to `MARKED_VIEWS`, and add `"PEOPLE": render_people(state),` to the `rendered` dict in `_views`.

- [ ] **Step 5: Verification sees every person's facts; cards per person; scenario labels**

In `graph.py`, add:

```python
def _people_facts(state: Mapping[str, Any]) -> dict[str, Any]:
    """Every person's facts for the verifier, keyed by ref so two people's values
    never collide (spec §5). Which number belongs to whom is the PEOPLE table's job."""
    people = state.get("people") or {}
    if len(people) < 2:
        return {}
    return {f"{ref}:{name}": value for ref, bundle in people.items()
            for name, value in (bundle.get("member_facts") or {}).items()}
```

In the `verify_answer(...)` call (line ~893), change `facts=_available_facts(state),` to `facts={**_available_facts(state), **_people_facts(state)},`. First confirm that `guardrails.verify_answer` reads `facts` by value only (`grep -n "facts" src/pension_chat/agent/guardrails.py`). If it reads specific keys (for example `facts.get("age")`), keep `facts=_available_facts(state)` and pass the people facts to the numeric-provenance check through the same value collection it builds, and record a ruling.

In `card.py`, add:

```python
def calculation_cards(state: Mapping[str, Any], sheet: WorkSheet) -> list[dict[str, Any]]:
    """One card per person with a figure; a one-person turn's card exactly as before."""
    people = state.get("people") or {}
    if len(people) < 2:
        card = calculation_card(state, sheet)
        return [card] if card else []
    cards: list[dict[str, Any]] = []
    done: set[str] = set()
    for entry in (state.get("calculation_result") or {}).values():
        ref = (entry or {}).get("person_ref")
        if not ref or ref in done or ref not in people or not (entry.get("base") or {}).get("ok"):
            continue
        done.add(ref)
        card = calculation_card(
            {**state, "calculation_result": {"figure": entry}, "input_sources": entry.get("input_sources") or {},
             "employee_record": people[ref]["record"]},
            sheet,
        )
        if card:
            cards.append({**card, "person": entry.get("person")})
    order = {r: i for i, r in enumerate(state.get("turn_people") or [])}
    people_order = {people[r]["label"]: order.get(r, 99) for r in people}
    return sorted(cards, key=lambda c: people_order.get(c["person"], 99))
```

In `run_agent_stream`, replace `card = calculation_card(...)` with `cards = calculation_cards(...)`, using the same arguments. Initialise `cards: list = []` where `card = None` was. The final `done` becomes `yield {"type": "done", "state": state, "card": cards[0] if cards else None, "cards": cards, "scenarios": scenarios}`.

In `sessions.py`'s `event_stream`:
- `card = event.get("card")` becomes `cards = event.get("cards") or ([event["card"]] if event.get("card") else [])`;
- initialise `cards: list = []`;
- the audit line becomes `tool_calls = _audit_trail(final_state) + cards` (Task 8 appends `_subject_trail`).

In `graph.py::turn_record`, find where a result entry's scenarios are written into `scenario_set` by their `label`. There, when the entry carries `person`, file the scenario under `f"{entry['person']} — {label}"` instead of `label`. That way two people's "at age 62" never overwrite each other in the chip row. Add this test to `tests/test_people_calculation.py`:

```python
def test_two_peoples_scenarios_do_not_overwrite_each_other() -> None:
    from pension_chat.agent.graph import turn_record

    results = _results()
    for entry in results.values():
        entry["scenarios"] = [{"label": "at age 62", "annual_amount": entry["base"]["annual_amount"]}]
    update = turn_record({"final_answer": "a", "turn_index": 1, "calculation_result": results,
                          "guardrail_passed": True})
    labels = set((update or {}).get("scenario_set") or {})
    assert {"KA_LS MA_LP — at age 62", "QW_XZ PL_MR — at age 62"} <= labels
```

If `turn_record` builds `scenario_set` from something other than `calculation_result[*].scenarios`, adapt the test's input to what it reads. Keep the assertion, and record the adaptation in the ledger.

In `evals/trace.py::_figures`, add `"person": (entry or {}).get("person")` to both dicts it appends.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_people_calculation.py -v`, then `uv run pytest -q`
Expected: all pass. Existing card tests are unaffected, since `calculation_card` is unchanged and `calculation_cards` wraps it.

- [ ] **Step 7: Commit**

```bash
git add src/pension_chat/agent/tools.py src/pension_chat/agent/loop.py src/pension_chat/agent/graph.py src/pension_chat/agent/card.py src/pension_chat/evals/trace.py src/pension_chat/api/sessions.py tests/test_people_calculation.py
git commit -m "Agent: several people in one loop -- each figure from its own record, a code-rendered comparison, a card each

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The session's people: migration, API, live event, recency

**Files:**
- Create: `alembic/versions/009_session_subjects.py`
- Modify: `src/pension_chat/db/models.py:228-270` (`Session.subjects`)
- Create: `src/pension_chat/api/subject.py`
- Modify: `src/pension_chat/api/sessions.py`:
  - pass `subjects`;
  - relay `people`;
  - `_subject_trail`;
  - recency reorder.
- Modify: `src/pension_chat/sse/__init__.py` (`sse_people`), `src/pension_chat/agent/graph.py` (`people_of`), `src/pension_chat/main.py` (register the router)
- Test: `tests/test_subject_api.py`

**Interfaces:**
- Consumes: `normalise_meab_id`, `MEAB_ID_PATTERN`, `current_provider`, `RecordUnavailable` and `EmployeeMatch` (Tasks 3 and 6); `people.MAX_PEOPLE`; `_authorised_session` and `_thread_owner` from `sessions.py`; the `people` stream event (Task 5).
- Produces:
  - `api.subject.roster_entry(m) -> dict`, `add_to_roster(roster, entry) -> list`, `remove_from_roster(roster, ref) -> list`, `by_recency(roster, used) -> list` and `resolve_ref(provider, ref) -> EmployeeMatch`;
  - the routes `GET /api/v1/sessions/{id}/subjects` (returns `{"people", "active"}`), `POST` on the same path (body `{"ref"}`, returns `{"people"}`), `DELETE /api/v1/sessions/{id}/subjects/{ref}` and `DELETE /api/v1/sessions/{id}/subjects`;
  - `sse_people(active: list[str]) -> str`;
  - `graph.people_of(session_id, *, employee_id, session_token) -> list[str]`;
  - `sessions._subject_trail(state) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_subject_api.py
"""The conversation's people: choose, remove, recency, and the audit -- at the helpers the routes wrap."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from pension_chat.api.sessions import _subject_trail
from pension_chat.api.subject import add_to_roster, by_recency, remove_from_roster, resolve_ref, roster_entry
from pension_chat.records import MEAB_ID_PATTERN, EmployeeMatch, RecordUnavailable
from pension_chat.sse import sse_people


def _e(ref):
    return {"ref": ref, "display_name": ref, "first_name": "F", "last_name": ref, "department": None,
            "employer": None, "birth_year": None}


class _Stub:
    def __init__(self, matches=(), fail=False):
        self.matches, self.fail, self.asked = list(matches), fail, []

    async def find_employees(self, query, scope=None):
        self.asked.append(query)
        if self.fail:
            raise RecordUnavailable("down")
        return self.matches


A = EmployeeMatch("M-000123", "KA_LS MA_LP", "City of Chicago", "active", "Finance", 1964, "KA_LS", "MA_LP")


async def test_a_ref_is_re_resolved_before_it_is_stored() -> None:
    stub = _Stub([A])
    match = await resolve_ref(stub, " m-000123 ")
    assert stub.asked == ["M-000123"] and roster_entry(match) == {
        "ref": "M-000123", "display_name": "KA_LS MA_LP", "first_name": "KA_LS", "last_name": "MA_LP",
        "department": "Finance", "employer": "City of Chicago", "birth_year": 1964}


@pytest.mark.parametrize("ref, matches, status", [("KA_LS MA_LP", [A], 400), ("M-12", [A], 400), ("M-000999", [], 404)])
async def test_bad_or_unknown_refs(ref, matches, status) -> None:
    with pytest.raises(HTTPException) as caught:
        await resolve_ref(_Stub(matches), ref)
    assert caught.value.status_code == status


async def test_unavailable_is_503_and_no_provider_is_404() -> None:
    with pytest.raises(HTTPException) as down:
        await resolve_ref(_Stub(fail=True), "M-000123")
    with pytest.raises(HTTPException) as off:
        await resolve_ref(None, "M-000123")
    assert (down.value.status_code, off.value.status_code) == (503, 404)


def test_choosing_puts_the_person_first_and_a_fifth_pushes_out_the_least_recent() -> None:
    roster = [_e("M-4"), _e("M-3"), _e("M-2"), _e("M-1")]
    assert [e["ref"] for e in add_to_roster(roster, _e("M-5"))] == ["M-5", "M-4", "M-3", "M-2"]
    assert [e["ref"] for e in add_to_roster(roster, _e("M-2"))] == ["M-2", "M-4", "M-3", "M-1"]


def test_removing_and_recency() -> None:
    roster = [_e("M-1"), _e("M-2"), _e("M-3")]
    assert [e["ref"] for e in remove_from_roster(roster, "M-2")] == ["M-1", "M-3"]
    assert [e["ref"] for e in by_recency(roster, ["M-3", "M-2"])] == ["M-3", "M-2", "M-1"]
    assert by_recency(roster, []) == roster


def test_the_audit_trail_names_the_people_and_any_choice() -> None:
    suggestion = {"kind": "choose", "question": "q", "choices": []}
    state = {"turn_people": ["M-1", "M-2"], "subject_suggestion": suggestion}
    assert _subject_trail(state) == [{"type": "subjects", "refs": ["M-1", "M-2"]},
                                     {"type": "subject_suggestion", **suggestion}]
    assert _subject_trail({}) == []


def test_the_people_event() -> None:
    assert sse_people(["M-1"]) == 'event: people\ndata: {"active": ["M-1"]}\n\n'


def test_the_migration() -> None:
    text = Path("alembic/versions/009_session_subjects.py").read_text(encoding="utf-8")
    assert "subjects" in text and 'down_revision = "008_answer_evidence"' in text
    assert "jsonb_array_length(subjects) <= 4" in text
    assert re.fullmatch(MEAB_ID_PATTERN, "M-000123") and not re.fullmatch(MEAB_ID_PATTERN, "m-000123")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_subject_api.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.api.subject'`.

- [ ] **Step 3: Write the migration and update the model**

```python
# alembic/versions/009_session_subjects.py
"""A conversation's people: the employees an employer is asking about (at most 4).

Most recent first: [{"ref", "display_name", "first_name", "last_name",
"department", "employer", "birth_year"}]. `ref` is the internal MEAB ID --
validated by code against records.MEAB_ID_PATTERN before storing, never shown.
Names are kept so a returning name is recognised without a query; the transcript
already holds them, under the same row-level policies, which cover this column.
Not the principal: `sessions.employee_id` is who is chatting and scopes RLS.

Revision ID: 009_session_subjects
Revises: 008_answer_evidence
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "009_session_subjects"
down_revision = "008_answer_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("subjects", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
    )
    op.create_check_constraint(
        "ck_sessions_subjects_at_most_four", "sessions",
        "jsonb_typeof(subjects) = 'array' AND jsonb_array_length(subjects) <= 4",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sessions_subjects_at_most_four", "sessions", type_="check")
    op.drop_column("sessions", "subjects")
```

In `models.py`, add to `Session` (and import `JSONB` from `sqlalchemy.dialects.postgresql` if it is not already imported):

```python
    # The conversation's people (migration 009): most recent first, at most 4,
    # each keyed by an internal MEAB ID never shown. Not the principal.
    subjects: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
```

Add `CheckConstraint("jsonb_typeof(subjects) = 'array' AND jsonb_array_length(subjects) <= 4", name="ck_sessions_subjects_at_most_four"),` to its `__table_args__`.

- [ ] **Step 4: Write `api/subject.py`**

```python
# src/pension_chat/api/subject.py
"""The conversation's people: chosen from a name's candidates, removed, restored.

A choice card sends back the opaque `ref` it was given; the server re-resolves
it in PBMS (active members only) before storing it. `ref` is the internal MEAB
ID: never shown, carried only so the UI can say which card or chip it means.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import update

from pension_chat.agent.people import MAX_PEOPLE
from pension_chat.api.auth import TokenData, get_current_user
from pension_chat.db.models import Session
from pension_chat.db.session import get_db_session
from pension_chat.records import EmployeeMatch, RecordProvider, RecordUnavailable, current_provider, normalise_meab_id

router = APIRouter(prefix="/api/v1", tags=["subjects"])


class AddPerson(BaseModel):
    ref: str


def roster_entry(match: EmployeeMatch) -> dict[str, Any]:
    return {"ref": match.key, "display_name": match.display_name, "first_name": match.first_name,
            "last_name": match.last_name, "department": match.department, "employer": match.employer,
            "birth_year": match.birth_year}


def add_to_roster(roster: list[dict], entry: dict) -> list[dict]:
    return ([entry] + [e for e in roster if e["ref"] != entry["ref"]])[:MAX_PEOPLE]


def remove_from_roster(roster: list[dict], ref: str) -> list[dict]:
    return [e for e in roster if e["ref"] != ref]


def by_recency(roster: list[dict], used: list[str]) -> list[dict]:
    """The people a turn was about move to the front, in the turn's order."""
    front = [e for ref in used for e in roster if e["ref"] == ref]
    return front + [e for e in roster if e["ref"] not in used]


async def resolve_ref(provider: RecordProvider | None, ref: str) -> EmployeeMatch:
    if provider is None:
        raise HTTPException(status_code=404, detail="Employee records are not enabled")
    key = normalise_meab_id(ref)
    if key is None:
        raise HTTPException(status_code=400, detail="Not an employee reference")
    try:
        matches = await provider.find_employees(key)
    except RecordUnavailable:
        raise HTTPException(status_code=503, detail="The fund's records are not answering just now") from None
    if not matches:
        raise HTTPException(status_code=404, detail="No active employee with that reference")
    return matches[0]


async def store_roster(session: Session, roster: list[dict]) -> None:
    from pension_chat.api.sessions import _thread_owner

    async with get_db_session(**_thread_owner(session)) as db:
        await db.execute(update(Session).where(Session.id == session.id).values(subjects=roster))
        await db.commit()


@router.get("/sessions/{session_id}/subjects")
async def get_people(session_id: str, request: Request,
                     user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    from pension_chat.agent.graph import people_of
    from pension_chat.api.sessions import _authorised_session, _thread_owner

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    roster = list(session.subjects or [])
    active = await people_of(session_id, **_thread_owner(session))
    return {"people": roster, "active": [r for r in active if any(e["ref"] == r for e in roster)]}


@router.post("/sessions/{session_id}/subjects")
async def add_person(session_id: str, body: AddPerson, request: Request,
                     user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    from pension_chat.api.sessions import _authorised_session

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    match = await resolve_ref(current_provider(), body.ref)
    roster = add_to_roster(list(session.subjects or []), roster_entry(match))
    await store_roster(session, roster)
    return {"people": roster}


@router.delete("/sessions/{session_id}/subjects/{ref}")
async def remove_person(session_id: str, ref: str, request: Request,
                        user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    from pension_chat.api.sessions import _authorised_session

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    roster = remove_from_roster(list(session.subjects or []), ref)
    await store_roster(session, roster)
    return {"people": roster}


@router.delete("/sessions/{session_id}/subjects")
async def clear_people(session_id: str, request: Request,
                       user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    from pension_chat.api.sessions import _authorised_session

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    await store_roster(session, [])
    return {"people": []}
```

In `main.py`, add `from pension_chat.api.subject import router as subject_router  # noqa: E402` and `app.include_router(subject_router)`.

In `graph.py`, add this next to `scenarios_of`:

```python
async def people_of(
    session_id: str, *, employee_id: str | None = None, session_token: str | None = None
) -> list[str]:
    """Who the last turn was about, read from the checkpoint -- so a reload restores the highlight."""
    config = thread_config(session_id, employee_id=employee_id, session_token=session_token)
    snapshot = await active_graph().aget_state(config)
    return list((snapshot.values or {}).get("last_people") or [])
```

In `sse/__init__.py`:

```python
def sse_people(active: list[str]) -> str:
    """Who this turn is about, as soon as it is decided -- before any answer text --
    so the header chips change first (spec §4.4)."""
    return sse_event("people", {"active": list(active)})
```

`sse_event` serialises with `json.dumps`, which gives exactly the string the test expects.

- [ ] **Step 5: Relay, record and reorder in `send_message`**

In `sessions.py`:
- import `sse_people`, and `from pension_chat.api.subject import by_recency, store_roster`;
- add `subjects=list(session.subjects or []),` to the `run_agent_stream(...)` call;
- in the event loop, add `elif kind == "people": yield sse_people(event["active"])`.

Add:

```python
def _subject_trail(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Who this turn was about, and any choice it offered -- for the audit and the choice cards."""
    trail: list[dict[str, Any]] = []
    if state.get("turn_people"):
        trail.append({"type": "subjects", "refs": list(state["turn_people"])})
    if suggestion := state.get("subject_suggestion"):
        trail.append({"type": "subject_suggestion", **suggestion})
    return trail
```

Change the audit line to `tool_calls = _audit_trail(final_state) + cards + _subject_trail(final_state)`. After the assistant message is persisted, add:

```python
        # The people this turn was about move to the front; one write, only
        # when the order changed (spec §4.4).
        roster = list(session.subjects or [])
        reordered = by_recency(roster, list(final_state.get("turn_people") or []))
        if reordered != roster:
            try:
                await store_roster(session, reordered)
            except Exception:
                logger.exception("Could not reorder the conversation's people")
```

- [ ] **Step 6: Run the tests and apply the migration**

Run: `uv run pytest tests/test_subject_api.py -v`, then `uv run pytest -q`
Expected: all pass.

Run: `uv run alembic upgrade head`
Expected: `Running upgrade 008_answer_evidence -> 009_session_subjects`. This touches the project's own Postgres, never PBMS. If the connection is refused, the Postgres firewall does not yet allow this network (49.43.225.208). Record that in the ledger and continue. Apply the migration once the rule exists, and before the demo check in Task 11.

- [ ] **Step 7: Lint, type-check and commit**

Run: `uv run ruff check . && uv run mypy .`

```bash
git add alembic/versions/009_session_subjects.py src/pension_chat/db/models.py src/pension_chat/api/subject.py src/pension_chat/api/sessions.py src/pension_chat/sse/__init__.py src/pension_chat/agent/graph.py src/pension_chat/main.py tests/test_subject_api.py
git commit -m "API: a conversation's people -- chosen by opaque ref, at most 4 by recency, relayed live, kept in the audit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The people chips, the choice cards, and one card per person

**Files:**
- Create: `frontend/src/lib/subject.ts`, `frontend/src/lib/subject.test.ts`
- Modify: `frontend/src/lib/api.ts` (after `forgetScenario`)
- Modify: `frontend/src/lib/card.ts`:
  - add `record_label` and `person` to `CalculationCardData`;
  - make `cardsFrom` return all cards;
  - add a `recordLabel` parameter to `sourceLabel`.
- Modify: every `sourceLabel(` and `cardFrom(` caller (`grep -rn "sourceLabel(\|cardFrom(" frontend/src`)
- Create: `frontend/src/components/chat/PeopleChips.tsx`, `frontend/src/components/chat/SubjectChoice.tsx`
- Modify: `frontend/src/components/chat/ChatHeader.tsx`, `ChatMessage.tsx`, `ChatInterface.tsx` and `CalculationCard.tsx` (the person title)

**Interfaces:**
- Consumes:
  - the Task 8 routes;
  - the roster entry shape `{ref, display_name, first_name, last_name, department, employer, birth_year}`;
  - the `people` SSE event `{active}`;
  - the `tool_calls` entry `{"type": "subject_suggestion", kind, question, choices: [{first_name, last_name, unavailable, candidates}]}`;
  - cards with `person` and `record_label`;
  - `/health`'s `record_source`.
- Produces:
  - `Person`, `Choice` and `Suggestion` (TypeScript);
  - `MAX_LISTED`, `chipLabel`, `candidateLabel`, `byDepartment`, `suggestionFrom`, `allChosen` and `whichOneQuestion`;
  - `fetchPeople`, `addPerson`, `removePerson`, `clearPeople` and `fetchRecordSource`.

Before writing components, read `frontend/AGENTS.md` and the `node_modules/next/dist/docs/` page on client components. Write `"use client"` leaf components in the style of `ScenarioChips.tsx`, using semantic token utilities only. **Never render `ref`.**

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/lib/subject.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  allChosen, byDepartment, candidateLabel, chipLabel, MAX_LISTED, suggestionFrom, whichOneQuestion,
} from "./subject.ts";

const p = (ref: string, department: string | null, name = "KA_LS MA_LP") => ({
  ref, display_name: name, first_name: null, last_name: null, employer: "City of Chicago", department, birth_year: 1964,
});

test("chip and candidate labels never include the ref", () => {
  assert.equal(chipLabel(p("M-1", "Finance")), "KA_LS MA_LP · Finance");
  assert.equal(chipLabel(p("M-1", null)), "KA_LS MA_LP · City of Chicago");
  assert.equal(candidateLabel(p("M-1", "Finance")), "Finance · City of Chicago · born 1964");
  assert.ok(!candidateLabel(p("M-1", "Finance")).includes("M-1"));
});

test("more than MAX_LISTED candidates group by department, largest first, no department last", () => {
  const many = [...Array(MAX_LISTED + 3)].map((_, i) => p(`M-${i}`, i % 3 === 0 ? "Streets" : i % 3 === 1 ? "Water" : null));
  assert.deepEqual(byDepartment(many).map((g) => [g.department, g.candidates.length]),
    [["Streets", 5], ["Water", 5], ["No department on file", 5]]);
});

test("a suggestion is read from tool_calls, and 'all chosen' counts only cards with candidates", () => {
  const s = { kind: "choose" as const, question: "q", choices: [
    { first_name: "A", last_name: "B", unavailable: false, candidates: [p("M-1", "F")] },
    { first_name: "C", last_name: "D", unavailable: false, candidates: [] },
  ] };
  assert.equal(suggestionFrom([{ type: "card" }, { type: "subject_suggestion", ...s }])?.question, "q");
  assert.equal(suggestionFrom(undefined), null);
  assert.equal(allChosen(s, {}), false);
  assert.equal(allChosen(s, { 0: "M-1" }), true);
});

test("a which-one choice re-asks the question about the chosen person by name", () => {
  assert.equal(whichOneQuestion("what would she get at 65?", p("M-2", "Water", "QW_XZ PL_MR")),
    "About QW_XZ PL_MR: what would she get at 65?");
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npm test`
Expected: FAIL, with `Cannot find module './subject.ts'`.

- [ ] **Step 3: Write `subject.ts`**

```ts
// frontend/src/lib/subject.ts
/** The conversation's people: found by name in the chat, chosen by the user (Phase 1). */

/** One person. `ref` is opaque: sent back to choose or remove, never shown. */
export interface Person {
  ref: string;
  display_name: string | null;
  first_name: string | null;
  last_name: string | null;
  department: string | null;
  employer: string | null;
  birth_year: number | null;
  status?: string | null;
}

export interface Choice {
  first_name: string | null;
  last_name: string | null;
  unavailable: boolean;
  candidates: Person[];
}

export interface Suggestion {
  kind: "choose" | "which_one";
  question: string;
  choices: Choice[];
}

/** More candidates than this and a choice starts with the department. */
export const MAX_LISTED = 12;
const NO_DEPARTMENT = "No department on file";

const joined = (parts: (string | null | undefined)[]) =>
  parts.filter((part): part is string => Boolean(part)).join(" · ");

export const chipLabel = (p: Person): string => joined([p.display_name, p.department ?? p.employer]);

export const candidateLabel = (p: Person): string =>
  joined([p.department, p.employer, p.birth_year ? `born ${p.birth_year}` : null]);

export function byDepartment(people: Person[]): { department: string; candidates: Person[] }[] {
  const groups = new Map<string, Person[]>();
  for (const person of people) {
    const key = person.department ?? NO_DEPARTMENT;
    groups.set(key, [...(groups.get(key) ?? []), person]);
  }
  return [...groups.entries()]
    .map(([department, candidates]) => ({ department, candidates }))
    .sort((a, b) =>
      b.candidates.length - a.candidates.length ||
      Number(a.department === NO_DEPARTMENT) - Number(b.department === NO_DEPARTMENT) ||
      a.department.localeCompare(b.department));
}

export function suggestionFrom(toolCalls: unknown): Suggestion | null {
  if (!Array.isArray(toolCalls)) return null;
  const hit = toolCalls.find(
    (t) => t && typeof t === "object" && (t as { type?: unknown }).type === "subject_suggestion",
  ) as (Suggestion & { type: string }) | undefined;
  if (!hit || !Array.isArray(hit.choices)) return null;
  const { type: _type, ...rest } = hit;
  return rest;
}

/** Every card that has someone to choose has had a choice: time to re-send the question. */
export const allChosen = (s: Suggestion, chosen: Record<number, string>): boolean =>
  s.choices.every((c, i) => c.candidates.length === 0 || Boolean(chosen[i]));

/** A which-one choice is answered by asking again about that person, by name. */
export const whichOneQuestion = (question: string, person: Person): string =>
  `About ${person.display_name ?? "them"}: ${question}`;
```

A re-sent which-one question names the person, so §4.2 treats it as a familiar name and answers about them alone.

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npm test`
Expected: all pass.

- [ ] **Step 5: API functions and cards**

Append to `api.ts`, and add `import type { Person } from "./subject";`:

```ts
export interface People { people: Person[]; active?: string[] }

export async function fetchPeople(sessionId: string): Promise<People> {
  const response = await fetchWithAuth(`/sessions/${sessionId}/subjects`);
  return response.ok ? response.json() : { people: [], active: [] };
}

/** Add a chosen candidate to the conversation; resolves to the updated list, or null on failure. */
export async function addPerson(sessionId: string, ref: string): Promise<People | null> {
  const response = await fetchWithAuth(`/sessions/${sessionId}/subjects`, {
    method: "POST",
    body: JSON.stringify({ ref }),
  });
  return response.ok ? response.json() : null;
}

export async function removePerson(sessionId: string, ref: string): Promise<People | null> {
  const response = await fetchWithAuth(`/sessions/${sessionId}/subjects/${encodeURIComponent(ref)}`, {
    method: "DELETE",
  });
  return response.ok ? response.json() : null;
}

export async function clearPeople(sessionId: string): Promise<boolean> {
  return (await fetchWithAuth(`/sessions/${sessionId}/subjects`, { method: "DELETE" })).ok;
}

/** "pbms" when employee records come from the fund; the chips show only then. */
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
- add `record_label?: string | null; person?: string | null;` to `CalculationCardData`;
- add `export function cardsFrom(toolCalls: unknown): CalculationCardData[]`, which returns every `type === "card"` entry. Keep `cardFrom` as `cardsFrom(toolCalls)[0] ?? null`;
- make `sourceLabel` take `(source, recordLabel?: string | null)` and return `recordLabel` when `source === "record" && recordLabel`.

Add this to `card.test.ts`:

```ts
test("every card in a message, and the fund's record named on record inputs", () => {
  const calls = [{ type: "card", person: "A" }, { type: "subjects" }, { type: "card", person: "B" }];
  assert.deepEqual(cardsFrom(calls).map((c) => c.person), ["A", "B"]);
  assert.equal(sourceLabel("record", "PBMS record"), "PBMS record");
  assert.equal(sourceLabel("record"), "Record");
});
```

Update the `sourceLabel(input.source)` calls to `sourceLabel(input.source, card.record_label)`. In `CalculationCard.tsx`, when `card.person` is set, show it as the card's first line, in the card's existing label style.

- [ ] **Step 6: The components and the wiring**

`PeopleChips.tsx`:
- Props: `{ people: Person[]; active: string[]; onRemove: (ref: string) => void; disabled?: boolean }`.
- With no people: muted text, "Name an employee in your question".
- Otherwise, one chip per person in the given order, labelled `chipLabel(p)`. A chip whose `ref` is in `active` uses the filled treatment (the same tokens `ScenarioChips` uses for a selected chip); the others are outlined.
- Each chip has an ✕ button with `aria-label={`Remove ${p.display_name}`}` that calls `onRemove(p.ref)`.
- The container is `flex flex-wrap gap-1`, so it wraps at 360 px.

`SubjectChoice.tsx`:
- Props: `{ choice: Choice; kind: "choose" | "which_one"; onChoose: (person: Person) => void; chosenRef?: string; disabled?: boolean }`.
- It renders nothing when `choice.unavailable` or `choice.candidates.length === 0`, because the message text says why.
- **More than `MAX_LISTED` candidates and no department picked:** buttons `${department} (${n})` from `byDepartment`.
- **Otherwise:** one row per candidate (filtered by the picked department) showing `display_name` and `candidateLabel`, with **Use**. A "Back to departments" link appears when a department is picked.
- When `chosenRef` is set, every button is disabled and the chosen row reads "Chosen".

`ChatHeader.tsx` gains `people?: React.ReactNode`, rendered after the title with `ml-auto`, and wrapping onto its own row at small widths.

`ChatMessage.tsx`:
- reads `const suggestion = suggestionFrom(message.tool_calls)` and `const cards = cardsFrom(message.tool_calls)`;
- renders every card (a person title when set) instead of one;
- when `suggestion` is set, renders one `<SubjectChoice>` per `suggestion.choices[i]`;
- keeps `const [chosen, setChosen] = useState<Record<number, string>>({})` and a `resent` ref;
- takes a new prop `onChoosePerson: (person: Person, suggestion: Suggestion, index: number) => Promise<boolean>`.

On each choice it awaits `onChoosePerson`. If that returns true, it sets `chosen[index] = person.ref`. Once `allChosen(suggestion, next)` is true and the question has not been re-sent (the `resent` ref), it calls the prop `onResend(question)`:
- for `kind === "which_one"`, with `whichOneQuestion(suggestion.question, person)`;
- otherwise with `suggestion.question`.

`ChatInterface.tsx`:
- `recordSource`, loaded once with `fetchRecordSource()`;
- `people: Person[]` and `active: string[]`. On each `sessionId` change they reset, and when `recordSource === "pbms"` they load with `fetchPeople`;
- in the SSE switch, `case "people": setActive((data.active as string[]) ?? []); break;`;
- `people={recordSource === "pbms" ? <PeopleChips people={people} active={active} disabled={isStreaming} onRemove={async (ref) => { if (!sessionId) return; const r = await removePerson(sessionId, ref); if (r) { setPeople(r.people); setActive((a) => a.filter((x) => x !== ref)); } }} /> : undefined}` on `ChatHeader`;
- `onChoosePerson={async (person, s) => { if (!sessionId) return false; if (s.kind === "which_one") return true; const r = await addPerson(sessionId, person.ref); if (r) setPeople(r.people); return Boolean(r); }}` and `onResend={(q) => send(q)}` on each `ChatMessage`. `send` is the existing function `EmptyState`'s `onPick` uses. A which-one person is already in the list, so choosing them adds nothing, and the re-sent question names them.

- [ ] **Step 7: Build, lint and test**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: tests pass, lint shows 0 errors (the existing 2 warnings are allowed), and the build succeeds. Run `grep -rnE "bg-(red|blue|gray|slate|zinc)-|gradient|blur|\{[a-z.]*\.ref\}" frontend/src/components/chat/PeopleChips.tsx frontend/src/components/chat/SubjectChoice.tsx`. Expected: no hits.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/subject.ts frontend/src/lib/subject.test.ts frontend/src/lib/api.ts frontend/src/lib/card.ts frontend/src/lib/card.test.ts frontend/src/components/chat
git commit -m "UI: live people chips, choice cards that re-ask once all are chosen, and a calculation card per person

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Evals: PBMS-shaped fixtures and the S13 "people in context" family

**Files:**
- Modify: `src/pension_chat/evals/fixtures.py` (`source`, and `NamedFixtureProvider`)
- Modify: `src/pension_chat/evals/cases.py` (`Case.people`, the `expected_figures` check)
- Modify: `src/pension_chat/evals/checks.py` (`expected_figures`)
- Modify: `src/pension_chat/evals/runner.py` (the roster; `subjects` passed to the agent)
- Create: `evals/fixtures/employees/pbms_tier_disagreement.yaml`, `pbms_payroll_service.yaml`, `people_a.yaml`, `people_b.yaml`, `people_c.yaml`
- Create: `evals/cases/S4/S4-010.yaml`, `evals/cases/S13/S13-001.yaml` through `S13-004.yaml`
- Test: `tests/test_evals_people.py`

**Interfaces:**
- Consumes: `people.decide` and the `subjects` roster shape (Tasks 5–6); trace figures' `person` (Task 7).
- Produces:
  - `NamedFixtureProvider(fixtures)`, a `FixtureProvider` that also implements `names_in` and `find_by_name` over fixture display names ("FIRST LAST");
  - `Case.people: list[str]` (fixture keys);
  - `roster_for(case, fixtures) -> list[dict]`;
  - the check `expected_figures: [{"person": label, "annual": number}]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evals_people.py
"""Eval support for people in context: named fixtures, a case's roster, per-person figures."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from pension_chat.evals.cases import Turn
from pension_chat.evals.checks import run_checks
from pension_chat.evals.fixtures import NamedFixtureProvider, load_fixtures
from pension_chat.evals.runner import roster_for
from pension_chat.evals.trace import TurnTrace
from pension_chat.records import NameSearch

FIXTURES = load_fixtures(Path("evals/fixtures/employees"), date(2026, 9, 30))


def test_a_pbms_shaped_fixture_carries_its_source_and_gaps() -> None:
    agent = FIXTURES["pbms_tier_disagreement"][0].to_agent_record([])
    assert agent["record_source"] == "PBMS" and "indicates Tier 3" in agent["missing"]["tier"]


async def test_named_fixtures_can_be_found_by_name() -> None:
    provider = NamedFixtureProvider(FIXTURES)
    assert isinstance(provider, NameSearch)
    first, last = FIXTURES["people_a"][0].display_name.split(" ", 1)
    assert provider.names_in(f"What would {first} {last}'s pension be?") == [(first, last)]
    assert [m.key for m in await provider.find_by_name(first, last)] == ["people_a"]


def test_a_cases_roster_is_built_from_its_fixtures() -> None:
    class _Case:
        people = ["people_a", "people_b"]

    roster = roster_for(_Case(), FIXTURES)
    assert [r["ref"] for r in roster] == ["people_a", "people_b"]
    assert roster[0]["display_name"] == FIXTURES["people_a"][0].display_name
    assert roster[0]["first_name"] and roster[0]["last_name"]


def test_expected_figures_are_checked_per_person() -> None:
    trace = TurnTrace(case_id="x", turn_index=0, question="q", employee=None)
    trace.figures = [{"formula": "a", "when": "now", "annual": "19770.89", "person": "RO_BK TA_NE"},
                     {"formula": "b", "when": "now", "annual": "25200.00", "person": "LI_MO GA_RD"}]
    good = Turn(q="q", goal="g", reference="r", checks={"expected_figures": [
        {"person": "RO_BK TA_NE", "annual": 19770.89}, {"person": "LI_MO GA_RD", "annual": 25200.00}]})
    swapped = Turn(q="q", goal="g", reference="r", checks={"expected_figures": [
        {"person": "RO_BK TA_NE", "annual": 25200.00}, {"person": "LI_MO GA_RD", "annual": 19770.89}]})
    assert all(c.passed for c in run_checks(good, trace))
    assert not any(c.passed for c in run_checks(swapped, trace))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_evals_people.py -v`
Expected: FAIL, with `ImportError: cannot import name 'NamedFixtureProvider'`.

- [ ] **Step 3: Fixtures**

In `load_fixtures`, pass `source=raw.get("source"),` to `EmployeeRecord(...)`.

Create `people_a.yaml`, `people_b.yaml` and `people_c.yaml` by **copying `tier2_at_62.yaml`, `tier2_at_67.yaml` and `tier1_capped.yaml` exactly**. Change only `key` (to `people_a`, `people_b`, `people_c`), and set `display_name` to `"RO_BK TA_NE"`, `"LI_MO GA_RD"` and `"FE_NX OR_TL"` respectively. Their hand-worked figures are already proved:

| Fixture | Figure | Source |
|---|---|---|
| `people_a` | 19,770.89 at 62 | S4-007 / `tests/test_core_formulas_meabf.py` |
| `people_b` | 25,200.00 at 67 | S4-008 |
| `people_c` | 80,000.00 capped | S4-009 |

`pbms_tier_disagreement.yaml` (synthetic):

```yaml
key: pbms_tier_disagreement
source: PBMS
display_name: "QW_XZ PL_MR"
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

`pbms_payroll_service.yaml` (synthetic):

```yaml
key: pbms_payroll_service
source: PBMS
display_name: "LK_JH MN_BV"
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

- [ ] **Step 4: `NamedFixtureProvider`, `Case.people`, the roster and `expected_figures`**

In `fixtures.py`:

```python
class NamedFixtureProvider(FixtureProvider):
    """Fixtures findable by name, for cases about people in context (S13).

    A separate class, not a flag: `NameSearch` is a runtime-checkable protocol,
    so a provider that merely *has* the methods takes part in name recognition.
    Cases without `people:` keep the plain FixtureProvider and are untouched.
    """

    def __init__(self, fixtures: dict[str, tuple[EmployeeRecord, list[SalaryYear]]]) -> None:
        super().__init__(fixtures)
        from pension_chat.pbms.names import NameDirectory

        self._pairs = {key: _split(rec.display_name) for key, (rec, _) in fixtures.items() if rec.display_name}
        self._directory = NameDirectory(pair for pair in self._pairs.values() if pair)

    def names_in(self, text: str) -> list[tuple[str, str]]:
        return self._directory.names_in(text)

    async def find_by_name(self, first: str, last: str) -> list[EmployeeMatch]:
        want = (first.upper(), last.upper())
        return [
            EmployeeMatch(key, rec.display_name or key, rec.employer, rec.status, rec.department, None,
                          pair[0], pair[1])
            for key, (rec, _) in self._fixtures.items()
            if (pair := self._pairs.get(key)) and (pair[0].upper(), pair[1].upper()) == want
        ]


def _split(display_name: str | None) -> tuple[str, str] | None:
    parts = (display_name or "").strip().rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else None
```

In `cases.py`, add `people: list[str] = field(default_factory=list)` to `Case`. Parse it from `raw.get("people") or []` where `employee` is parsed (line ~92). Add `"expected_figures": list,` to the check vocabulary at line ~37.

In `checks.py`, add this before the `ttft_class` branch:

```python
    if name == "expected_figures":
        # One figure per person, each to the cent and each under the right name --
        # a comparison that swaps two people's figures fails here.
        misses = []
        for want in value:
            amount = Decimal(str(want["annual"]))
            mine = [Decimal(f["annual"]) for f in trace.figures
                    if f.get("person") == want["person"] and f["when"] == str(want.get("when") or "now")]
            if not any(abs(a - amount) <= Decimal("0.01") for a in mine):
                misses.append(f"{want['person']}: expected {amount}, computed {[str(a) for a in mine] or 'none'}")
        return CheckResult(name, not misses, "; ".join(misses) or "every person's figure as expected")
```

In `runner.py`:

```python
def roster_for(case: Any, fixtures: dict[str, Any]) -> list[dict[str, Any]]:
    """A case's people, as if the user had already chosen them (spec §8)."""
    from pension_chat.evals.fixtures import _split

    roster = []
    for key in getattr(case, "people", None) or []:
        record = fixtures[key][0]
        first, last = _split(record.display_name) or (None, None)
        roster.append({"ref": key, "display_name": record.display_name, "first_name": first,
                       "last_name": last, "department": record.department, "employer": record.employer,
                       "birth_year": None})
    return roster
```

In `run_conversation`, compute `roster = roster_for(case, fixtures)` and use `NamedFixtureProvider(fixtures) if roster else FixtureProvider(fixtures)` inside `use_provider(...)`. Pass `roster` to `_one_turn`, which calls `agent(question, thread, employee, **({"subjects": roster} if roster else {}))`. Extend `_one_turn`'s signature with `roster: list[dict] | None = None`. Cases without `people:` call the agent exactly as before, which keeps test fakes with the old signature working.

- [ ] **Step 5: The cases**

`evals/cases/S4/S4-010.yaml`:

```yaml
id: S4-010
family: S4
areas: [calculation, records]
tiers: [full]
employee: pbms_tier_disagreement
turns:
- q: What would this employee's annuity be if they retired at the end of this year?
  goal: An answer about this PBMS employee that reports the tier discrepancy instead of choosing silently.
  reference: 'PBMS records Tier 2, but participation from 2019 indicates Tier 3; the answer says so and which tier
    any figure uses. With 7.5 years of service, under the 10 years §1-160 and §8-138(g-1) require, no retirement
    annuity is payable yet; the answer says so rather than giving a figure. No internal ID appears.'
  checks:
    ttft_class: heavy
    cites_any: [1-160, 8-138]
```

The S13 cases use `people: [people_a, people_b]` (S13-003 adds `people_c`). Copy the S4-007, S4-008 and S4-009 turn wording for each person's retirement timing, so each expected figure matches its proved value. Read those three case files first and reuse their exact question phrasing, with the person's name in place of "this employee".

- **S13-001 switch and return** (areas `[people, calculation]`, tiers `[smoke, full]`), three turns:
  1. S4-007's question with "RO_BK TA_NE" in place of "this employee". Checks: `expected_figures: [{person: "RO_BK TA_NE", annual: 19770.89}]`, `ttft_class: heavy`.
  2. "And what about LI_MO GA_RD, retiring at 67?" Checks: `expected_figures: [{person: "LI_MO GA_RD", annual: 25200.00}]`.
  3. "Back to RO_BK TA_NE -- how many years of service do they have?" Reference: 14 years, from their record; no figure for LI_MO GA_RD.
- **S13-002 comparison of two** (tiers `[smoke, full]`): "Compare what RO_BK TA_NE would get retiring at 62 with LI_MO GA_RD retiring at 67." Checks: `expected_figures` for both people, `calculation: true`, `ttft_class: heavy`. Reference: one table, one row each, each figure from that person's record.
- **S13-003 comparison of three plus a follow-up** (tiers `[full]`), `people: [people_a, people_b, people_c]`:
  1. A comparison of all three at their S4 timings. `expected_figures` for all three.
  2. "And which of them reaches the 80% cap?" Reference: FE_NX OR_TL (Tier 1, 36 years); the answer names them.
- **S13-004 pronoun after a comparison** (tiers `[smoke, full]`):
  1. The S13-002 question.
  2. "What would she get at 65?" Checks: `no_calculation: true`. Reference: the answer asks which of RO_BK TA_NE or LI_MO GA_RD is meant, and computes nothing.

Validate the cases: `uv run python scripts/run_evals.py --tier smoke --dry-run` if the script has a dry-run flag. Otherwise run `uv run pytest tests -q -k "evals or cases"`, which loads every case file.

- [ ] **Step 6: Run the tests and the new family**

Run: `uv run pytest tests/test_evals_people.py -v && uv run pytest -q`
Expected: all pass.

Run: `uv run python scripts/run_evals.py --tier full --no-save-formulas` limited to S13 and S4-010. Use the runner's family or case filter; check `--help` for the flag name. Record the pass counts and the TTFT of S13-002 against S4-007's in the ledger. Success criterion 5 asks for comparison TTFT within 1 s of a single-person turn. If it is outside that, record the stage breakdown and stop for a ruling.

- [ ] **Step 7: Commit**

```bash
git add src/pension_chat/evals evals/fixtures/employees/people_*.yaml evals/fixtures/employees/pbms_*.yaml evals/cases/S4/S4-010.yaml evals/cases/S13 tests/test_evals_people.py
git commit -m "Evals: S13 people in context -- switching, returning, comparisons checked per person, a pronoun that must ask

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The live smoke test, docs, and the demo check

**Files:**
- Modify: `pyproject.toml` (the `pbms` marker, excluded by default)
- Create: `tests/test_pbms_live.py`
- Modify: `CLAUDE.md`, `.env.example`, the spec (add §9 Demo check)

- [ ] **Step 1: The live smoke test (on demand, never in CI)**

In `pyproject.toml` under `[tool.pytest.ini_options]`, add `markers = ["pbms: reads the client's PBMS live; run on demand with -m pbms"]`, and add `-m 'not pbms'` to `addopts`, creating `addopts` if it is absent.

```python
# tests/test_pbms_live.py
"""On demand only: `uv run pytest -m pbms`. Reads PBMS live; asserts shapes and counts; prints nothing."""
from __future__ import annotations

import random
from datetime import date

import pytest

pytestmark = pytest.mark.pbms


async def test_names_from_the_directory_resolve_to_readable_records() -> None:
    from pension_chat.config import get_settings
    from pension_chat.pbms import build_record_provider

    provider = build_record_provider(get_settings().model_copy(update={"record_source": "pbms"}))
    assert provider is not None
    try:
        assert await provider.load_names() > 1000
        pairs = random.sample(list(provider._names._by_full.values()), 2)
        text = f"Compare {' '.join(pairs[0])} with {' '.join(pairs[1])} at 62"
        assert provider.names_in(text) == pairs
        for first, last in pairs:
            matches = await provider.find_by_name(first, last)
            assert matches and all(m.status == "active" for m in matches)
            record = await provider.get_record(matches[0].key)
            history = await provider.get_salary_history(matches[0].key)
            assert record is not None and record.source == "PBMS" and record.tier in (None, 1, 2, 3)
            assert all(h.year < date.today().year and h.annual_salary > 0 for h in history)
    finally:
        await provider.close()
```

Run: `uv run pytest -q`. Expected: the live test is deselected.

Then run: `uv run pytest -m pbms -v`. Expected: 1 passed. If it fails, report only which assertion failed.

- [ ] **Step 2: Docs**

`.env.example`: add `RECORD_SOURCE=postgres`, `PBMS_SQL_SERVER=`, `PBMS_SQL_DATABASE=`, `PBMS_SQL_USER=` and `PBMS_SQL_PASSWORD=`, with a comment that the read-only login replaces the `sa` credentials with no code change.

`CLAUDE.md`: add a subsection **"PBMS read layer and people in context"** under Architecture, of at most 18 lines. It states:
- `RECORD_SOURCE=pbms` installs `PbmsProvider` process-wide, and evals use `FixtureProvider`, or `NamedFixtureProvider` for cases with `people:`;
- the guard (named statements only, the allow-list, never SSN or `CC*`);
- salary is read through `T_Pen_Ded_Master`;
- names are found in memory, and PBMS is asked only on a hit;
- `agent/people.py` decides who a turn is about, deterministically: a shared name is always a choice, and a singular pronoun after a comparison is asked about;
- people are internal MEAB IDs (`ref`), never shown;
- one loop for several people: `compute_benefit(person=…)` sees one person's record, and the `PEOPLE` view carries the comparison numbers.

Also add `uv run python scripts/pbms_discover.py timing` and `uv run pytest -m pbms` to the Commands block.

- [ ] **Step 3: The demo check (manual, recorded in the spec)**

**Prerequisite:** the Postgres firewall allows this network (49.43.225.208), and migration 009 is applied.

With `RECORD_SOURCE=pbms`, run `uv run uvicorn pension_chat.main:app --reload` and `cd frontend && npm run dev`. The server log should show "PBMS name directory loaded: N names". Pick names with the live test's sampling, and don't write them anywhere committed. Then in the browser:
1. **One match.** Ask about someone whose name has one active match. The confirm card appears, **Use** adds a highlighted chip and answers the question, the calculation card reads "PBMS record", and no MEAB ID appears anywhere on the page.
2. **A shared name.** A name shared by 2–12 people shows the list; a name shared by more than 12 shows departments first.
3. **Follow-ups.** "And at 65?" is answered for the same person with no card. Naming a second person gives their card, then the chip highlight moves as soon as the next answer starts.
4. **A comparison.** "Compare A and B at 62" gives one table with a row each, both chips are highlighted, and there is one calculation card each. "What would she get?" then asks which.
5. **A fifth person.** Choosing a fifth person drops the least recent chip.
6. **PBMS down.** With a wrong `PBMS_SQL_SERVER` and a restart, a question says the fund's records could not be read.

Append "§9 Demo check (date): 1 ✓ … 6 ✓" to the spec, with a note on anything that failed.

- [ ] **Step 4: Run everything and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy . && (cd frontend && npm test && npm run lint)`
Expected: all green.

```bash
git add pyproject.toml tests/test_pbms_live.py CLAUDE.md .env.example docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md
git commit -m "Phase 1: an on-demand live smoke test, docs, and the demo check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Then run `uv run python scripts/run_evals.py --changed`, and record the pass counts and heavy-TTFT median in the ledger.
