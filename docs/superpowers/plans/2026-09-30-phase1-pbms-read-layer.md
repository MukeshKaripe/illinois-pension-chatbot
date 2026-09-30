# Phase 1: MEABF Read Layer and Name-Based Demo Path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An employer names an employee by first and last name in the chat, chooses the right person when the name is shared, and the agent answers from that employee's PBMS record, read live and read-only.

**Architecture:**
- A new package, `src/pension_chat/pbms/`, holds four parts:
  - a guard: `PbmsClient`, which runs only named, read-only, allow-listed statements;
  - the SQL: `queries.py`;
  - the in-memory name directory: `names.py`, which matches names in a message at no database cost;
  - the provider: `PbmsProvider`, which implements `RecordProvider` and adds `names_in` and `find_by_name`.
- The app installs the provider process-wide when `RECORD_SOURCE=pbms`, and refreshes the directory every 6 hours.
- `triage` turns a name in a message into a choose-first suggestion.
- The chosen person becomes the session's **subject** (`sessions.subject_meab_id`). The MEAB ID is internal only, and appears in payloads just as an opaque `ref`.
- `fetch_employee_record` loads the subject's record.

**Tech Stack:**
- Python 3.12, FastAPI, LangGraph, SQLAlchemy/Alembic (Postgres);
- `pyodbc` with ODBC Driver 18 for SQL Server;
- Next.js 16 / React 19 with `node --test`;
- pytest (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md`, revised 2026-09-30 for name lookup. Read all of it, especially §3.4, §4.2 and §6.1.

## Global Constraints

- **Credentials.** The `sa` login is used **for reads only**. Code enforces this, not convention. Credentials come from `PBMS_SQL_SERVER`, `PBMS_SQL_DATABASE`, `PBMS_SQL_USER` and `PBMS_SQL_PASSWORD`. They are never logged, printed, or placed in an exception message or `repr`.
- **No real data in the repo.** No real PBMS data may appear in any fixture, test, log or commit, and that includes the masked names. Scripts that touch PBMS print only counts, rates, codes, shapes and timings.
- **Never write to PBMS.** Nothing is ever written to it.
- **Never read personal identifiers.** No statement ever names `Ssn`, `SSN`, `eSSN`, any `CC*` column, `PbsAddress_*`, `Pbs*_Phone`, `PbsEmail`, `Maiden_Name`, `Middle_Name` or `MemberID`.
- **Session settings.** Every PBMS connection uses `ApplicationIntent=ReadOnly` and `APP=pension-chat-readonly`, and runs at `READ UNCOMMITTED`. Queries time out after 5 s by default; the name directory load is allowed 30 s.
- **Employees are found by first and last name only.** The MEAB ID (`^M-[0-9]{6}$`) is the internal subject key. It is never shown in the UI or written into an answer, and it travels in API payloads only as the opaque `ref`.
- **Active members only.** An active member is a linker row with `MEAB_Id LIKE 'M-%'`, a live employee row (`e.Status = 'A'`) and `e.Employee_Status_Cd = 'A'`.
- **Salary is read through `T_Pen_Ded_Master`.** No statement filters `T_Pen_Ded_Detail` by `Person_Info_Seq_No`.
- **Only tier codes `1`, `2` and `3` map to a tier.** Any other code is recorded in `missing["tier"]`.
- **Gaps stay gaps.** A field PBMS leaves empty is absent, with a reason in `missing`. It is never defaulted, and nothing falls back to Postgres.
- **Nobody is chosen for the user.** A shared name is always offered as a choice. When more than 12 people share it (`MAX_LISTED = 12`), the department is asked first.
- **Default behaviour is unchanged.** With `RECORD_SOURCE` unset, which means `postgres`, local development, tests and evals behave exactly as they do today.
- **Commits.** Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A name typed as "ka_ls ma_lp's", "MA_LP, KA_LS" or "…KA_LS MA_LP?"** is still recognised: case, possessive, `LAST, FIRST` and trailing punctuation are all handled. A name inside a longer token (`XKA_LS`) is not recognised (Task 4).
2. **A name shared by 109 people** is offered department-first, and nobody is auto-selected (Tasks 6 and 8).
3. **A follow-up that repeats the current subject's name** passes straight through, instead of asking the user to choose again (Task 6).
4. **A pooled connection the server has dropped** is retried once on a fresh connection instead of failing the turn (Task 2).
5. **A driver error message that echoes the connection string** never reaches the exception text or `repr(client)` (Task 2).
6. **A question asked in September** does not report the part-year salary as the "current annual salary" (Task 3).

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

### Task 5: The agent reads the conversation's subject

**Files:**
- Modify: `src/pension_chat/agent/state.py`:
  - add `subject_meab_id`, `subject_suggestion` and `record_unavailable` to the TypedDict and to `initial_state()`;
  - add a `subject_meab_id` parameter to `initial_state` and `turn_input`.
- Modify: `src/pension_chat/agent/graph.py:208-248` (`run_agent_stream`)
- Modify: `src/pension_chat/agent/nodes.py:290-337` (`fetch_employee_record`)
- Modify: `src/pension_chat/agent/loop.py:141-183` (`_context`, and a new `_record_notes`)
- Modify: `src/pension_chat/agent/card.py` (`record_label`)
- Test: `tests/test_subject_record.py`

**Interfaces:**
- Consumes: `current_provider` and `RecordUnavailable` (Tasks 2–3), and the agent record's `record_source`, `display_name`, `employer`, `department`, `service_source` and `missing`.
- Produces:
  - `initial_state(..., *, subject_meab_id=None)` and `turn_input(..., *, subject_meab_id=None)`;
  - `run_agent_stream(..., subject_meab_id=None)`;
  - the per-turn state keys `subject_meab_id`, `subject_suggestion` and `record_unavailable`;
  - `loop._record_notes(record) -> str`;
  - the card's `"record_label": "PBMS record" | None`. It deliberately omits the MEAB ID.

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
                              display_name="QW_XZ PL_MR", department="Finance", status="active",
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
    assert "could not be read" in _context({**initial_state("q", "s"), "record_unavailable": True})


def test_the_record_notes_name_the_employee_not_the_internal_key() -> None:
    notes = _record_notes({"id": "M-000123", "display_name": "QW_XZ PL_MR", "department": "Finance",
                           "record_source": "PBMS", "service_source": "counted from payroll",
                           "missing": {"tier": "PBMS records Tier 2, but ..."}})
    assert "QW_XZ PL_MR" in notes and "Finance" in notes and "counted from payroll" in notes
    assert "tier: PBMS records Tier 2" in notes
    assert "M-000123" not in notes


def test_the_card_names_the_pbms_record_without_its_key() -> None:
    state = {**initial_state("q", "s"),
             "employee_record": {"id": "M-000123", "article_number": 8, "record_source": "PBMS"},
             "calculation_result": {"r": {"base": {"ok": True, "inputs_used": {}, "annual_amount": "1"}}}}
    card = calculation_card(state, WorkSheet.from_state(state))
    assert card is not None and card["record_label"] == "PBMS record"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_subject_record.py -v`
Expected: FAIL, with `TypeError: initial_state() got an unexpected keyword argument 'subject_meab_id'`.

- [ ] **Step 3: Update the state and `run_agent_stream`**

In `state.py`, add to `PensionAgentState` next to `employee_id`:

```python
    # The employee this conversation is about -- an internal MEAB ID, never
    # shown -- read from `sessions.subject_meab_id` on every turn. Not the
    # principal: `employee_id` still scopes row-level security and checkpoints.
    subject_meab_id: str | None
    # A name the user typed that is not the subject: its candidates, offered
    # for the user to choose from (`triage`).
    subject_suggestion: dict[str, Any] | None
    # The record source could not be read this turn -- distinct from "no record".
    record_unavailable: bool
```

`initial_state` gains a keyword-only parameter `subject_meab_id: str | None = None`, and its dict gains `"subject_meab_id": subject_meab_id, "subject_suggestion": None, "record_unavailable": False,`. `turn_input` gains the same parameter and adds `subject_meab_id=subject_meab_id,` to `fresh.update(...)`. The keys stay out of `DURABLE_KEYS`: the session row is the durable home of the subject.

In `graph.py`, `run_agent_stream` gains `subject_meab_id: str | None = None` after `thread_owner`, and passes it through:

```python
        turn: dict[str, Any] = initial_state(question, session_id, employee_id, history,
                                             subject_meab_id=subject_meab_id)
    else:
        turn = turn_input(question, session_id, employee_id, subject_meab_id=subject_meab_id)
```

- [ ] **Step 4: Update `fetch_employee_record`**

Replace the lines from `employee_id = state.get("employee_id")` through `record, history = loaded` with:

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

Add `RecordUnavailable` to the `from pension_chat.records import …` line in `nodes.py`.

- [ ] **Step 5: Update the context and the card**

In `loop.py`, change the final `else:` of the facts block (lines 182–183) to:

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

Add this function above `_context`:

```python
def _record_notes(record: dict[str, Any]) -> str:
    """Who this is and what their record could not say -- only for a fund record (PBMS).

    An employer is asking about someone else, so the answer names them -- by
    name and department, never by the internal MEAB ID. A gap the record reports
    (no hire date, a tier PBMS and the dates disagree on) is something to tell
    the employer, not something to fill.
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

In `card.py`, add `"record_label": _record_label(state),` after `"support_level"` in the returned dict. Add this below `calculation_card`:

```python
def _record_label(state: Mapping[str, Any]) -> str | None:
    """Whose record the "Record" inputs came from -- the fund's, named without its internal key."""
    record = state.get("employee_record") or {}
    return f"{record['record_source']} record" if record.get("record_source") else None
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_subject_record.py tests/test_records_provider.py -v`, then `uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/pension_chat/agent/state.py src/pension_chat/agent/graph.py src/pension_chat/agent/nodes.py src/pension_chat/agent/loop.py src/pension_chat/agent/card.py tests/test_subject_record.py
git commit -m "Agent: the conversation's subject is the record it reads; an unreadable record is said, not hidden

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: A name in a message becomes a choice, never a load

**Files:**
- Modify: `src/pension_chat/agent/nodes.py:116-144` (`triage`)
- Modify: `src/pension_chat/agent/graph.py:101-115` (`decline`)
- Test: `tests/test_subject_suggestion.py`

**Interfaces:**
- Consumes: `records.NameSearch` and `current_provider` (Task 4), `RecordUnavailable`, the `EmployeeMatch` fields, and the state keys `subject_meab_id`, `subject_suggestion` and `pinned_record` (Task 5).
- Produces:
  - `nodes.MAX_LISTED = 12`;
  - `nodes._subject_suggestion(state, question) -> dict | None`. Its shape is `{"first_name", "last_name", "question", "unavailable": bool, "candidates": [{"ref", "display_name", "employer", "department", "status", "birth_year"}]}`, with candidates capped at 200;
  - `graph.subject_suggestion_message(s) -> str`;
  - triage's `out_of_scope_reason == "subject_suggestion"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_subject_suggestion.py
"""A name typed in a message is offered for the user to choose; nobody is loaded on a mention."""
from __future__ import annotations

from pension_chat.agent import nodes
from pension_chat.agent.graph import decline, subject_suggestion_message
from pension_chat.records import EmployeeMatch, RecordUnavailable, use_provider


class _Stub:
    def __init__(self, count=1, fail=False):
        self.count, self.fail, self.searched = count, fail, []

    def names_in(self, text):
        return [("KA_LS", "MA_LP")] if "KA_LS MA_LP" in text.upper() else []

    async def find_by_name(self, first, last):
        self.searched.append((first, last))
        if self.fail:
            raise RecordUnavailable("down")
        return [EmployeeMatch(f"M-{i:06d}", "KA_LS MA_LP", "City of Chicago", "active",
                              f"Dept {i % 3}", 1960 + i) for i in range(self.count)]

    async def find_employees(self, query, scope=None):
        return []

    async def get_record(self, key):
        raise AssertionError("a mention must not load a record")

    async def get_salary_history(self, key):
        raise AssertionError("a mention must not load a record")


def _state(q, *, subject=None, subject_name=None):
    pinned = {"record": {"id": subject, "display_name": subject_name}} if subject else None
    return {"question": q, "messages": [q], "subject_meab_id": subject, "pinned_record": pinned}


async def test_a_name_becomes_a_choice_carrying_the_question() -> None:
    with use_provider(_Stub(count=3)):
        out = await nodes.triage(_state("What would KA_LS MA_LP's pension be at 62?"))
    s = out["subject_suggestion"]
    assert out["in_scope"] is False and out["out_of_scope_reason"] == "subject_suggestion"
    assert (s["first_name"], s["last_name"], len(s["candidates"])) == ("KA_LS", "MA_LP", 3)
    assert s["question"] == "What would KA_LS MA_LP's pension be at 62?"
    assert s["candidates"][0] == {"ref": "M-000000", "display_name": "KA_LS MA_LP", "employer": "City of Chicago",
                                  "department": "Dept 0", "status": "active", "birth_year": 1960}


async def test_the_current_subjects_name_passes_straight_through() -> None:
    stub = _Stub()
    with use_provider(stub):
        out = await nodes.triage(_state("and KA_LS MA_LP at 65?", subject="M-000001", subject_name="KA_LS MA_LP"))
    assert out.get("subject_suggestion") is None and stub.searched == []


async def test_without_name_search_names_mean_nothing() -> None:
    assert (await nodes.triage(_state("KA_LS MA_LP"))).get("subject_suggestion") is None


async def test_unavailable_is_said() -> None:
    with use_provider(_Stub(fail=True)):
        s = (await nodes.triage(_state("KA_LS MA_LP")))["subject_suggestion"]
    assert s["unavailable"] is True and s["candidates"] == []


def _s(count, unavailable=False):
    return {"first_name": "KA_LS", "last_name": "MA_LP", "question": "q", "unavailable": unavailable,
            "candidates": [{"ref": f"M-{i:06d}", "display_name": "KA_LS MA_LP", "employer": "City",
                            "department": "Finance", "status": "active", "birth_year": 1964}
                           for i in range(count)]}


def test_the_messages_by_count() -> None:
    assert subject_suggestion_message(_s(1)) == (
        "Did you mean KA_LS MA_LP (Finance, born 1964)? Choose below and I'll answer your question about them.")
    assert subject_suggestion_message(_s(9)).startswith("There are 9 active employees named KA_LS MA_LP. Which one")
    assert "Which department" in subject_suggestion_message(_s(40))
    assert "couldn't find an active employee" in subject_suggestion_message(_s(0))
    assert "not answering" in subject_suggestion_message(_s(0, unavailable=True))
    assert "M-0" not in subject_suggestion_message(_s(1))


async def test_decline_answers_the_suggestion_without_a_model() -> None:
    out = await decline({"out_of_scope_reason": "subject_suggestion", "subject_suggestion": _s(9)})
    assert "9 active employees" in out["final_answer"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_subject_suggestion.py -v`
Expected: FAIL, with `ImportError: cannot import name 'subject_suggestion_message'`.

- [ ] **Step 3: Implement**

In `nodes.py`, extend the records import with `NameSearch`, `current_provider` and `RecordUnavailable`. Add this above `triage`:

```python
# More candidates than this and the choice starts with the department (spec §4.2).
MAX_LISTED = 12
_MAX_CANDIDATES = 200


def _same_name(first: str, last: str, display_name: str | None) -> bool:
    return " ".join(f"{first} {last}".split()).upper() == " ".join((display_name or "").split()).upper()


async def _subject_suggestion(state: PensionAgentState, question: str) -> dict[str, Any] | None:
    """A name the user typed that is not this conversation's employee: its candidates, to choose from.

    Never loaded on a mention -- a name is shared by up to 109 active members, so
    choosing for the user would answer about the wrong person. Matched in memory
    (`names_in`); PBMS is asked only on a hit. Only a provider with name search
    (PBMS) takes part; the Postgres path and the eval fixtures do not.
    """
    provider = current_provider()
    if not isinstance(provider, NameSearch):
        return None
    pinned = (state.get("pinned_record") or {}).get("record") or {}
    current_name = pinned.get("display_name") if pinned.get("id") == state.get("subject_meab_id") else None
    others = [(f, la) for f, la in provider.names_in(question) if not _same_name(f, la, current_name)]
    if not others:
        return None
    first, last = others[0]
    suggestion: dict[str, Any] = {"first_name": first, "last_name": last, "question": question,
                                  "unavailable": False, "candidates": []}
    try:
        matches = await provider.find_by_name(first, last)
    except RecordUnavailable:
        suggestion["unavailable"] = True
        return suggestion
    suggestion["candidates"] = [
        {"ref": m.key, "display_name": m.display_name, "employer": m.employer,
         "department": m.department, "status": m.status, "birth_year": m.birth_year}
        for m in matches[:_MAX_CANDIDATES]
    ]
    return suggestion
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

In `graph.py`, add `from pension_chat.agent.nodes import MAX_LISTED`, merging it into the existing nodes import. Add this above `decline`:

```python
def subject_suggestion_message(suggestion: dict[str, Any]) -> str:
    """The reply to a name in a message: choose one, choose a department, not found, or not checkable.

    Names the people by name, department and birth year -- never by the
    internal MEAB ID the choice card carries as an opaque `ref`.
    """
    name = f"{suggestion.get('first_name', '')} {suggestion.get('last_name', '')}".strip()
    if suggestion.get("unavailable"):
        return (f"I couldn't look up {name} just now: the fund's records are not answering. "
                "Please try again in a moment.")
    candidates = suggestion.get("candidates") or []
    if not candidates:
        return f"I couldn't find an active employee named {name}."
    if len(candidates) == 1:
        one = candidates[0]
        about = ", ".join(v for v in (one.get("department") or one.get("employer"),
                                      f"born {one['birth_year']}" if one.get("birth_year") else None) if v)
        return (f"Did you mean {one.get('display_name') or name}{f' ({about})' if about else ''}? "
                "Choose below and I'll answer your question about them.")
    if len(candidates) <= MAX_LISTED:
        return (f"There are {len(candidates)} active employees named {name}. Which one do you mean? "
                "Choose below and I'll answer your question about them.")
    return (f"There are {len(candidates)} active employees named {name}. Which department do they work in? "
            "Choose below.")
```

In `decline`, replace the `answer = …` line with:

```python
    if reason == "subject_suggestion":
        answer = subject_suggestion_message(state.get("subject_suggestion") or {})
    else:
        answer = GREETING_MESSAGE if reason == "greeting" else OUT_OF_SCOPE_MESSAGE
```

If importing `MAX_LISTED` from `nodes` into `graph` creates a cycle, it won't: `graph.py` already imports `triage` and `fetch_employee_record` from `nodes`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_subject_suggestion.py -v`, then `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/nodes.py src/pension_chat/agent/graph.py tests/test_subject_suggestion.py
git commit -m "Agent: a name in a message is offered for the user to choose, never loaded on a mention

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The session's subject: migration, API and audit

**Files:**
- Create: `alembic/versions/009_session_subject.py`
- Modify: `src/pension_chat/db/models.py:228-270` (`Session`)
- Create: `src/pension_chat/api/subject.py`
- Modify: `src/pension_chat/api/sessions.py`:
  - `send_message` passes the subject;
  - `_subject_trail` joins the audit trail.
- Modify: `src/pension_chat/main.py` (register the subject router)
- Test: `tests/test_subject_api.py`

**Interfaces:**
- Consumes: `normalise_meab_id`, `MEAB_ID_PATTERN`, `current_provider`, `RecordUnavailable` and `EmployeeMatch` (Task 3); `_authorised_session` and `_thread_owner` from `sessions.py`; `run_agent_stream(subject_meab_id=…)` (Task 5); the state keys `subject_suggestion` and `employee_record.record_source`.
- Produces:
  - `api.subject.resolve_ref(provider, ref) -> dict`, which raises 400, 404 or 503;
  - `api.subject.public_match(m) -> dict`, with the keys `display_name`, `employer`, `department`, `status` and `birth_year`, and **no** `ref`;
  - the routes `GET`, `PUT` (body `{"ref": str}`) and `DELETE` on `/api/v1/sessions/{id}/subject`;
  - `sessions._subject_trail(state) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_subject_api.py
"""Choosing, restoring and clearing a conversation's employee; tested at the helpers the routes wrap."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from pension_chat.api.sessions import _subject_trail
from pension_chat.api.subject import resolve_ref
from pension_chat.records import MEAB_ID_PATTERN, EmployeeMatch, RecordUnavailable


class _Stub:
    def __init__(self, matches=(), fail=False):
        self.matches, self.fail, self.asked = list(matches), fail, []

    async def find_employees(self, query, scope=None):
        self.asked.append(query)
        if self.fail:
            raise RecordUnavailable("down")
        return self.matches


A = EmployeeMatch("M-000123", "KA_LS MA_LP", "City of Chicago", "active", "Finance", 1964)


async def test_a_ref_is_re_resolved_and_returned_without_the_key() -> None:
    stub = _Stub([A])
    key, public = await resolve_ref(stub, " m-000123 ")
    assert stub.asked == ["M-000123"] and key == "M-000123"
    assert public == {"display_name": "KA_LS MA_LP", "employer": "City of Chicago", "department": "Finance",
                      "status": "active", "birth_year": 1964}


@pytest.mark.parametrize("ref, matches, status", [
    ("KA_LS MA_LP", [A], 400),
    ("M-12", [A], 400),
    ("M-000999", [], 404),
])
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


def test_the_audit_trail_names_the_subject_and_any_choice_offered() -> None:
    suggestion = {"first_name": "KA_LS", "last_name": "MA_LP", "candidates": []}
    state = {"employee_record": {"id": "M-000123", "record_source": "PBMS"}, "subject_suggestion": suggestion}
    assert _subject_trail(state) == [
        {"type": "subject", "ref": "M-000123"},
        {"type": "subject_suggestion", **suggestion},
    ]
    assert _subject_trail({"employee_record": {"id": "x"}}) == []


def test_the_migration_checks_the_same_pattern_the_code_uses() -> None:
    text = Path("alembic/versions/009_session_subject.py").read_text(encoding="utf-8")
    assert "subject_meab_id" in text and 'down_revision = "008_answer_evidence"' in text
    assert MEAB_ID_PATTERN in text
    assert re.fullmatch(MEAB_ID_PATTERN, "M-000123") and not re.fullmatch(MEAB_ID_PATTERN, "m-000123")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_subject_api.py -v`
Expected: FAIL, with `ModuleNotFoundError: No module named 'pension_chat.api.subject'`.

- [ ] **Step 3: Write the migration and update the model**

```python
# alembic/versions/009_session_subject.py
"""A conversation's subject: the employee an employer is asking about.

Kept as the internal MEAB ID -- the one identifier that is unique; users find
employees by name and never see it. Not the principal: `sessions.employee_id` is
who is chatting and scopes row-level security. The existing `sessions` policies
cover the new columns. The check is `records.MEAB_ID_PATTERN`: ^M-[0-9]{6}$

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
    # The employee this conversation is about (migration 009): an internal MEAB
    # ID, never shown. Not the principal -- `employee_id` scopes RLS.
    subject_meab_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Add `CheckConstraint("subject_meab_id ~ '^M-[0-9]{6}$'", name="ck_sessions_subject_meab_id"),` to its `__table_args__`.

- [ ] **Step 4: Write `api/subject.py`**

```python
# src/pension_chat/api/subject.py
"""The conversation's employee: chosen from a name's candidates, restored, cleared.

The choice card sends back the opaque `ref` it was given; the server re-resolves
it in PBMS (active members only) before storing it, and never returns it -- the
MEAB ID is internal (spec §4.1).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import update

from pension_chat.api.auth import TokenData, get_current_user
from pension_chat.db.models import Session
from pension_chat.db.session import get_db_session
from pension_chat.records import EmployeeMatch, RecordProvider, RecordUnavailable, current_provider, normalise_meab_id

router = APIRouter(prefix="/api/v1", tags=["subject"])


class SubjectRequest(BaseModel):
    ref: str


def public_match(match: EmployeeMatch) -> dict[str, Any]:
    return {"display_name": match.display_name, "employer": match.employer,
            "department": match.department, "status": match.status, "birth_year": match.birth_year}


async def resolve_ref(provider: RecordProvider | None, ref: str) -> tuple[str, dict[str, Any]]:
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
    return matches[0].key, public_match(matches[0])


async def _store(session: Session, meab_id: str | None) -> None:
    from pension_chat.api.sessions import _thread_owner

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
    """The conversation's employee, re-read so the chip is current."""
    from pension_chat.api.sessions import _authorised_session

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    if not session.subject_meab_id:
        return None
    try:
        return (await resolve_ref(current_provider(), session.subject_meab_id))[1]
    except HTTPException:
        # Still the subject even when the name cannot be read right now.
        return {"display_name": None, "employer": None, "department": None, "status": None, "birth_year": None}


@router.put("/sessions/{session_id}/subject")
async def put_subject(session_id: str, body: SubjectRequest, request: Request,
                      user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    from pension_chat.api.sessions import _authorised_session

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    key, public = await resolve_ref(current_provider(), body.ref)
    await _store(session, key)
    return public


@router.delete("/sessions/{session_id}/subject")
async def delete_subject(session_id: str, request: Request,
                         user: TokenData | None = Depends(get_current_user)) -> dict[str, bool]:
    from pension_chat.api.sessions import _authorised_session

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    await _store(session, None)
    return {"cleared": True}
```

`resolve_ref` returns `(key, public)`. The test above unpacks it that way.

In `main.py`, add `from pension_chat.api.subject import router as subject_router  # noqa: E402` and `app.include_router(subject_router)`.

- [ ] **Step 5: Pass the subject to each turn and record it**

In `sessions.py`, add:

```python
def _subject_trail(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Who this turn was about, and any choice it offered -- for the audit and the choice card."""
    trail: list[dict[str, Any]] = []
    record = state.get("employee_record") or {}
    if record.get("record_source"):
        trail.append({"type": "subject", "ref": record.get("id")})
    if suggestion := state.get("subject_suggestion"):
        trail.append({"type": "subject_suggestion", **suggestion})
    return trail
```

In `send_message`, add `subject_meab_id=session.subject_meab_id,` to the `run_agent_stream(...)` call. Change the audit line to:

```python
        tool_calls = _audit_trail(final_state) + ([card] if card else []) + _subject_trail(final_state)
```

`session` is the ORM object loaded at the top of `send_message`. Its column attributes were loaded before its database session closed, so `subject_meab_id` is available.

- [ ] **Step 6: Run the tests and apply the migration**

Run: `uv run pytest tests/test_subject_api.py -v`, then `uv run pytest -q`
Expected: all pass.

Run: `uv run alembic upgrade head`
Expected: `Running upgrade 008_answer_evidence -> 009_session_subject`. This touches the project's own Postgres, never PBMS. If the connection is refused, the Postgres firewall does not yet allow this network (49.43.225.208). Record that in the ledger and continue. Apply the migration once the rule exists, and run it before the demo check in Task 9.

- [ ] **Step 7: Lint, type-check and commit**

Run: `uv run ruff check . && uv run mypy .`

```bash
git add alembic/versions/009_session_subject.py src/pension_chat/db/models.py src/pension_chat/api/subject.py src/pension_chat/api/sessions.py src/pension_chat/main.py tests/test_subject_api.py
git commit -m "API: a conversation's employee -- chosen by opaque ref, re-resolved in PBMS, passed to every turn, kept in the audit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The employee chip, the choice card, and the card label

**Files:**
- Create: `frontend/src/lib/subject.ts`, `frontend/src/lib/subject.test.ts`
- Modify: `frontend/src/lib/api.ts` (after `forgetScenario`)
- Modify: `frontend/src/lib/card.ts`:
  - add `record_label` to `CalculationCardData`;
  - add a `recordLabel` parameter to `sourceLabel`.
- Modify: every `sourceLabel(` caller (`grep -rn "sourceLabel(" frontend/src`)
- Create: `frontend/src/components/chat/SubjectChip.tsx`, `frontend/src/components/chat/SubjectChoice.tsx`
- Modify: `frontend/src/components/chat/ChatHeader.tsx`, `ChatMessage.tsx` and `ChatInterface.tsx`

**Interfaces:**
- Consumes:
  - the Task 7 routes;
  - the public match shape `{display_name, employer, department, status, birth_year}`;
  - the `tool_calls` entry `{"type": "subject_suggestion", first_name, last_name, question, unavailable, candidates: [{ref, …}]}`;
  - the card's `record_label`;
  - `/health`'s `record_source`.
- Produces:
  - `Subject`, `Candidate` and `Suggestion` (TypeScript);
  - `MAX_LISTED = 12`;
  - `chipLabel(s)`, `candidateLabel(c)`, `byDepartment(cs)` and `suggestionFrom(toolCalls)`;
  - `setSubject(sessionId, ref)`, `clearSubject`, `fetchSubject` and `fetchRecordSource`.

Before writing components, read `frontend/AGENTS.md` and the `node_modules/next/dist/docs/` page on client components. Write `"use client"` leaf components in the style of `ScenarioChips.tsx`, using semantic token utilities only. **Never render `ref`.**

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/lib/subject.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";

import { byDepartment, candidateLabel, chipLabel, MAX_LISTED, suggestionFrom } from "./subject.ts";

const c = (ref: string, department: string | null, birth_year: number | null = 1964) => ({
  ref, display_name: "KA_LS MA_LP", employer: "City of Chicago", department, status: "active", birth_year,
});

test("the chip reads name then department, falling back to employer", () => {
  assert.equal(chipLabel({ display_name: "KA_LS MA_LP", employer: "City", department: "Finance", status: "active", birth_year: 1964 }),
    "KA_LS MA_LP · Finance");
  assert.equal(chipLabel({ display_name: "KA_LS MA_LP", employer: "City", department: null, status: null, birth_year: null }),
    "KA_LS MA_LP · City");
});

test("a candidate reads department, employer and birth year, never the ref", () => {
  const label = candidateLabel(c("M-000001", "Finance"));
  assert.equal(label, "Finance · City of Chicago · born 1964");
  assert.ok(!label.includes("M-000001"));
});

test("more than MAX_LISTED candidates are grouped by department, largest first", () => {
  const many = [...Array(MAX_LISTED + 3)].map((_, i) => c(`M-${i}`, i % 3 === 0 ? "Streets" : i % 3 === 1 ? "Water" : null));
  const groups = byDepartment(many);
  assert.deepEqual(groups.map((g) => [g.department, g.candidates.length]),
    [["Streets", 5], ["Water", 5], ["No department on file", 5]]);
});

test("a suggestion is read from tool_calls", () => {
  const s = { first_name: "KA_LS", last_name: "MA_LP", question: "q", unavailable: false, candidates: [c("M-1", "F")] };
  assert.equal(suggestionFrom([{ type: "card" }, { type: "subject_suggestion", ...s }])?.question, "q");
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
/** The conversation's employee: found by name in the chat, chosen by the user (Phase 1). */

export interface Subject {
  display_name: string | null;
  employer: string | null;
  department: string | null;
  status: string | null;
  birth_year: number | null;
}

/** One person a name matched. `ref` is opaque: sent back on a choice, never shown. */
export interface Candidate extends Subject {
  ref: string;
}

export interface Suggestion {
  first_name: string;
  last_name: string;
  question: string;
  unavailable: boolean;
  candidates: Candidate[];
}

/** More candidates than this and the choice starts with the department. */
export const MAX_LISTED = 12;

const NO_DEPARTMENT = "No department on file";

export function chipLabel(s: Subject): string {
  return [s.display_name, s.department ?? s.employer].filter((p): p is string => Boolean(p)).join(" · ");
}

export function candidateLabel(c: Candidate): string {
  return [c.department, c.employer, c.birth_year ? `born ${c.birth_year}` : null]
    .filter((p): p is string => Boolean(p))
    .join(" · ");
}

export function byDepartment(candidates: Candidate[]): { department: string; candidates: Candidate[] }[] {
  const groups = new Map<string, Candidate[]>();
  for (const c of candidates) {
    const key = c.department ?? NO_DEPARTMENT;
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }
  return [...groups.entries()]
    .map(([department, cs]) => ({ department, candidates: cs }))
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
  if (!hit || !Array.isArray(hit.candidates)) return null;
  const { type: _type, ...rest } = hit;
  return rest;
}
```

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npm test`
Expected: all pass.

- [ ] **Step 5: Add the API functions and the card label**

Append to `api.ts`, and add `import type { Subject } from "./subject";` at the top:

```ts
/** Make a candidate this conversation's employee; resolves to who they are, or null on failure. */
export async function setSubject(sessionId: string, ref: string): Promise<Subject | null> {
  const response = await fetchWithAuth(`/sessions/${sessionId}/subject`, {
    method: "PUT",
    body: JSON.stringify({ ref }),
  });
  return response.ok ? response.json() : null;
}

export async function clearSubject(sessionId: string): Promise<boolean> {
  return (await fetchWithAuth(`/sessions/${sessionId}/subject`, { method: "DELETE" })).ok;
}

export async function fetchSubject(sessionId: string): Promise<Subject | null> {
  const response = await fetchWithAuth(`/sessions/${sessionId}/subject`);
  return response.ok ? response.json() : null;
}

/** "pbms" when employee records come from the fund; the employee chip shows only then. */
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
- make `sourceLabel` take `(source, recordLabel?: string | null)` and return `recordLabel` when `source === "record" && recordLabel`.

Add this to `card.test.ts`:

```ts
test("a record input names the fund's record when the card carries one", () => {
  assert.equal(sourceLabel("record", "PBMS record"), "PBMS record");
  assert.equal(sourceLabel("record"), "Record");
});
```

Update every `sourceLabel(input.source)` call to `sourceLabel(input.source, card.record_label)`.

- [ ] **Step 6: Write the components and wire them in**

`SubjectChip.tsx`:
- Props: `{ subject: Subject | null; onClear: () => void; disabled?: boolean }`.
- With a subject: a chip with `chipLabel(subject)` and a **Clear** button (`aria-label="Clear employee"`).
- Without one: muted text, "Name an employee in your question".

`SubjectChoice.tsx`:
- Props: `{ suggestion: Suggestion; onChoose: (ref: string, question: string) => void; disabled?: boolean }`.
- Local state: `department: string | null` and `chosen: string | null`.
- It renders nothing when `suggestion.unavailable` or when there are no candidates, because the message text already says why.
- **More than `MAX_LISTED` candidates and no department picked:** buttons from `byDepartment`, labelled `${department} (${n})`. Clicking one sets `department`.
- **Otherwise:** the candidates (filtered to the chosen department if one is set), one row each showing `display_name` and `candidateLabel(c)`, with a **Use** button. It calls `onChoose(c.ref, suggestion.question)` and sets `chosen = c.ref`.
- Once `chosen` is set, every button is disabled and the chosen row is marked "Chosen". When a department is picked, there is also a "Back to departments" link.

`ChatHeader.tsx` gains an optional `subject?: React.ReactNode` rendered at the right end (`ml-auto`). It must wrap rather than scroll horizontally at 360 px.

`ChatMessage.tsx` reads `const suggestion = suggestionFrom(message.tool_calls);`, renders `<SubjectChoice>` under the message body when it is set, and takes a new prop `onChooseSubject?: (ref: string, question: string) => void`.

`ChatInterface.tsx`:
- `const [recordSource, setRecordSource] = useState("postgres")`, loaded once with `fetchRecordSource()` in a mount effect;
- `const [subject, setSubjectState] = useState<Subject | null>(null)`;
- on every `sessionId` change: `setSubjectState(null)`, then, when `recordSource === "pbms"` and `sessionId` is set, `fetchSubject(sessionId).then(setSubjectState)`;
- `subject={recordSource === "pbms" ? <SubjectChip subject={subject} onClear={async () => { if (sessionId && await clearSubject(sessionId)) setSubjectState(null); }} disabled={isStreaming} /> : undefined}` on `ChatHeader`;
- `onChooseSubject={async (ref, question) => { if (!sessionId) return; const s = await setSubject(sessionId, ref); if (s) { setSubjectState(s); send(question); } }}` on each `ChatMessage`. `send` is the existing function `EmptyState`'s `onPick` uses. Re-sending the original question is what makes the user not ask twice.

- [ ] **Step 7: Build, lint and test**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: tests pass, lint shows 0 errors (the existing 2 warnings are allowed), and the build succeeds. Run `grep -rnE "bg-(red|blue|gray|slate|zinc)-|gradient|blur|\.ref\b.*<|>\{.*\.ref\}" frontend/src/components/chat/Subject*.tsx`. Expected: no palette, gradient or blur hits, and no `ref` rendered.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/subject.ts frontend/src/lib/subject.test.ts frontend/src/lib/api.ts frontend/src/lib/card.ts frontend/src/lib/card.test.ts frontend/src/components/chat
git commit -m "UI: choose an employee from a name's candidates (department first when many); the chip; record inputs name the PBMS record

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Eval fixtures, the live smoke test, docs, and the demo check

**Files:**
- Modify: `src/pension_chat/evals/fixtures.py` (read an optional `source`)
- Create: `evals/fixtures/employees/pbms_tier_disagreement.yaml`, `evals/fixtures/employees/pbms_payroll_service.yaml`
- Create: `evals/cases/S4/S4-010.yaml`
- Modify: `pyproject.toml` (the `pbms` marker, excluded by default)
- Create: `tests/test_pbms_live.py`
- Modify: `CLAUDE.md`, `.env.example`, the spec (add §9 Demo check)
- Test: `tests/test_evals_fixtures.py` (create it if it does not exist)

**Interfaces:**
- Consumes: everything above.
- Produces: PBMS-shaped fixtures that exercise `_record_notes` in evals, and a recorded demo check.

- [ ] **Step 1: Write the failing fixture test**

```python
# tests/test_evals_fixtures.py  (append if the file exists)
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

`evals/fixtures/employees/pbms_tier_disagreement.yaml` (synthetic):

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

`evals/fixtures/employees/pbms_payroll_service.yaml` (synthetic):

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
  reference: 'The answer names the employee by name, not by any ID. PBMS records Tier 2, but participation from 2019
    indicates Tier 3; the answer says so and which tier any figure uses. With 7.5 years of service, under the 10
    years §1-160 and §8-138(g-1) require, no retirement annuity is payable yet; the answer says so rather than
    giving an annuity figure.'
  checks:
    ttft_class: heavy
    cites_any:
    - 1-160
    - 8-138
```

Run: `uv run pytest tests/test_evals_fixtures.py -v && uv run pytest tests -q -k evals`
Expected: pass.

- [ ] **Step 3: Add the live smoke test (on demand, never in CI)**

In `pyproject.toml` under `[tool.pytest.ini_options]`, add `markers = ["pbms: reads the client's PBMS live; run on demand with -m pbms"]`, and add `-m 'not pbms'` to `addopts`, creating `addopts` if it is absent.

```python
# tests/test_pbms_live.py
"""On demand only: `uv run pytest -m pbms`. Reads PBMS live; asserts shapes and counts; prints nothing."""
from __future__ import annotations

import random
from datetime import date

import pytest

pytestmark = pytest.mark.pbms


async def test_a_name_from_the_directory_resolves_to_a_readable_record() -> None:
    from pension_chat.config import get_settings
    from pension_chat.pbms import build_record_provider

    provider = build_record_provider(get_settings().model_copy(update={"record_source": "pbms"}))
    assert provider is not None
    try:
        assert await provider.load_names() > 1000
        first, last = random.choice(list(provider._names._by_full.values()))
        assert provider.names_in(f"What would {first} {last}'s pension be at 62?") == [(first, last)]
        matches = await provider.find_by_name(first, last)
        assert matches and all(m.status == "active" for m in matches)
        record = await provider.get_record(matches[0].key)
        history = await provider.get_salary_history(matches[0].key)
    finally:
        await provider.close()
    assert record is not None and record.source == "PBMS" and record.article_number == 8
    assert record.tier in (None, 1, 2, 3)
    assert all(h.year < date.today().year and h.annual_salary > 0 for h in history)
```

Run: `uv run pytest -q`
Expected: the live test is deselected.

Then run it once by hand: `uv run pytest -m pbms -v`
Expected: 1 passed. Assertion messages could echo a value, so if it fails, report only which assertion failed.

- [ ] **Step 4: Docs**

`.env.example`: add `RECORD_SOURCE=postgres`, `PBMS_SQL_SERVER=`, `PBMS_SQL_DATABASE=`, `PBMS_SQL_USER=` and `PBMS_SQL_PASSWORD=`, with a comment that the read-only login replaces the `sa` credentials with no code change.

`CLAUDE.md`: add a subsection **"PBMS read layer (`pbms/`)"** under Architecture, of at most 14 lines. It states:
- `RECORD_SOURCE=pbms` installs `PbmsProvider` process-wide, and evals still use `FixtureProvider`;
- the guard (named statements only, the allow-list, never SSN or `CC*`);
- salary is read through `T_Pen_Ded_Master`;
- employees are found by first and last name from the in-memory directory, and PBMS is asked only on a hit;
- a shared name is always a choice (department first above 12);
- the subject (`sessions.subject_meab_id`) is an internal MEAB ID, never shown, and is not the principal;
- gaps go in `missing` and reach the answer through `_record_notes`.

Also add `uv run python scripts/pbms_discover.py timing` to the Commands block.

- [ ] **Step 5: The demo check (manual, recorded in the spec)**

**Prerequisite:** the Postgres firewall allows this network (49.43.225.208), and migration 009 is applied (`uv run alembic upgrade head`).

With `RECORD_SOURCE=pbms` in `.env`, run `uv run uvicorn pension_chat.main:app --reload` and `cd frontend && npm run dev`. The server log should show "PBMS name directory loaded: N names". Then in the browser, pick names by count with `scripts/pbms_discover.py`'s sampling or the live test; don't write them down anywhere committed:
1. Ask a pension question naming someone whose name has one active match. The reply asks to confirm, and **Use** sets the chip and answers the original question. The calculation card's record inputs read "PBMS record", and no MEAB ID appears anywhere on the page.
2. Name someone whose name is shared by 2–12 people. The list shows department · employer · born YYYY, and choosing one answers the question.
3. Name someone shared by more than 12. Departments come first, then the people.
4. With a subject set, a follow-up that repeats the same name is answered directly, without asking again.
5. Break the connection (set a wrong `PBMS_SQL_SERVER` and restart). A question says the fund's records could not be read.

Append "§9 Demo check (date): 1 ✓ 2 ✓ 3 ✓ 4 ✓ 5 ✓" to the spec, with a note on anything that failed.

- [ ] **Step 6: Run everything and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy . && (cd frontend && npm test && npm run lint)`
Expected: all green.

```bash
git add src/pension_chat/evals/fixtures.py evals/fixtures/employees/pbms_*.yaml evals/cases/S4/S4-010.yaml pyproject.toml tests/test_pbms_live.py tests/test_evals_fixtures.py CLAUDE.md .env.example docs/superpowers/specs/2026-09-30-phase1-pbms-read-layer-design.md
git commit -m "Phase 1: PBMS-shaped eval fixtures, an on-demand live smoke test, docs, and the demo check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Then run `uv run python scripts/run_evals.py --changed`, and record the pass counts and heavy-TTFT median in the ledger for comparison with the last run.
