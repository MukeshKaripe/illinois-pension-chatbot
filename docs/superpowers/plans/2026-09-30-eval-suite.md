# Eval Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the member-first eval harness with a scenario-driven suite for the employer-first scope (MEABF). The new suite grades multi-turn conversations with code checks that can veto and a judge that compares each answer against a reference, and it measures time to the first answer token, with an estimate of the in-region figure.

**Architecture:**
- **Four seams in the agent:**
  - a `RecordProvider` context variable read by `fetch_employee_record`;
  - a graph override, so a run can supply an in-memory checkpointer;
  - a per-turn instrument (a context variable) that times each remote call and records which subsections were rendered to the model;
  - a note recorded whenever search falls back to running without the semantic ranker.
- **A new package, `src/pension_chat/evals/`,** loads YAML cases and fixtures, runs conversations concurrently through the real `run_agent_stream`, and builds a `TurnTrace` for each turn. It then runs the code checks, calls the judge, and writes a report compared against a baseline.
- **`scripts/run_evals.py`** is the command-line entry point.

**Tech Stack:** Python 3.12, `uv`, pytest (asyncio), LangGraph (`InMemorySaver`), SQLAlchemy async events, httpx and asyncpg (for the probe), PyYAML, Azure OpenAI (`gpt-6-luna`).

**Spec:** `docs/superpowers/specs/2026-09-30-eval-suite-design.md` (approved 2026-09-30; S11 is first-class).

## Global Constraints

- Nothing a run does writes to the client's databases. Records and checkpoints stay in memory. The runner sets `CAPTURE_FAILURES=false`, `ANSWER_MEMORY_ENABLED=false` and `PERSIST_DERIVED_RECIPES=false` unless `--with-memory` is passed.
- No synthetic members in the client's Postgres. Fixtures exist only as YAML plus memory.
- `data/` is never committed. `evals/runs/` and `evals/.judge_cache/` are git-ignored.
- TTFT is the first *answer* token; progress lines do not count (D3a). Classes: `simple` = 3.0 s, `heavy` = 7.0 s.
- A turn passes only when **all** of these hold:
  - every code check passes;
  - the judge returns `goal=yes`, `relevant=yes` and `within_boundary=yes`;
  - `behaviour` is in `acceptable` whenever the turn lists acceptable behaviours.
- The judge gates only once it is calibrated: it must agree on ≥18 of 20 labelled turns, for the current rubric version. Until then its verdicts are reported and do not gate.
- The missing-input policy: at most **2** assumed secondary inputs per answer. A critical input is never assumed.
- Default concurrency is **8** conversations. Targets: smoke under 3 min, full under 12 min.
- The first-class families are **S1, S4, S4b and S11**; S6 comes next.
- Seams change no behaviour when unused. The existing suite (`uv run pytest`) stays green after every task.
- Commit messages follow the repo's style (`Area: what changed`) and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Fixture ages drift with the calendar.** `build_member_facts` uses `date.today()`, so a fixture with a fixed birth date changes age every day. Fixtures are therefore written in relative terms (`age`, `hired_years_ago`) and turned into dates against the run date. Task 5 pins this with a test.
2. **Concurrent conversations must not share state.** The instrument, the provider and the graph override are context variables set inside each conversation's own task. A test runs two fake conversations concurrently and checks that neither's calls appear in the other's trace (Task 10).
3. **A rate limit must not count as latency, or as a failure.** The waits from a `RateLimitError` retry are subtracted from TTFT, and a turn that errors on a 429 is retried rather than failed (Tasks 3 and 10).
4. **A replaced answer must be checked as the user finally saw it.** The fact checks read `final_answer`, not the streamed text, and `not_replaced` / `verification_passed` read the replacement events (Tasks 6 and 7).
5. **A stale judge cache after a rubric change.** The cache key includes the rubric's version hash, so editing `rubric.yaml` invalidates every cached verdict (Task 9).

---

## File structure

**Created:**
- `src/pension_chat/records.py`: `EmployeeRecord`, `SalaryYear`, `EmployeeMatch`, the `RecordProvider` protocol, `use_provider()` and `current_provider()`. This is the production interface; `PbmsProvider` implements it in Phase 1.
- `src/pension_chat/agent/instrument.py`: the per-turn `Instrument` (calls, notes, rendered evidence), `timed()`, `note()`, `rendered()` and `install_sqlalchemy()`.
- `src/pension_chat/evals/__init__.py`
- `src/pension_chat/evals/capabilities.py`: the `needs:` tags, known and available.
- `src/pension_chat/evals/cases.py`: the `Case` and `Turn` models, the loader, the check vocabulary and area validation.
- `src/pension_chat/evals/fixtures.py`: `FixtureProvider` and the relative-date materialiser.
- `src/pension_chat/evals/trace.py`: `TurnTrace` and `build_trace()`.
- `src/pension_chat/evals/checks.py`: `run_checks(turn, trace) -> list[CheckResult]`.
- `src/pension_chat/evals/timing.py`: `probe()`, `estimate_in_region()` and `stage_breakdown()`.
- `src/pension_chat/evals/judge.py`: `judge_turn()`, the prompt, parsing, the cache and calibration.
- `src/pension_chat/evals/runner.py`: `run_conversation()`, `run_suite()`, the after-failure marking and the flaky re-run.
- `src/pension_chat/evals/select.py`: area paths, `--changed`, tiers.
- `src/pension_chat/evals/report.py`: scoring, the critical block, latency tables, the baseline diff, markdown and JSON.
- `scripts/run_evals.py`: the command line.
- `evals/rubric.yaml`, `evals/labels/README.md`, `evals/fixtures/employees/*.yaml`, `evals/cases/<family>/*.yaml`
- Tests: `tests/test_records_provider.py`, `tests/test_graph_override.py`, `tests/test_instrument.py`, `tests/test_rendered_evidence.py`, `tests/test_evals_cases.py`, `tests/test_evals_fixtures.py`, `tests/test_evals_trace.py`, `tests/test_evals_checks.py`, `tests/test_evals_timing.py`, `tests/test_evals_judge.py`, `tests/test_evals_runner.py`, `tests/test_evals_select.py`, `tests/test_evals_report.py`, `tests/test_evals_inventory.py`, `tests/fixtures/eval_traces/tier2_before_fix.json`

**Modified:**
- `src/pension_chat/agent/nodes.py` (`fetch_employee_record`)
- `src/pension_chat/agent/graph.py` (`active_graph`, `use_graph`)
- `src/pension_chat/agent/llm.py` (timing and rate-limit notes around the three `create()` calls)
- `src/pension_chat/agent/retrieval.py` (timing on embeddings and search; a fallback note; spans in `_assemble`; `merge_variants_with_ids`)
- `src/pension_chat/agent/tools.py` (`_render` records the subsections it rendered)
- `src/pension_chat/db/session.py` (`install_sqlalchemy(engine)`)
- `.gitignore`, `CLAUDE.md`

**Deleted (Task 14):**
- `scripts/eval_agent.py`, `scripts/replay_coverage.py`, `scripts/promote_captures.py`
- `evals/*.yaml` and `evals/_captured/`
- `tests/test_eval_checker.py`, `tests/test_eval_latency.py`, `tests/test_eval_suite.py`

---

### Task 1: The record seam

**Files:**
- Create: `src/pension_chat/records.py`
- Modify: `src/pension_chat/agent/nodes.py` (`fetch_employee_record`, around line 275)
- Test: `tests/test_records_provider.py`

**Interfaces:**
- Produces:
  - `EmployeeRecord`, `SalaryYear(year: int, annual_salary: Decimal)`, `EmployeeMatch`;
  - `RecordProvider` (Protocol) with `find_employees(query, scope=None)`, `get_record(key) -> EmployeeRecord | None` and `get_salary_history(key) -> list[SalaryYear]`;
  - `use_provider(provider)` (a context manager) and `current_provider() -> RecordProvider | None`;
  - `EmployeeRecord.to_agent_record(history) -> dict`, the shape `fetch_employee_record` already returns;
  - `agent_history(history) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_records_provider.py
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
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_records_provider.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'pension_chat.records'`

- [ ] **Step 3: Write `records.py`**

```python
# src/pension_chat/records.py
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

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol


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
        return record


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


_provider: ContextVar[RecordProvider | None] = ContextVar("pension_chat_record_provider", default=None)


def current_provider() -> RecordProvider | None:
    return _provider.get()


@contextmanager
def use_provider(provider: RecordProvider) -> Iterator[RecordProvider]:
    token = _provider.set(provider)
    try:
        yield provider
    finally:
        _provider.reset(token)
```

- [ ] **Step 4: Make `fetch_employee_record` ask the provider**

In `src/pension_chat/agent/nodes.py`, add `from pension_chat.records import agent_history, current_provider` to the imports. In `fetch_employee_record`, replace

```python
    else:
        loaded = await _load_record(employee_id)
```

with

```python
    else:
        loaded = await _from_provider(str(employee_id)) if current_provider() else await _load_record(employee_id)
```

Then add, directly above `_load_record`:

```python
async def _from_provider(key: str) -> tuple[dict, list[dict]] | None:
    """The record from the provider in force: PBMS in production, fixtures in evals."""
    provider = current_provider()
    assert provider is not None
    record = await provider.get_record(key)
    if record is None:
        return None
    history = await provider.get_salary_history(key)
    return record.to_agent_record(history), agent_history(history)
```

- [ ] **Step 5: Run the tests to watch them pass**

Run: `uv run pytest tests/test_records_provider.py -v`
Expected: 6 passed

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (2177 + 6)

- [ ] **Step 7: Commit**

```bash
git add src/pension_chat/records.py src/pension_chat/agent/nodes.py tests/test_records_provider.py
git commit -m "Records: the agent reads employees through a RecordProvider when one is in force

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Graph override (in-memory checkpoints)

**Files:**
- Modify: `src/pension_chat/agent/graph.py` (`active_graph`, around line 156)
- Test: `tests/test_graph_override.py`

**Interfaces:**
- Produces: `use_graph(graph)` (a context manager). `active_graph()` returns the override when one is set. `_record_turn` and `run_agent_stream` both call `active_graph()`, so both follow the override.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_override.py
"""An eval run's conversations live in memory, never in the client's Postgres."""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from pension_chat.agent import graph as g


def test_override_is_used_and_reset() -> None:
    mine = g.build_loop_graph(InMemorySaver())
    assert g.active_graph() is g.loop_graph
    with g.use_graph(mine):
        assert g.active_graph() is mine
    assert g.active_graph() is g.loop_graph
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_graph_override.py -v`
Expected: FAIL with `AttributeError: module 'pension_chat.agent.graph' has no attribute 'use_graph'`

- [ ] **Step 3: Implement**

In `graph.py`, add `from collections.abc import Iterator`, `from contextlib import contextmanager` and `from contextvars import ContextVar`. Then change `active_graph` to:

```python
_graph_override: ContextVar[Any | None] = ContextVar("pension_chat_graph_override", default=None)


def active_graph():
    """(keep the existing docstring, and add:) An eval run substitutes a graph
    compiled over an in-memory checkpointer with `use_graph`, so nothing it
    does reaches Postgres."""
    return _graph_override.get() or loop_graph


@contextmanager
def use_graph(graph: Any) -> Iterator[Any]:
    token = _graph_override.set(graph)
    try:
        yield graph
    finally:
        _graph_override.reset(token)
```

- [ ] **Step 4: Run it to watch it pass, then the full suite**

Run: `uv run pytest tests/test_graph_override.py -v && uv run pytest -q`
Expected: 1 passed; the full suite all passes

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/graph.py tests/test_graph_override.py
git commit -m "Graph: a run can substitute its own compiled graph (in-memory checkpoints)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The per-turn instrument (remote-call timing, fallback and rate-limit notes)

**Files:**
- Create: `src/pension_chat/agent/instrument.py`
- Modify:
  - `src/pension_chat/agent/llm.py`: the three `await client.chat.completions.create(**kwargs)` calls in `call_with_tools`, `complete` and `stream`, and their retry branches;
  - `src/pension_chat/agent/retrieval.py`: `_embed`'s `client.embeddings.create`, `_search_one`'s two `client.search` calls, and `fetch_sections`'s `client.search`;
  - `src/pension_chat/db/session.py`: after `engine = create_async_engine(...)`.
- Test: `tests/test_instrument.py`

**Interfaces:**
- Produces:
  - `Call(kind: str, label: str, start: float, end: float)`, where times come from `time.perf_counter()`;
  - `Instrument` with `.calls: list[Call]`, `.notes: list[tuple[float, str, str]]` and `.rendered: dict[str, list[str]]`;
  - `begin() -> Instrument` and `current() -> Instrument | None`;
  - `timed(kind, label)` (an async context manager);
  - `note(kind, detail="")` and `rendered(section, subsection_ids)`;
  - `install_sqlalchemy(engine)`.
- Kinds: `model`, `embedding`, `search`, `postgres`, `postgres_connect`.
- Notes: `semantic_fallback`, `rate_limited` (with the detail set to the seconds waited).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_instrument.py
"""Every remote call a turn makes is timed, and costs nothing when no run is watching."""
from __future__ import annotations

import asyncio

import pytest

from pension_chat.agent import instrument


@pytest.mark.asyncio
async def test_timed_records_nothing_without_an_instrument() -> None:
    assert instrument.current() is None
    async with instrument.timed("search", "q"):
        pass                                # must not raise


@pytest.mark.asyncio
async def test_timed_records_a_call() -> None:
    inst = instrument.begin()
    async with instrument.timed("model", "investigate"):
        await asyncio.sleep(0.01)
    [call] = inst.calls
    assert call.kind == "model" and call.label == "investigate"
    assert call.end - call.start >= 0.009


@pytest.mark.asyncio
async def test_a_failed_call_is_still_recorded() -> None:
    inst = instrument.begin()
    with pytest.raises(RuntimeError):
        async with instrument.timed("search", "q"):
            raise RuntimeError("boom")
    assert [c.kind for c in inst.calls] == ["search"]


@pytest.mark.asyncio
async def test_concurrent_tasks_keep_their_own_instruments() -> None:
    async def one(kind: str) -> list[str]:
        inst = instrument.begin()
        async with instrument.timed(kind, kind):
            await asyncio.sleep(0.01)
        return [c.kind for c in inst.calls]

    a, b = await asyncio.gather(one("model"), one("search"))
    assert a == ["model"] and b == ["search"]


def test_notes_and_rendered() -> None:
    inst = instrument.begin()
    instrument.note("semantic_fallback", "tier 2")
    instrument.rendered("1-160", ["(a)", "(c)"])
    instrument.rendered("1-160", ["(c)", "(d)"])
    assert inst.notes[0][1:] == ("semantic_fallback", "tier 2")
    assert inst.rendered["1-160"] == ["(a)", "(c)", "(d)"]


@pytest.mark.asyncio
async def test_postgres_statements_are_timed_through_engine_events() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    instrument.install_sqlalchemy(engine)
    inst = instrument.begin()
    async with engine.connect() as conn:
        await conn.execute(text("select 1"))
    await engine.dispose()
    assert "postgres" in [c.kind for c in inst.calls]
```

If `aiosqlite` is not installed (`uv run python -c "import aiosqlite"` fails), add it with `uv add --dev aiosqlite` in this step. It is a test-only dependency.

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_instrument.py -v`
Expected: collection ERROR, `cannot import name 'instrument'`

- [ ] **Step 3: Write `instrument.py`**

```python
# src/pension_chat/agent/instrument.py
"""A per-turn record of what the turn did over the network, for the eval suite.

A context variable, like token usage in llm.py: `begin()` starts one for the
turn in flight, and every remote call made inside that turn -- model,
embeddings, search, Postgres -- is timed into it. Nothing is recorded, and
nothing costs anything, when no instrument is in force (production). Nothing
here enters the graph state.

It also records which subsections of each section were actually rendered to
the model (`rendered`). Retrieved is not the same as read: on 2026-09-30,
Section 1-160 was retrieved on every run while its subsection (c) -- the
answer -- never reached the model.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event


@dataclass
class Call:
    kind: str
    label: str
    start: float
    end: float


@dataclass
class Instrument:
    calls: list[Call] = field(default_factory=list)
    notes: list[tuple[float, str, str]] = field(default_factory=list)
    rendered: dict[str, list[str]] = field(default_factory=dict)
    pending_connect: float | None = None


_current: ContextVar[Instrument | None] = ContextVar("pension_chat_instrument", default=None)


def begin() -> Instrument:
    inst = Instrument()
    _current.set(inst)
    return inst


def current() -> Instrument | None:
    return _current.get()


@asynccontextmanager
async def timed(kind: str, label: str = "") -> AsyncIterator[None]:
    inst = _current.get()
    if inst is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        inst.calls.append(Call(kind, label, start, time.perf_counter()))


def note(kind: str, detail: str = "") -> None:
    inst = _current.get()
    if inst is not None:
        inst.notes.append((time.perf_counter(), kind, detail))


def rendered(section: str, subsection_ids: list[str]) -> None:
    inst = _current.get()
    if inst is None:
        return
    seen = inst.rendered.setdefault(section, [])
    for sid in subsection_ids:
        if sid not in seen:
            seen.append(sid)


def install_sqlalchemy(engine: Any) -> None:
    """Time every statement and every new connection on this engine.

    SQLAlchemy's async engine runs these events in the caller's context
    (checked 2026-09-30), so the instrument in force is the one for this turn.
    """
    sync = engine.sync_engine

    @event.listens_for(sync, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if _current.get() is not None:
            conn.info["_instrument_start"] = time.perf_counter()

    @event.listens_for(sync, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        inst = _current.get()
        start = conn.info.pop("_instrument_start", None)
        if inst is not None and start is not None:
            inst.calls.append(Call("postgres", statement.split(None, 1)[0].lower(), start, time.perf_counter()))

    @event.listens_for(sync, "do_connect")
    def _do_connect(dialect, conn_rec, cargs, cparams):  # noqa: ANN001
        inst = _current.get()
        if inst is not None:
            inst.pending_connect = time.perf_counter()

    @event.listens_for(sync, "connect")
    def _connect(dbapi_connection, connection_record):  # noqa: ANN001
        inst = _current.get()
        if inst is not None and inst.pending_connect is not None:
            inst.calls.append(Call("postgres_connect", "", inst.pending_connect, time.perf_counter()))
            inst.pending_connect = None
```

- [ ] **Step 4: Run the tests to watch them pass**

Run: `uv run pytest tests/test_instrument.py -v`
Expected: 6 passed

- [ ] **Step 5: Wire it in**

- **`db/session.py`**, directly after the `engine = create_async_engine(...)` block:

  ```python
  from pension_chat.agent.instrument import install_sqlalchemy  # noqa: E402

  install_sqlalchemy(engine)
  ```

  If this import creates a cycle (`agent.instrument` imports only `sqlalchemy`, so it should not), move `instrument.py` to `pension_chat/instrument.py` and adjust the imports in every task accordingly.

- **`agent/llm.py`**: add `from pension_chat.agent import instrument`. Then wrap each of the three `response = await client.chat.completions.create(**kwargs)` lines:

  ```python
              async with instrument.timed("model", node):
                  response = await client.chat.completions.create(**kwargs)
  ```

  In each of the three retry branches, directly before `await asyncio.sleep(0.4 * (attempt + 1))`, add:

  ```python
                  if isinstance(exc, RateLimitError):
                      instrument.note("rate_limited", f"{0.4 * (attempt + 1):.2f}")
  ```

  Bind the exception as `exc` in the `except _RETRYABLE` clause if it isn't already.

- **`agent/retrieval.py`**: add `from pension_chat.agent import instrument`, then:
  - wrap `await client.embeddings.create(...)` in `async with instrument.timed("embedding", "query"):`;
  - wrap both `results = await client.search(**kwargs)` lines in `_search_one`, and the one in `fetch_sections`, in `async with instrument.timed("search", query):`. In `fetch_sections`, use the label `"fetch"`;
  - in `_search_one`, inside the first `except Exception as exc:` (the one that retries without the semantic ranker), add `instrument.note("semantic_fallback", query)` as its first line.

- [ ] **Step 6: A wiring test**

Append to `tests/test_instrument.py`:

```python
@pytest.mark.asyncio
async def test_the_semantic_fallback_is_noted(monkeypatch) -> None:
    from pension_chat.agent import retrieval

    calls = {"n": 0}

    class _Results:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _Client:
        async def search(self, **kwargs):
            calls["n"] += 1
            if "query_type" in kwargs:
                raise RuntimeError("semantic quota exhausted")
            return _Results()

    monkeypatch.setattr(retrieval, "get_async_search_client", lambda: _Client())
    inst = instrument.begin()
    await retrieval._search_one("tier 2", None, None, 8)
    assert [n[1] for n in inst.notes] == ["semantic_fallback"]
    assert [c.kind for c in inst.calls] == ["search", "search"]
```

Run: `uv run pytest tests/test_instrument.py -v && uv run pytest -q`
Expected: 7 passed; the full suite all passes

- [ ] **Step 7: Commit**

```bash
git add src/pension_chat/agent/instrument.py src/pension_chat/agent/llm.py src/pension_chat/agent/retrieval.py src/pension_chat/db/session.py tests/test_instrument.py pyproject.toml uv.lock
git commit -m "Instrument: time every remote call a turn makes, note semantic fallbacks and rate limits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Evidence accounting (which subsections reached the model)

**Files:**
- Modify: `src/pension_chat/agent/retrieval.py` (`merge_variants`, `_assemble`, `RetrievedSection`)
- Modify: `src/pension_chat/agent/tools.py` (`_render`, around line 369)
- Test: `tests/test_rendered_evidence.py`

**Interfaces:**
- Consumes: `instrument.rendered(section, ids)` from Task 3.
- Produces:
  - `merge_variants_with_ids(chunks, subsection_key=_subsection_sort_key) -> list[tuple[str, list[str]]]`; `merge_variants` becomes a wrapper that returns only the texts;
  - `RetrievedSection.spans: list[tuple[str, int]]`, pairs of (subsection id, end offset in `text`), included in `to_dict()` as `"spans"`;
  - `tools._render` calls `instrument.rendered(section, ids)` for the subsections whose span ends within the text it actually rendered.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rendered_evidence.py
"""What reached the model, subsection by subsection -- not what was retrieved."""
from __future__ import annotations

from pension_chat.agent import instrument
from pension_chat.agent.retrieval import _assemble, merge_variants_with_ids
from pension_chat.agent.tools import _render


def _c(sub: str, body: str) -> dict:
    return {"id": f"8-129_{sub.strip('()')}", "section": "8-129", "article": "8",
            "section_title": "Age and service annuity", "subsection_id": sub, "content": body}


def test_merge_with_ids_names_the_subsections_each_part_carries() -> None:
    parts = merge_variants_with_ids([_c("(b)", "(b) two"), _c("(a)", "(a) one")])
    assert parts == [("(a) one", ["(a)"]), ("(b) two", ["(b)"])]


def test_assemble_records_spans_for_fully_included_parts_only() -> None:
    section = _assemble("8-129", [_c("(a)", "a" * 500), _c("(b)", "b" * 3000)], 1.0, char_budget=1500)
    ids = [sid for sid, _ in section.spans]
    assert ids == ["(a)"]                   # (b) was cut, so it is not evidence
    assert section.truncated


def test_render_reports_what_it_rendered() -> None:
    section = _assemble("8-129", [_c("(a)", "a" * 500), _c("(b)", "b" * 500)], 1.0, char_budget=10_000)
    inst = instrument.begin()
    _render([section.to_dict()], budget=700)
    assert inst.rendered == {"8-129": ["(a)"]}


def test_render_without_an_instrument_is_unchanged() -> None:
    section = _assemble("8-129", [_c("(a)", "(a) one")], 1.0, char_budget=10_000)
    assert "(a) one" in _render([section.to_dict()], budget=10_000)
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_rendered_evidence.py -v`
Expected: collection ERROR, `cannot import name 'merge_variants_with_ids'`

- [ ] **Step 3: Implement in `retrieval.py`**

Rename the body of `merge_variants` to `merge_variants_with_ids`, returning `(text, ids)` pairs:
- the one-version path returns `[(text, [subsection_id])]` for each non-empty chunk;
- the versions note returns `(note, [])`;
- a shared part returns `(body, [subsection])`, where `subsection` is the group's key;
- a labelled wording returns `(labelled_body, [subsection])`.

Then:

```python
def merge_variants(chunks, subsection_key=_subsection_sort_key) -> list[str]:
    """(keep the existing docstring)"""
    return [text for text, _ in merge_variants_with_ids(chunks, subsection_key)]
```

Add `spans: list[tuple[str, int]] = field(default_factory=list)` to `RetrievedSection`, and add `"spans": [list(s) for s in self.spans]` to `to_dict()`. In `_assemble`, iterate `for body, ids in merge_variants_with_ids(chunks):`. For each body appended **whole**, append `(sid, end)` for each `sid` in `ids`, where `end = len("\n".join(parts))` after appending. A truncated body adds no spans. Pass `spans=spans` to `RetrievedSection(...)`.

- [ ] **Step 4: Implement in `tools._render`**

Add `from pension_chat.agent import instrument`. After computing the (possibly cut) `text` for a section, and before appending the block:

```python
        spans = section.get("spans") or []
        if spans:
            instrument.rendered(
                str(section["section"]), [sid for sid, end in spans if end <= len(text)]
            )
```

- [ ] **Step 5: Run the tests to watch them pass, then the full suite**

Run: `uv run pytest tests/test_rendered_evidence.py tests/test_retrieval_variants.py tests/test_sections.py -v && uv run pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/pension_chat/agent/retrieval.py src/pension_chat/agent/tools.py tests/test_rendered_evidence.py
git commit -m "Retrieval: record which subsections were rendered to the model, not just retrieved

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Cases, capabilities and fixtures

**Files:**
- Create: `src/pension_chat/evals/__init__.py` (empty), `capabilities.py`, `cases.py`, `fixtures.py`
- Create: the 12 fixture files under `evals/fixtures/employees/` (listed in Step 5)
- Test: `tests/test_evals_cases.py`, `tests/test_evals_fixtures.py`

**Interfaces:**
- Consumes: `EmployeeRecord`, `SalaryYear` and `EmployeeMatch` (Task 1).
- Produces:
  - `capabilities.KNOWN`, `capabilities.AVAILABLE` (frozensets of str);
  - `cases.CHECKS` (a dict from check name to expected value type), `cases.AREAS` (frozenset), `cases.FAMILIES` (tuple), `cases.FIRST_CLASS = ("S1", "S4", "S4b", "S11")`, `cases.BEHAVIOURS`;
  - `@dataclass Turn(q, goal, reference, acceptable: list[str], checks: dict, employee: str | None)`;
  - `@dataclass Case(id, family, areas, turns, critical=False, needs=[], employee=None, tiers=["full"], path: Path)`, with the property `pending -> bool`, true when `needs` is not a subset of `AVAILABLE`;
  - `load_cases(root: Path, fixture_keys: set[str]) -> list[Case]`, which raises `CaseError` on any problem;
  - `fixtures.load_fixtures(root: Path, today: date) -> dict[str, tuple[EmployeeRecord, list[SalaryYear]]]`;
  - `fixtures.FixtureProvider(fixtures)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evals_cases.py
from __future__ import annotations

from pathlib import Path

import pytest

from pension_chat.evals.cases import CaseError, load_cases

GOOD = """
id: S1-900
family: S1
areas: [retrieval]
critical: true
turns:
  - q: "What is the Tier 2 retirement age?"
    goal: "The age and the section."
    reference: "Age 67 with 10 years (§1-160(c))."
    checks:
      cites_any: ["1-160"]
      ttft_class: simple
"""


def _write(tmp: Path, text: str, name: str = "c.yaml") -> Path:
    (tmp / "S1").mkdir(exist_ok=True)
    (tmp / "S1" / name).write_text(text, encoding="utf-8")
    return tmp


def test_loads_a_case(tmp_path: Path) -> None:
    [case] = load_cases(_write(tmp_path, GOOD), fixture_keys=set())
    assert case.id == "S1-900" and case.critical and not case.pending
    assert case.turns[0].checks["cites_any"] == ["1-160"]


@pytest.mark.parametrize(
    "mutation, message",
    [
        (("cites_any:", "cite_any:"), "unknown check 'cite_any'"),
        (("family: S1", "family: S99"), "unknown family"),
        (("areas: [retrieval]", "areas: [retreival]"), "unknown area"),
        (("goal: \"The age and the section.\"", "goal: \"\""), "goal"),
        (("ttft_class: simple", "ttft_class: fast"), "ttft_class"),
    ],
)
def test_refuses_a_malformed_case(tmp_path: Path, mutation, message) -> None:
    with pytest.raises(CaseError, match=message):
        load_cases(_write(tmp_path, GOOD.replace(*mutation)), fixture_keys=set())


def test_refuses_an_unknown_fixture(tmp_path: Path) -> None:
    text = GOOD.replace("critical: true", "critical: true\nemployee: nobody")
    with pytest.raises(CaseError, match="fixture 'nobody'"):
        load_cases(_write(tmp_path, text), fixture_keys={"tier2_standard"})


def test_refuses_an_unknown_capability(tmp_path: Path) -> None:
    text = GOOD.replace("critical: true", "critical: true\nneeds: [telepathy]")
    with pytest.raises(CaseError, match="capability 'telepathy'"):
        load_cases(_write(tmp_path, text), fixture_keys=set())


def test_a_case_needing_an_unbuilt_capability_is_pending(tmp_path: Path) -> None:
    text = GOOD.replace("critical: true", "critical: true\nneeds: [find_employee]")
    [case] = load_cases(_write(tmp_path, text), fixture_keys=set())
    assert case.pending


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, GOOD, "a.yaml")
    _write(tmp_path, GOOD, "b.yaml")
    with pytest.raises(CaseError, match="duplicate id 'S1-900'"):
        load_cases(tmp_path, fixture_keys=set())


def test_an_unacceptable_behaviour_name_is_refused(tmp_path: Path) -> None:
    text = GOOD.replace("    checks:", "    acceptable: [shrug]\n    checks:")
    with pytest.raises(CaseError, match="behaviour 'shrug'"):
        load_cases(_write(tmp_path, text), fixture_keys=set())
```

```python
# tests/test_evals_fixtures.py
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pension_chat.evals.fixtures import FixtureProvider, load_fixtures

TIER2 = """
key: tier2_standard
display_name: "A. Rivera"
employer: City of Chicago
department: Streets and Sanitation
age: 58
hired_years_ago: 13
tier: 2
status: active
service_years: 12.5
service_source: stored
salary: {current: 84000, growth: 0.03, years: 4}
"""

NO_HIRE = """
key: no_hire_date
display_name: "B. Chen"
age: 61
tier: null
status: active
salary: {current: 70000, growth: 0.0, years: 2}
missing: {hire_date: "not recorded in PBMS", service_years: "no stored service"}
"""


def _dir(tmp: Path, **files: str) -> Path:
    for name, text in files.items():
        (tmp / f"{name}.yaml").write_text(text, encoding="utf-8")
    return tmp


def test_relative_dates_are_materialised_against_the_run_date(tmp_path: Path) -> None:
    today = date(2026, 9, 30)
    fx = load_fixtures(_dir(tmp_path, a=TIER2), today)
    record, history = fx["tier2_standard"]
    assert record.birth_date == date(1968, 9, 30)
    assert record.hire_date == date(2013, 9, 30)
    assert [h.year for h in history] == [2023, 2024, 2025, 2026]
    assert history[-1].annual_salary == Decimal("84000")
    assert history[0].annual_salary == (Decimal("84000") / Decimal("1.03") ** 3).quantize(Decimal("0.01"))


def test_the_age_does_not_drift_with_the_calendar(tmp_path: Path) -> None:
    for today in (date(2026, 9, 30), date(2030, 1, 15)):
        record, _ = load_fixtures(_dir(tmp_path, a=TIER2), today)["tier2_standard"]
        age = today.year - record.birth_date.year - ((today.month, today.day) < (record.birth_date.month, record.birth_date.day))
        assert age == 58


def test_missing_fields_stay_missing_with_their_reason(tmp_path: Path) -> None:
    record, _ = load_fixtures(_dir(tmp_path, b=NO_HIRE), date(2026, 9, 30))["no_hire_date"]
    assert record.hire_date is None and record.service_years is None
    assert record.missing["hire_date"] == "not recorded in PBMS"


@pytest.mark.asyncio
async def test_provider_answers_from_fixtures(tmp_path: Path) -> None:
    provider = FixtureProvider(load_fixtures(_dir(tmp_path, a=TIER2, b=NO_HIRE), date(2026, 9, 30)))
    assert (await provider.get_record("tier2_standard")).tier == 2
    assert await provider.get_record("nobody") is None
    assert len(await provider.get_salary_history("tier2_standard")) == 4
    matches = await provider.find_employees("rivera")
    assert [m.key for m in matches] == ["tier2_standard"]
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_evals_cases.py tests/test_evals_fixtures.py -v`
Expected: collection ERROR, `No module named 'pension_chat.evals'`

- [ ] **Step 3: Write `capabilities.py`**

```python
# src/pension_chat/evals/capabilities.py
"""Which `needs:` tags exist, and which have shipped.

Adding a tag to AVAILABLE is the act of shipping that capability to the evals:
from then on, the cases that need it count toward the score.
"""
KNOWN = frozenset({
    "statute", "calculation", "clarification", "recommendation",
    "employer_voice", "find_employee", "article_boundary", "pbms_records",
})
AVAILABLE = frozenset({"statute", "calculation", "clarification", "recommendation"})
```

- [ ] **Step 4: Write `cases.py`**

```python
# src/pension_chat/evals/cases.py
"""Load and validate eval cases. A case is one conversation; each turn says
what the user wants (for the judge) and which code checks apply (the vetoes).

Everything is validated before a single turn runs: an unknown check, family,
area, capability, behaviour or fixture is a typo that would otherwise pass
silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pension_chat.evals import capabilities

FAMILIES = ("S1", "S2", "S3", "S4", "S4b", "S5", "S6", "S7", "S8", "S9", "S10", "S11", "S12")
FIRST_CLASS = ("S1", "S4", "S4b", "S11")
AREAS = frozenset({
    "retrieval", "citations", "calculation", "clarification", "assumptions",
    "boundary", "continuity", "recommendation", "records", "voice",
})
BEHAVIOURS = frozenset({"answer", "ask_critical", "branch_on_critical", "ask_other", "refuse"})
TTFT_CLASSES = {"simple": 3.0, "heavy": 7.0}

#: check name -> the type its value must have
CHECKS: dict[str, type | tuple[type, ...]] = {
    "cites_any": list, "cites_all": list, "cites_none_outside": list, "evidence_has": list,
    "must_state": list, "must_not_state": list,
    "calculation": bool, "no_calculation": bool, "figures_from": list, "worked_from_article": str,
    "critical_inputs": list, "max_assumed": int, "assumptions_disclosed": bool,
    "no_reask": list, "max_questions": int,
    "refuses": bool, "answers_from_article": str,
    "verification_passed": bool, "not_replaced": bool,
    "ttft_class": str, "about_employee": str,
}


class CaseError(ValueError):
    pass


@dataclass
class Turn:
    q: str
    goal: str
    reference: str
    acceptable: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    employee: str | None = None


@dataclass
class Case:
    id: str
    family: str
    areas: list[str]
    turns: list[Turn]
    path: Path
    critical: bool = False
    needs: list[str] = field(default_factory=list)
    employee: str | None = None
    tiers: list[str] = field(default_factory=lambda: ["full"])

    @property
    def pending(self) -> bool:
        return not set(self.needs) <= capabilities.AVAILABLE


def _fail(path: Path, message: str) -> None:
    raise CaseError(f"{path}: {message}")


def _turn(path: Path, raw: dict[str, Any], fixture_keys: set[str]) -> Turn:
    for key in ("q", "goal", "reference"):
        if not str(raw.get(key) or "").strip():
            _fail(path, f"a turn has no {key}")
    checks = dict(raw.get("checks") or {})
    for name, value in checks.items():
        if name not in CHECKS:
            _fail(path, f"unknown check '{name}'")
        if not isinstance(value, CHECKS[name]):
            _fail(path, f"check '{name}' needs a {CHECKS[name]}")
    if "ttft_class" in checks and checks["ttft_class"] not in TTFT_CLASSES:
        _fail(path, f"ttft_class must be one of {sorted(TTFT_CLASSES)}")
    acceptable = list(raw.get("acceptable") or [])
    for b in acceptable:
        if b not in BEHAVIOURS:
            _fail(path, f"unknown behaviour '{b}'")
    employee = raw.get("employee")
    if employee and employee not in fixture_keys:
        _fail(path, f"unknown fixture '{employee}'")
    return Turn(str(raw["q"]), str(raw["goal"]), str(raw["reference"]), acceptable, checks, employee)


def load_cases(root: Path, fixture_keys: set[str]) -> list[Case]:
    cases: list[Case] = []
    seen: dict[str, Path] = {}
    for path in sorted(root.rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        case_id = str(raw.get("id") or "")
        if not case_id:
            _fail(path, "no id")
        if case_id in seen:
            _fail(path, f"duplicate id '{case_id}' (also in {seen[case_id]})")
        seen[case_id] = path
        family = raw.get("family")
        if family not in FAMILIES:
            _fail(path, f"unknown family '{family}'")
        areas = list(raw.get("areas") or [])
        for area in areas:
            if area not in AREAS:
                _fail(path, f"unknown area '{area}'")
        needs = list(raw.get("needs") or [])
        for need in needs:
            if need not in capabilities.KNOWN:
                _fail(path, f"unknown capability '{need}'")
        employee = raw.get("employee")
        if employee and employee not in fixture_keys:
            _fail(path, f"unknown fixture '{employee}'")
        turns = [_turn(path, t, fixture_keys) for t in raw.get("turns") or []]
        if not turns:
            _fail(path, "no turns")
        cases.append(Case(
            id=case_id, family=family, areas=areas, turns=turns, path=path,
            critical=bool(raw.get("critical")), needs=needs, employee=employee,
            tiers=list(raw.get("tiers") or ["full"]),
        ))
    return cases
```

- [ ] **Step 5: Write `fixtures.py` and the 12 fixture files**

```python
# src/pension_chat/evals/fixtures.py
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
```

Create `evals/fixtures/employees/` with one file per archetype. `tier2_standard.yaml` and `no_hire_date.yaml` have exactly the content of `TIER2` and `NO_HIRE` in the test above. The other 10:

```yaml
# tier1_veteran.yaml
key: tier1_veteran
display_name: "M. Okafor"
employer: City of Chicago
department: Water Management
age: 59
hired_years_ago: 31
tier: 1
status: active
service_years: 30.75
service_source: stored
salary: {current: 102000, growth: 0.025, years: 10}
```
```yaml
# tier3_recent.yaml
key: tier3_recent
display_name: "L. Nguyen"
employer: City of Chicago
department: Public Health
age: 41
hired_years_ago: 7
tier: 3
status: active
service_years: 6.9
service_source: stored
salary: {current: 68000, growth: 0.03, years: 7}
```
```yaml
# no_stored_service.yaml
key: no_stored_service
display_name: "R. Patel"
employer: Board of Education
age: 57
hired_years_ago: 22
tier: 1
status: active
salary: {current: 76000, growth: 0.02, years: 10}
missing: {service_years: "no stored service in PBMS"}
```
```yaml
# near_threshold.yaml
key: near_threshold
display_name: "D. Moreno"
employer: City of Chicago
department: Aviation
age: 66
hired_years_ago: 10
tier: 2
status: active
service_years: 9.8
service_source: stored
salary: {current: 71000, growth: 0.02, years: 10}
```
```yaml
# retiree.yaml
key: retiree
display_name: "E. Walsh"
employer: City of Chicago
age: 71
hired_years_ago: 38
tier: 1
status: retired
service_years: 33.0
service_source: stored
salary: {current: 95000, growth: 0.0, years: 1}
```
```yaml
# smith_streets.yaml
key: smith_streets
display_name: "J. Smith"
employer: City of Chicago
department: Streets and Sanitation
age: 54
hired_years_ago: 20
tier: 1
status: active
service_years: 19.5
service_source: stored
salary: {current: 74000, growth: 0.02, years: 10}
```
```yaml
# smith_library.yaml
key: smith_library
display_name: "J. Smith"
employer: Chicago Public Library
age: 38
hired_years_ago: 9
tier: 2
status: active
service_years: 8.9
service_source: stored
salary: {current: 61000, growth: 0.03, years: 9}
```
```yaml
# reciprocal.yaml
key: reciprocal
display_name: "K. Ibrahim"
employer: City of Chicago
age: 60
hired_years_ago: 12
tier: 1
status: active
service_years: 11.5
service_source: stored
reciprocal_service_years: 9.0
salary: {current: 88000, growth: 0.02, years: 10}
```
```yaml
# unmapped_status.yaml
key: unmapped_status
display_name: "T. Kowalski"
employer: City of Chicago
age: 52
hired_years_ago: 15
tier: 2
status: "DB7"
service_years: 14.0
service_source: stored
salary: {current: 66000, growth: 0.02, years: 10}
missing: {status_meaning: "status code DB7 is not mapped"}
```
```yaml
# tier0_closed.yaml
key: tier0_closed
display_name: "P. Russo"
employer: City of Chicago
age: 80
hired_years_ago: 50
tier: 0
status: "closed"
service_years: 20.0
service_source: stored
missing: {salary_history: "closed record"}
```

- [ ] **Step 6: Run the tests to watch them pass**

Run: `uv run pytest tests/test_evals_cases.py tests/test_evals_fixtures.py -v`
Expected: all passed. Then check that the real fixture directory loads:
`uv run python -c "from datetime import date; from pathlib import Path; from pension_chat.evals.fixtures import load_fixtures; print(sorted(load_fixtures(Path('evals/fixtures/employees'), date.today())))"`
Expected: a list of the 12 keys

- [ ] **Step 7: Commit**

```bash
git add src/pension_chat/evals evals/fixtures tests/test_evals_cases.py tests/test_evals_fixtures.py
git commit -m "Evals: case loader with a closed check vocabulary, and relative-dated MEABF fixtures

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: TurnTrace

**Files:**
- Create: `src/pension_chat/evals/trace.py`
- Test: `tests/test_evals_trace.py`

**Interfaces:**
- Consumes: `Instrument` (Task 3). The `run_agent_stream` event dicts:
  - `token {text}`, `replace {text, reason}`, `progress {text, at}`, `status {stage, node}`, `error {message, detail}`;
  - `done {state, card, scenarios}`.
- Produces:
  - `@dataclass TurnTrace` with the fields below, plus `to_dict()` and `from_dict()`;
  - `build_trace(*, case_id, turn_index, question, employee, events: list[tuple[float, dict]], instrument, started: float) -> TurnTrace`.
  - `events` is a list of `(seconds since the turn started, event)`, and `started` is the `perf_counter()` value at the turn's start.

`TurnTrace` fields:
- `case_id: str`, `turn_index: int`, `question: str`, `employee: str | None`
- `streamed_answer: str`, `final_answer: str`, `replacements: list[dict]` (each `{"reason": ...}`)
- `error: str | None`, `in_scope: bool`
- `tools: list[dict]` (`{"name", "args"}`), `retrieved: list[str]`, `rendered: dict[str, list[str]]`
- `verified_citations: list[str]`, `unverified_citations: list[str]`, `unverified_numbers: list`
- `guardrail_passed: bool | None`, `failure_codes: list[str]` (from the final report and every replacement reason)
- `input_sources: dict[str, str]`, `assumptions: dict`, `calculation: bool`, `card: dict | None`
- `subject: str | None` (`employee_record["id"]`)
- `ttft: float | None`, `ttft_rate_limit_wait: float`, `total: float`
- `calls: list[dict]` (`{"kind", "label", "start", "end"}`, relative to the turn start), `notes: list[dict]`
- `stages: dict[str, float]` (state `timings`), `usage: dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evals_trace.py
from __future__ import annotations

from pension_chat.agent.instrument import Call, Instrument
from pension_chat.evals.trace import TurnTrace, build_trace


def _done(**state) -> dict:
    base = {
        "retrieved_sections": [{"section": "1-160"}, {"section": "8-129"}],
        "guardrail_passed": True,
        "guardrail_report": {"passed": True, "hard_failures": [], "warnings": [],
                             "verified_citations": ["1-160"], "unverified_citations": [],
                             "unverified_numbers": []},
        "input_sources": {}, "assumptions": {}, "calculation_result": None,
        "employee_record": None, "in_scope": True,
        "loop_messages": [
            {"role": "assistant", "tool_calls": [{"function": {"name": "outline", "arguments": "{\"article\": \"8\"}"}}]},
        ],
        "tool_calls_spent": {"search_statute": 1, "outline": 1},
        "timings": {"investigate": 8.7}, "token_usage": {"calls": 2},
    }
    base.update(state)
    return {"type": "done", "state": base, "card": None, "scenarios": []}


def test_builds_from_events_and_instrument() -> None:
    inst = Instrument(calls=[Call("search", "q", 100.5, 101.0)],
                      notes=[(100.2, "rate_limited", "0.40")],
                      rendered={"1-160": ["(a)", "(c)"]})
    events = [
        (1.0, {"type": "progress", "text": "searching"}),
        (3.2, {"type": "token", "text": "Age 67 "}),
        (3.5, {"type": "token", "text": "with 10 years."}),
        (4.0, _done()),
    ]
    t = build_trace(case_id="S1-003", turn_index=0, question="q", employee=None,
                    events=events, instrument=inst, started=100.0)
    assert t.ttft == 3.2
    assert t.ttft_rate_limit_wait == 0.4
    assert t.final_answer == "Age 67 with 10 years." == t.streamed_answer
    assert t.retrieved == ["1-160", "8-129"]
    assert t.rendered == {"1-160": ["(a)", "(c)"]}
    assert t.verified_citations == ["1-160"]
    assert [x["name"] for x in t.tools] == ["search_statute", "outline"]
    assert t.calls == [{"kind": "search", "label": "q", "start": 0.5, "end": 1.0}]


def test_a_replacement_is_the_final_answer_and_its_codes_are_kept() -> None:
    events = [
        (2.0, {"type": "token", "text": "Section 8-129 says"}),
        (2.5, {"type": "replace", "text": "Hedge.", "reason": {"hard_failures": [{"code": "UNVERIFIED_CITATION"}]}}),
        (3.0, _done(guardrail_passed=False)),
    ]
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=events, instrument=Instrument(), started=0.0)
    assert t.final_answer == "Hedge."
    assert t.streamed_answer == "Section 8-129 says"
    assert "UNVERIFIED_CITATION" in t.failure_codes


def test_an_error_event_is_recorded() -> None:
    events = [(1.0, {"type": "error", "message": "m", "detail": "RateLimitError: 429"})]
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=events, instrument=Instrument(), started=0.0)
    assert t.error == "RateLimitError: 429" and t.ttft is None


def test_round_trips_through_json() -> None:
    t = build_trace(case_id="x", turn_index=0, question="q", employee=None,
                    events=[(1.0, _done())], instrument=Instrument(), started=0.0)
    assert TurnTrace.from_dict(t.to_dict()) == t
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_evals_trace.py -v`
Expected: collection ERROR, `No module named 'pension_chat.evals.trace'`

- [ ] **Step 3: Implement `trace.py`**

```python
# src/pension_chat/evals/trace.py
"""Everything a turn did, in one record the checks and the judge read.

Checks and the judge read only this, never the live agent, so a saved trace
can be re-checked or re-judged without running a model call again.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from pension_chat.agent.instrument import Instrument


@dataclass
class TurnTrace:
    case_id: str
    turn_index: int
    question: str
    employee: str | None
    streamed_answer: str = ""
    final_answer: str = ""
    replacements: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    in_scope: bool = True
    tools: list[dict[str, Any]] = field(default_factory=list)
    retrieved: list[str] = field(default_factory=list)
    rendered: dict[str, list[str]] = field(default_factory=dict)
    verified_citations: list[str] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    unverified_numbers: list[Any] = field(default_factory=list)
    guardrail_passed: bool | None = None
    failure_codes: list[str] = field(default_factory=list)
    input_sources: dict[str, str] = field(default_factory=dict)
    assumptions: dict[str, Any] = field(default_factory=dict)
    calculation: bool = False
    card: dict[str, Any] | None = None
    subject: str | None = None
    ttft: float | None = None
    ttft_rate_limit_wait: float = 0.0
    total: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    stages: dict[str, float] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), default=str))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnTrace:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


def _codes(report: dict[str, Any] | None) -> list[str]:
    report = report or {}
    return [str(f.get("code")) for f in (report.get("hard_failures") or []) + (report.get("warnings") or [])
            if isinstance(f, dict) and f.get("code")]


def _tools(state: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    spent = dict(state.get("tool_calls_spent") or {})
    # The pre-search runs before the model, so it has no assistant message; take it from the spend.
    if spent.get("search_statute"):
        out.append({"name": "search_statute", "args": {"prefetch": True}})
    for message in state.get("loop_messages") or []:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"raw": fn.get("arguments")}
            out.append({"name": fn.get("name"), "args": args})
    return out


def build_trace(
    *, case_id: str, turn_index: int, question: str, employee: str | None,
    events: list[tuple[float, dict[str, Any]]], instrument: Instrument, started: float,
) -> TurnTrace:
    trace = TurnTrace(case_id=case_id, turn_index=turn_index, question=question, employee=employee)
    streamed: list[str] = []
    final: str | None = None
    for at, event in events:
        kind = event.get("type")
        if kind == "token":
            if trace.ttft is None:
                trace.ttft = at
            streamed.append(str(event.get("text") or ""))
        elif kind == "replace":
            final = str(event.get("text") or "")
            reason = event.get("reason") or {}
            trace.replacements.append({"reason": reason})
            trace.failure_codes += _codes(reason if isinstance(reason, dict) else {})
        elif kind == "error":
            trace.error = str(event.get("detail") or event.get("message") or "error")
        elif kind == "done":
            state = event.get("state") or {}
            report = state.get("guardrail_report") or {}
            trace.in_scope = bool(state.get("in_scope", True))
            trace.tools = _tools(state)
            trace.retrieved = [str(s.get("section")) for s in state.get("retrieved_sections") or [] if s.get("section")]
            trace.verified_citations = list(report.get("verified_citations") or [])
            trace.unverified_citations = list(report.get("unverified_citations") or [])
            trace.unverified_numbers = list(report.get("unverified_numbers") or [])
            trace.guardrail_passed = state.get("guardrail_passed")
            trace.failure_codes += _codes(report)
            trace.input_sources = dict(state.get("input_sources") or {})
            trace.assumptions = dict(state.get("assumptions") or {})
            trace.calculation = bool(state.get("calculation_result"))
            trace.card = event.get("card")
            trace.subject = (state.get("employee_record") or {}).get("id")
            trace.stages = dict(state.get("timings") or {})
            trace.usage = dict(state.get("token_usage") or {})
            trace.total = at
    trace.streamed_answer = "".join(streamed)
    trace.final_answer = final if final is not None else trace.streamed_answer
    trace.rendered = {k: list(v) for k, v in instrument.rendered.items()}
    trace.calls = [{"kind": c.kind, "label": c.label, "start": round(c.start - started, 4),
                    "end": round(c.end - started, 4)} for c in instrument.calls]
    trace.notes = [{"at": round(t - started, 4), "kind": k, "detail": d} for t, k, d in instrument.notes]
    trace.ttft_rate_limit_wait = sum(
        float(n["detail"] or 0) for n in trace.notes
        if n["kind"] == "rate_limited" and (trace.ttft is None or n["at"] <= trace.ttft)
    )
    trace.failure_codes = list(dict.fromkeys(trace.failure_codes))
    return trace
```

- [ ] **Step 4: Run the tests to watch them pass**

Run: `uv run pytest tests/test_evals_trace.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/evals/trace.py tests/test_evals_trace.py
git commit -m "Evals: TurnTrace, the one record checks and the judge read

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Code checks (the vetoes)

**Files:**
- Create: `src/pension_chat/evals/checks.py`
- Create: `tests/fixtures/eval_traces/tier2_before_fix.json`
- Test: `tests/test_evals_checks.py`

**Interfaces:**
- Consumes: `TurnTrace` (Task 6), `Turn` and `TTFT_CLASSES` (Task 5), `facts.aliases_of`, `retrieval.CROSS_CUTTING_ARTICLES`.
- Produces:
  - `@dataclass CheckResult(name: str, passed: bool, detail: str, gating: bool = True)`;
  - `run_checks(turn: Turn, trace: TurnTrace, *, latency_gate: bool = False) -> list[CheckResult]`.
  - `ttft_class` is `gating=False` unless `latency_gate`.

**Origin mapping for `figures_from`:**

| Case value | Allowed `input_sources` origins |
|---|---|
| `engine` | (the result itself; nothing extra) |
| `record` | `record` |
| `user` | `stated`, `hypothetical`, `stand_in` |
| `statute` | `statutory_parameter` |
| `assumed` | `assumed` |

`derived` is always allowed. `figures_from` also requires `unverified_numbers == []`.

- [ ] **Step 1: Write the trace fixture and the failing tests**

`tests/fixtures/eval_traces/tier2_before_fix.json` is the 2026-09-30 failure, reduced to what the checks read:

```json
{"case_id": "S1-003", "turn_index": 0, "question": "Under Article 8, what is the minimum age and service to retire for a Tier 2 member, and which section says so?",
 "employee": null,
 "streamed_answer": "Article 8's heading for age-and-service annuities is Section 8-129, but I could not establish the Tier 2 minimum age and service requirement from the text available to me.",
 "final_answer": "Sections 1-160 and 1-162 set requirements for certain Tier 2 members, but their text does not establish whether those provisions apply to every Article 8 Tier 2 member.",
 "replacements": [{"reason": {"excised": true}}],
 "retrieved": ["1-162", "1-160", "1-103.1", "1-117", "1-161"],
 "rendered": {"1-162": ["(a)", "(b)", "(c)"], "1-160": ["(a)", "(b)", "(b-5)", "(b-10)"]},
 "verified_citations": ["1-162"], "guardrail_passed": true,
 "tools": [{"name": "search_statute", "args": {"prefetch": true}}, {"name": "outline", "args": {"article": "8"}}],
 "ttft": 18.02, "total": 22.27}
```

```python
# tests/test_evals_checks.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pension_chat.evals.cases import Turn
from pension_chat.evals.checks import run_checks
from pension_chat.evals.trace import TurnTrace

BEFORE = TurnTrace.from_dict(json.loads(
    (Path(__file__).parent / "fixtures/eval_traces/tier2_before_fix.json").read_text()))


def _turn(**checks) -> Turn:
    return Turn(q="q", goal="g", reference="r", checks=checks)


def _one(turn: Turn, trace: TurnTrace, **kw):
    [result] = run_checks(turn, trace, **kw)
    return result


def _trace(**kw) -> TurnTrace:
    return TurnTrace(case_id="c", turn_index=0, question="q", employee=None, **kw)


def test_the_tier2_failure_is_caught_by_evidence_has() -> None:
    r = _one(_turn(evidence_has=["1-160(c)"]), BEFORE)
    assert not r.passed and "1-160(c)" in r.detail


def test_evidence_has_passes_once_c_reaches_the_model() -> None:
    after = TurnTrace.from_dict({**BEFORE.to_dict(), "rendered": {"1-160": ["(a)", "(b)", "(c)"]}})
    assert _one(_turn(evidence_has=["1-160(c)"]), after).passed
    assert _one(_turn(evidence_has=["1-160"]), after).passed


@pytest.mark.parametrize("check, value, verified, ok", [
    ("cites_any", ["1-160"], ["1-162"], False),
    ("cites_any", ["1-160", "8-129"], ["8-129"], True),
    ("cites_all", ["1-160", "8-129"], ["1-160"], False),
    ("cites_none_outside", ["8"], ["8-129", "1-160"], True),       # 1 and 20 are cross-cutting
    ("cites_none_outside", ["8"], ["8-129", "3-111"], False),
])
def test_citations(check, value, verified, ok) -> None:
    assert _one(_turn(**{check: value}), _trace(verified_citations=verified)).passed is ok


@pytest.mark.parametrize("answer, facts, ok", [
    ("Age 67 with at least 10 years.", ["67", "10 years"], True),
    ("You need $1,234.50 a month.", ["$1234.50"], True),
    ("Age 65 with 10 years.", ["67"], False),
    ("AGE  67   and  ten", ["age 67"], True),
])
def test_must_state_reads_the_final_answer_normalised(answer, facts, ok) -> None:
    assert _one(_turn(must_state=facts), _trace(final_answer=answer)).passed is ok


def test_must_not_state() -> None:
    assert not _one(_turn(must_not_state=["1-162"]), BEFORE).passed


def test_figures_from() -> None:
    ok = _trace(input_sources={"salary": "stated", "age": "stated", "fas": "derived"})
    assert _one(_turn(figures_from=["user", "engine"]), ok).passed
    bad = _trace(input_sources={"salary": "record"})
    assert not _one(_turn(figures_from=["user"]), bad).passed
    untraced = _trace(unverified_numbers=["$9,999"])
    assert not _one(_turn(figures_from=["engine"]), untraced).passed


def test_critical_inputs_are_never_assumed() -> None:
    t = _trace(input_sources={"hire_date": "assumed"})
    r = _one(_turn(critical_inputs=["hire_date"]), t)
    assert not r.passed and "hire_date" in r.detail
    assert _one(_turn(critical_inputs=["hire_date"]), _trace(input_sources={"hire_date": "stated"})).passed


def test_max_assumed_and_disclosure() -> None:
    three = _trace(input_sources={"a": "assumed", "b": "assumed", "c": "assumed"})
    assert not _one(_turn(max_assumed=2), three).passed
    hidden = _trace(failure_codes=["ASSUMPTION_NOT_DISCLOSED"])
    assert not _one(_turn(assumptions_disclosed=True), hidden).passed


def test_no_reask_flags_a_question_about_something_given() -> None:
    t = _trace(final_answer="Thanks. How old is the employee? I'll then compute it.")
    assert not _one(_turn(no_reask=["age"]), t).passed
    fine = _trace(final_answer="At 56 the employee is not yet eligible. When did they first join?")
    assert _one(_turn(no_reask=["age"]), fine).passed


def test_max_questions_ignores_quoted_text() -> None:
    t = _trace(final_answer='You asked "can she retire?" When did she join? Which date?')
    assert not _one(_turn(max_questions=1), t).passed
    assert _one(_turn(max_questions=2), t).passed


def test_refuses_and_calculation_and_verification() -> None:
    assert _one(_turn(refuses=True), _trace(in_scope=False)).passed
    assert not _one(_turn(calculation=True), _trace()).passed
    assert _one(_turn(no_calculation=True), _trace()).passed
    assert not _one(_turn(not_replaced=True), BEFORE).passed
    assert _one(_turn(verification_passed=True), BEFORE).passed


def test_worked_from_and_answers_from_article() -> None:
    t = _trace(card={"section": "8-138"}, verified_citations=["8-138", "1-160"])
    assert _one(_turn(worked_from_article="8"), t).passed
    assert _one(_turn(answers_from_article="8"), t).passed
    assert not _one(_turn(answers_from_article="8"), _trace(verified_citations=["11-134"])).passed


def test_about_employee() -> None:
    assert _one(_turn(about_employee="tier2_standard"), _trace(subject="tier2_standard")).passed


def test_ttft_class_is_reported_but_not_gating_until_the_latency_gate() -> None:
    slow = _trace(ttft=9.0)
    r = _one(_turn(ttft_class="simple"), slow)
    assert not r.passed and not r.gating
    assert _one(_turn(ttft_class="simple"), slow, latency_gate=True).gating


def test_ttft_excludes_rate_limit_waits() -> None:
    t = _trace(ttft=3.8, ttft_rate_limit_wait=1.2)
    assert _one(_turn(ttft_class="simple"), t).passed


def test_an_errored_turn_fails_every_check() -> None:
    t = _trace(error="boom")
    assert not _one(_turn(cites_any=["1-160"]), t).passed
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_evals_checks.py -v`
Expected: collection ERROR, `No module named 'pension_chat.evals.checks'`

- [ ] **Step 3: Implement `checks.py`**

```python
# src/pension_chat/evals/checks.py
"""Code checks over a TurnTrace -- the vetoes. Structure over substrings: they
read the verified citations, the subsections rendered to the model, and each
input's origin wherever those exist, and the answer text only for stated facts
and questions. Each returns the offending values, so a failure line says what
went wrong without opening the trace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pension_chat.agent.facts import aliases_of
from pension_chat.agent.retrieval import CROSS_CUTTING_ARTICLES
from pension_chat.evals.cases import TTFT_CLASSES, Turn
from pension_chat.evals.trace import TurnTrace


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    gating: bool = True


ORIGINS = {
    "engine": set(),
    "record": {"record"},
    "user": {"stated", "hypothetical", "stand_in"},
    "statute": {"statutory_parameter"},
    "assumed": {"assumed"},
}
ALWAYS_ALLOWED = {"derived"}

#: Words that mark a question as asking for this input. Matched only in
#: sentences that end with "?", outside quotation marks.
REASK_CUES = {
    "age": ("how old", "age", "date of birth", "born"),
    "salary": ("salary", "earn", "pay"),
    "service": ("years of service", "service credit", "how long"),
    "participation_date": ("when did", "join", "hire date", "hired", "became a member", "first became"),
    "tier": ("which tier", "what tier"),
}


def _article(section: str) -> str:
    return section.split("-", 1)[0]


def _norm(text: str) -> str:
    text = text.lower().replace("$", "")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    return " ".join(text.split())


def _questions(answer: str) -> list[str]:
    unquoted = re.sub(r"[\"“][^\"”]*[\"”]", " ", answer)
    return [s.strip() for s in re.findall(r"[^.?!\n]*\?", unquoted) if s.strip()]


def _check(name: str, value, trace: TurnTrace, latency_gate: bool) -> CheckResult:
    verified = trace.verified_citations
    if name == "cites_any":
        return CheckResult(name, any(s in verified for s in value), f"verified {verified}")
    if name == "cites_all":
        missing = [s for s in value if s not in verified]
        return CheckResult(name, not missing, f"missing {missing}")
    if name == "cites_none_outside":
        allowed = set(value) | set(CROSS_CUTTING_ARTICLES)
        outside = [s for s in verified if _article(s) not in allowed]
        return CheckResult(name, not outside, f"outside {outside}")
    if name == "evidence_has":
        missing = []
        for item in value:
            m = re.fullmatch(r"([\dA-Z]+-[\d.]+[a-z]?)(\(.+\))?", item)
            section, sub = (m.group(1), m.group(2)) if m else (item, None)
            got = trace.rendered.get(section)
            if got is None or (sub and sub not in got):
                missing.append(item)
        return CheckResult(name, not missing, f"not rendered to the model: {missing}; rendered {trace.rendered}")
    if name in ("must_state", "must_not_state"):
        answer = _norm(trace.final_answer)
        hits = [f for f in value if _norm(str(f)) in answer]
        if name == "must_state":
            missing = [f for f in value if f not in hits]
            return CheckResult(name, not missing, f"not stated: {missing}")
        return CheckResult(name, not hits, f"stated: {hits}")
    if name == "calculation":
        return CheckResult(name, trace.calculation is value, f"calculation={trace.calculation}")
    if name == "no_calculation":
        return CheckResult(name, not trace.calculation, f"calculation={trace.calculation}")
    if name == "figures_from":
        allowed = set().union(*(ORIGINS[v] for v in value)) | ALWAYS_ALLOWED
        bad = {k: o for k, o in trace.input_sources.items() if o not in allowed}
        ok = not bad and not trace.unverified_numbers
        return CheckResult(name, ok, f"origins {bad}; untraced {trace.unverified_numbers}")
    if name == "worked_from_article":
        section = str((trace.card or {}).get("section") or "")
        return CheckResult(name, _article(section) == value, f"worked from {section or 'nothing'}")
    if name == "answers_from_article":
        allowed = {value} | set(CROSS_CUTTING_ARTICLES)
        section = str((trace.card or {}).get("section") or "")
        outside = [s for s in verified + ([section] if section else []) if _article(s) not in allowed]
        return CheckResult(name, bool(verified or section) and not outside, f"outside {outside}")
    if name == "critical_inputs":
        assumed = [n for n in value for alias in aliases_of(n)
                   if trace.input_sources.get(alias) == "assumed" or alias in trace.assumptions]
        return CheckResult(name, not assumed, f"assumed critical inputs: {sorted(set(assumed))}")
    if name == "max_assumed":
        count = sum(1 for o in trace.input_sources.values() if o == "assumed")
        return CheckResult(name, count <= value, f"{count} assumed")
    if name == "assumptions_disclosed":
        bad = [c for c in trace.failure_codes if "assumption" in c.lower()]
        return CheckResult(name, not bad, f"codes {bad}")
    if name == "no_reask":
        asked = [n for n in value for q in _questions(trace.final_answer)
                 if any(cue in q.lower() for cue in REASK_CUES.get(n, (n.replace("_", " "),)))]
        return CheckResult(name, not asked, f"asked again for {sorted(set(asked))}")
    if name == "max_questions":
        count = len(_questions(trace.final_answer))
        return CheckResult(name, count <= value, f"{count} questions")
    if name == "refuses":
        return CheckResult(name, (not trace.in_scope) is value, f"in_scope={trace.in_scope}")
    if name == "verification_passed":
        return CheckResult(name, bool(trace.guardrail_passed) is value, f"passed={trace.guardrail_passed}")
    if name == "not_replaced":
        return CheckResult(name, (not trace.replacements) is value, f"replacements {trace.replacements}")
    if name == "about_employee":
        return CheckResult(name, trace.subject == value, f"subject {trace.subject}")
    if name == "ttft_class":
        limit = TTFT_CLASSES[value]
        ttft = None if trace.ttft is None else trace.ttft - trace.ttft_rate_limit_wait
        return CheckResult(name, ttft is not None and ttft <= limit,
                           f"ttft {ttft if ttft is None else round(ttft, 2)}s vs {limit}s", gating=latency_gate)
    raise KeyError(name)


def run_checks(turn: Turn, trace: TurnTrace, *, latency_gate: bool = False) -> list[CheckResult]:
    if trace.error:
        return [CheckResult(name, False, f"turn errored: {trace.error}") for name in turn.checks] or [
            CheckResult("error", False, trace.error)]
    return [_check(name, value, trace, latency_gate) for name, value in turn.checks.items()]
```

- [ ] **Step 4: Run the tests to watch them pass**

Run: `uv run pytest tests/test_evals_checks.py -v`
Expected: all passed. If `test_critical_inputs...` fails because `aliases_of("hire_date")` does not include the engine's participation-date name, add that name to the test's expected aliases after reading `facts.ALIAS_GROUPS`. Record the ruling in the ledger.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/evals/checks.py tests/test_evals_checks.py tests/fixtures/eval_traces
git commit -m "Evals: code checks over the trace; the 1-160(c) failure is caught by evidence_has

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Timing (probe, in-region estimate, stage breakdown)

**Files:**
- Create: `src/pension_chat/evals/timing.py`
- Test: `tests/test_evals_timing.py`

**Interfaces:**
- Consumes: `TurnTrace.calls`, `.ttft` and `.ttft_rate_limit_wait`.
- Produces:
  - `KIND_ENDPOINT = {"model": "openai", "embedding": "openai", "search": "search", "postgres": "postgres", "postgres_connect": "postgres"}`;
  - `Probe = dict[str, dict[str, float]]`, mapping each endpoint to `{"cold": s, "warm": s}`;
  - `async probe(settings) -> Probe`, which raises `ProbeError(endpoint)` when an endpoint is unreachable;
  - `estimate_in_region(trace, probe, in_region_rtt=0.002) -> float | None`;
  - `stage_breakdown(trace) -> dict[str, float]`, with keys `model`, `embedding`, `search`, `postgres` and `unattributed`, all measured before the first answer token.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evals_timing.py
from __future__ import annotations

import pytest

from pension_chat.evals.timing import estimate_in_region, stage_breakdown
from pension_chat.evals.trace import TurnTrace

PROBE = {"openai": {"cold": 0.9, "warm": 0.25}, "search": {"cold": 0.8, "warm": 0.25},
         "postgres": {"cold": 1.5, "warm": 0.25}}


def _t(calls, ttft=10.0, wait=0.0) -> TurnTrace:
    return TurnTrace(case_id="c", turn_index=0, question="q", employee=None,
                     ttft=ttft, ttft_rate_limit_wait=wait,
                     calls=[{"kind": k, "label": "", "start": s, "end": e} for k, s, e in calls])


def test_sequential_calls_each_lose_their_network_overhead() -> None:
    t = _t([("search", 0.0, 1.0), ("model", 1.0, 3.0)])
    assert estimate_in_region(t, PROBE) == pytest.approx(10.0 - 2 * 0.248)


def test_parallel_calls_count_once_at_the_largest_overhead() -> None:
    t = _t([("search", 0.0, 1.0), ("embedding", 0.1, 0.6)])
    assert estimate_in_region(t, PROBE) == pytest.approx(10.0 - 0.248)


def test_a_new_connection_loses_its_setup() -> None:
    t = _t([("postgres_connect", 0.0, 1.5)])
    assert estimate_in_region(t, PROBE) == pytest.approx(10.0 - (1.5 - 0.006))


def test_calls_after_the_first_token_do_not_count() -> None:
    t = _t([("model", 11.0, 12.0)])
    assert estimate_in_region(t, PROBE) == pytest.approx(10.0)


def test_rate_limit_waits_come_off_too() -> None:
    assert estimate_in_region(_t([], wait=1.5), PROBE) == pytest.approx(8.5)


def test_no_first_token_means_no_estimate() -> None:
    assert estimate_in_region(_t([], ttft=None), PROBE) is None


def test_stage_breakdown_counts_overlap_once_and_names_the_gap() -> None:
    t = _t([("search", 0.0, 1.0), ("embedding", 0.5, 1.5), ("model", 2.0, 5.0)], ttft=6.0)
    stages = stage_breakdown(t)
    assert stages["model"] == pytest.approx(3.0)
    assert stages["search"] == pytest.approx(1.0)
    assert stages["unattributed"] == pytest.approx(6.0 - 4.5)   # union of [0,1.5] and [2,5] is 4.5 s
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_evals_timing.py -v`
Expected: collection ERROR

- [ ] **Step 3: Implement `timing.py`**

```python
# src/pension_chat/evals/timing.py
"""Time to first answer token: measured here, and estimated for in-region.

Every remote call on the path to the first answer token carries this
machine's round trip (about 245 ms from India to Central US on 2026-09-29) where
an in-region caller would pay about 2 ms. The probe measures that round trip
per endpoint, cold (DNS+TCP+TLS) and warm; the estimate takes the difference
off each call before the first token, counting calls that ran in parallel
once. An estimate is labelled as one, and the latency gate is provisional,
until a run inside the client's region calibrates it.
"""

from __future__ import annotations

import time
from typing import Any

from pension_chat.evals.trace import TurnTrace

KIND_ENDPOINT = {"model": "openai", "embedding": "openai", "search": "search",
                 "postgres": "postgres", "postgres_connect": "postgres"}
Probe = dict[str, dict[str, float]]


class ProbeError(RuntimeError):
    pass


async def probe(settings: Any) -> Probe:
    import asyncpg
    import httpx

    out: Probe = {}
    for endpoint, url in (("openai", settings.azure_openai_endpoint), ("search", settings.azure_search_endpoint)):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                t = time.perf_counter()
                await client.get(url)
                cold = time.perf_counter() - t
                t = time.perf_counter()
                await client.get(url)
                out[endpoint] = {"cold": cold, "warm": time.perf_counter() - t}
        except Exception as exc:
            raise ProbeError(f"{endpoint} unreachable at {url}: {exc}") from exc
    try:
        t = time.perf_counter()
        conn = await asyncpg.connect(host=settings.pghost, port=settings.pgport, user=settings.pguser,
                                     password=settings.pgpassword, database=settings.pgdatabase, ssl="require")
        cold = time.perf_counter() - t
        t = time.perf_counter()
        await conn.fetchval("select 1")
        out["postgres"] = {"cold": cold, "warm": time.perf_counter() - t}
        await conn.close()
    except Exception as exc:
        raise ProbeError(f"postgres unreachable at {settings.pghost}: {exc}") from exc
    return out


def _before_first_token(trace: TurnTrace) -> list[dict[str, Any]]:
    if trace.ttft is None:
        return []
    return sorted((c for c in trace.calls if c["start"] < trace.ttft), key=lambda c: c["start"])


def _overhead(call: dict[str, Any], probe: Probe, rtt: float) -> float:
    measured = probe.get(KIND_ENDPOINT.get(call["kind"], ""), {})
    if call["kind"].endswith("_connect"):
        return max(0.0, (call["end"] - call["start"]) - 3 * rtt)
    return max(0.0, measured.get("warm", rtt) - rtt)


def estimate_in_region(trace: TurnTrace, probe: Probe, in_region_rtt: float = 0.002) -> float | None:
    if trace.ttft is None:
        return None
    saved, group_end, group_max = 0.0, None, 0.0
    for call in _before_first_token(trace):
        cost = _overhead(call, probe, in_region_rtt)
        if group_end is not None and call["start"] < group_end:
            group_end, group_max = max(group_end, call["end"]), max(group_max, cost)
            continue
        saved += group_max
        group_end, group_max = call["end"], cost
    saved += group_max
    return max(0.0, trace.ttft - trace.ttft_rate_limit_wait - saved)


def _union(intervals: list[tuple[float, float]]) -> float:
    total, end = 0.0, None
    for start, stop in sorted(intervals):
        if end is None or start > end:
            total += stop - start
            end = stop
        elif stop > end:
            total += stop - end
            end = stop
    return total


def stage_breakdown(trace: TurnTrace) -> dict[str, float]:
    if trace.ttft is None:
        return {}
    calls = _before_first_token(trace)
    clip = lambda c: (c["start"], min(c["end"], trace.ttft))  # noqa: E731
    stages = {}
    for kind in ("model", "embedding", "search", "postgres"):
        stages[kind] = _union([clip(c) for c in calls if c["kind"].startswith(kind)])
    stages["unattributed"] = max(0.0, trace.ttft - _union([clip(c) for c in calls]))
    return stages
```

- [ ] **Step 4: Run the tests to watch them pass**

Run: `uv run pytest tests/test_evals_timing.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/evals/timing.py tests/test_evals_timing.py
git commit -m "Evals: per-stage TTFT and an estimated in-region figure from a network probe

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The judge (rubric, verdict, cache, calibration)

**Files:**
- Create: `src/pension_chat/evals/judge.py`, `evals/rubric.yaml`, `evals/labels/README.md`
- Test: `tests/test_evals_judge.py`

**Interfaces:**
- Consumes: `Turn` and `BEHAVIOURS` (Task 5), `TurnTrace` (Task 6), `llm.complete(node=..., messages=..., json_mode=True, reasoning_effort="low")`.
- Produces:
  - `@dataclass Verdict(goal: str, relevant: bool, within_boundary: bool, behaviour: str, voice: int, hedging: int, clarity: int, quotes: dict[str, str], reason: str)`;
  - `Verdict.passes(acceptable: list[str]) -> bool`;
  - `rubric_version(path) -> str`, the first 12 hex characters of the SHA-256 of the file;
  - `async judge_turn(turn, trace, history: list[tuple[str, str]], *, rubric_path, cache_dir, complete=llm.complete) -> Verdict | None`. It returns `None` when the verdict is unjudged;
  - `calibration(labels_dir, verdicts: dict[str, Verdict]) -> tuple[int, int]` (agreed, total);
  - `is_calibrated(eval_root, rubric_path) -> bool`, which reads `evals/calibration.json`.

- [ ] **Step 1: Write `evals/rubric.yaml`**

```yaml
# The judge grades against the turn's own goal and reference -- never its own opinion.
# Editing this file changes the rubric version, which invalidates every cached verdict
# and requires re-calibration before the judge gates again.
instructions: |
  You grade one turn of a conversation between an employer (HR or pension staff at a
  Municipal Employees' Annuity and Benefit Fund (MEABF, Illinois Pension Code Article 8)
  employer) and an assistant. The assistant may answer only within Article 8 and the
  sections Article 8 references (Articles 1 and 20 apply to every member).
  You are given what the user wanted (GOAL), the facts a correct answer rests on
  (REFERENCE), the statute evidence the assistant had, and the ANSWER.
  Judge only against GOAL and REFERENCE. A different wording of a correct fact is correct.
  Quote the answer for every verdict. Reply with JSON only.
goal: |
  "yes": the answer does what GOAL asks and agrees with REFERENCE on every fact it states.
  "partly": some of GOAL is done, or a fact is missing or hedged where REFERENCE settles it.
  "no": GOAL is not done, or a stated fact contradicts REFERENCE.
  Outside the boundary, "yes" means: declined plainly and offered what can be answered.
relevant: |
  true when the answer addresses this question, not a neighbouring one.
within_boundary: |
  true when the answer rests only on Article 8 and the sections it references, and
  applies no other fund's law to this employee.
behaviour: |
  one of: answer | ask_critical | branch_on_critical | ask_other | refuse.
  ask_critical = asks for an input that decides which rule applies (e.g. when the employee
  first became a member). branch_on_critical = answers each possibility of such an input.
voice: "1-5: speaks to an employer about their employee (5), or to the member as 'you' (1)."
hedging: "1-5: 5 = no hedging where the law is clear; 1 = hedges settled law."
clarity: "1-5: 5 = direct, ordered, figures and sections easy to find."
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_evals_judge.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pension_chat.evals.cases import Turn
from pension_chat.evals.judge import Verdict, judge_turn, rubric_version
from pension_chat.evals.trace import TurnTrace

RUBRIC = Path(__file__).parents[1] / "evals" / "rubric.yaml"
TURN = Turn(q="Tier 2 age?", goal="The age and section.", reference="67 with 10 years, §1-160(c).")
TRACE = TurnTrace(case_id="S1-003", turn_index=0, question="Tier 2 age?", employee=None,
                  final_answer="Age 67 with 10 years under Section 1-160(c).")
GOOD = {"goal": "yes", "relevant": True, "within_boundary": True, "behaviour": "answer",
        "voice": 4, "hedging": 5, "clarity": 5, "quotes": {"goal": "Age 67 with 10 years"}, "reason": "ok"}


def _fake(responses: list[str]):
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    return complete, calls


@pytest.mark.asyncio
async def test_a_verdict_is_parsed_and_cached(tmp_path: Path) -> None:
    complete, calls = _fake([json.dumps(GOOD)])
    v = await judge_turn(TURN, TRACE, [], rubric_path=RUBRIC, cache_dir=tmp_path, complete=complete)
    assert v == Verdict(**GOOD) and v.passes([])
    again = await judge_turn(TURN, TRACE, [], rubric_path=RUBRIC, cache_dir=tmp_path, complete=complete)
    assert again == v and len(calls) == 1          # served from the cache


@pytest.mark.asyncio
async def test_the_prompt_carries_goal_reference_and_answer(tmp_path: Path) -> None:
    complete, calls = _fake([json.dumps(GOOD)])
    await judge_turn(TURN, TRACE, [("earlier q", "earlier a")], rubric_path=RUBRIC, cache_dir=tmp_path, complete=complete)
    prompt = json.dumps(calls[0]["messages"])
    for piece in ("The age and section.", "67 with 10 years", "Age 67 with 10 years under", "earlier q"):
        assert piece in prompt
    assert calls[0]["json_mode"] is True


@pytest.mark.asyncio
async def test_malformed_twice_is_unjudged(tmp_path: Path) -> None:
    complete, calls = _fake(["not json", "{\"goal\": \"maybe\"}"])
    assert await judge_turn(TURN, TRACE, [], rubric_path=RUBRIC, cache_dir=tmp_path, complete=complete) is None
    assert len(calls) == 2


def test_passes_needs_goal_yes_and_an_acceptable_behaviour() -> None:
    assert not Verdict(**{**GOOD, "goal": "partly"}).passes([])
    assert not Verdict(**{**GOOD, "relevant": False}).passes([])
    assert not Verdict(**{**GOOD, "behaviour": "answer"}).passes(["ask_critical", "branch_on_critical"])
    assert Verdict(**{**GOOD, "behaviour": "branch_on_critical"}).passes(["ask_critical", "branch_on_critical"])


def test_editing_the_rubric_changes_its_version(tmp_path: Path) -> None:
    p = tmp_path / "r.yaml"
    p.write_text("a: 1")
    first = rubric_version(p)
    p.write_text("a: 2")
    assert rubric_version(p) != first
```

- [ ] **Step 3: Run the tests to watch them fail**

Run: `uv run pytest tests/test_evals_judge.py -v`
Expected: collection ERROR

- [ ] **Step 4: Implement `judge.py`**

```python
# src/pension_chat/evals/judge.py
"""One reference-guided judge call per turn.

It decides three things -- whether the user's goal was accomplished, whether
the answer was relevant, and whether it stayed within the boundary -- against
the turn's own GOAL and REFERENCE, and quotes the answer for each. It runs only
after every code check passed; code can veto a turn, and the judge cannot
overrule it. It gates only once calibrated against the product owner's labels
(>=18 of 20 for this rubric version); until then it is reported alone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pension_chat.evals.cases import BEHAVIOURS, Turn
from pension_chat.evals.trace import TurnTrace

GOALS = ("yes", "partly", "no")


@dataclass
class Verdict:
    goal: str
    relevant: bool
    within_boundary: bool
    behaviour: str
    voice: int
    hedging: int
    clarity: int
    quotes: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    def passes(self, acceptable: list[str]) -> bool:
        return (self.goal == "yes" and self.relevant and self.within_boundary
                and (not acceptable or self.behaviour in acceptable))


def rubric_version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _parse(text: str) -> Verdict | None:
    try:
        raw = json.loads(text)
        v = Verdict(
            goal=str(raw["goal"]), relevant=bool(raw["relevant"]),
            within_boundary=bool(raw["within_boundary"]), behaviour=str(raw["behaviour"]),
            voice=int(raw["voice"]), hedging=int(raw["hedging"]), clarity=int(raw["clarity"]),
            quotes=dict(raw.get("quotes") or {}), reason=str(raw.get("reason") or ""),
        )
    except (ValueError, KeyError, TypeError):
        return None
    if v.goal not in GOALS or v.behaviour not in BEHAVIOURS:
        return None
    return v


def _messages(rubric: dict[str, Any], turn: Turn, trace: TurnTrace,
              history: list[tuple[str, str]]) -> list[dict[str, str]]:
    criteria = "\n".join(f"{k}: {rubric[k]}" for k in
                         ("goal", "relevant", "within_boundary", "behaviour", "voice", "hedging", "clarity"))
    earlier = "\n".join(f"USER: {q}\nASSISTANT: {a}" for q, a in history) or "(none)"
    evidence = ", ".join(f"{s}{v}" for s, v in trace.rendered.items()) or "(none recorded)"
    user = (
        f"CONVERSATION SO FAR:\n{earlier}\n\nQUESTION:\n{trace.question}\n\n"
        f"GOAL:\n{turn.goal}\n\nREFERENCE:\n{turn.reference}\n\n"
        f"STATUTE EVIDENCE THE ASSISTANT HAD (sections and subsections): {evidence}\n\n"
        f"ANSWER:\n{trace.final_answer}\n\n"
        'Reply as JSON: {"goal": "yes|partly|no", "relevant": bool, "within_boundary": bool, '
        '"behaviour": "...", "voice": 1-5, "hedging": 1-5, "clarity": 1-5, '
        '"quotes": {"goal": "...", "relevant": "...", "within_boundary": "..."}, "reason": "..."}'
    )
    return [{"role": "system", "content": f"{rubric['instructions']}\n\n{criteria}"},
            {"role": "user", "content": user}]


async def judge_turn(
    turn: Turn, trace: TurnTrace, history: list[tuple[str, str]], *,
    rubric_path: Path, cache_dir: Path,
    complete: Callable[..., Awaitable[str]] | None = None,
) -> Verdict | None:
    if complete is None:
        from pension_chat.agent.llm import complete as llm_complete
        complete = llm_complete
    version = rubric_version(rubric_path)
    key = hashlib.sha256(json.dumps(
        [version, turn.goal, turn.reference, trace.question, trace.final_answer, history]).encode()).hexdigest()
    cached = cache_dir / f"{key}.json"
    if cached.exists():
        return Verdict(**json.loads(cached.read_text(encoding="utf-8")))
    messages = _messages(yaml.safe_load(rubric_path.read_text(encoding="utf-8")), turn, trace, history)
    for _ in range(2):
        verdict = _parse(await complete(node="eval_judge", messages=messages, json_mode=True,
                                        reasoning_effort="low"))
        if verdict is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(asdict(verdict)), encoding="utf-8")
            return verdict
    return None


def calibration(labels_dir: Path, verdicts: dict[str, Verdict]) -> tuple[int, int]:
    """Labels are YAML files of {id: "<case>#<turn>", goal, relevant, within_boundary}."""
    agreed = total = 0
    for path in sorted(labels_dir.glob("*.yaml")):
        for label in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            verdict = verdicts.get(label["id"])
            if verdict is None:
                continue
            total += 1
            agreed += (verdict.goal == label["goal"] and verdict.relevant == label["relevant"]
                       and verdict.within_boundary == label["within_boundary"])
    return agreed, total


def is_calibrated(eval_root: Path, rubric_path: Path) -> bool:
    path = eval_root / "calibration.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data.get("rubric_version") == rubric_version(rubric_path)
            and data.get("total", 0) >= 20 and data.get("agreed", 0) >= 18)
```

Write `evals/labels/README.md`:

```markdown
# Judge calibration labels

Label about 20 turns from a saved run (`evals/runs/<ts>/traces/*.json`). Each file is a YAML list:

    - id: "S1-003#0"          # case id # turn index
      goal: yes               # yes | partly | no
      relevant: true
      within_boundary: true

Then run `uv run python scripts/run_evals.py --calibrate --from evals/runs/<ts>`.
The judge gates pass/fail only when it agrees on at least 18 of 20 for the current rubric version.
```

- [ ] **Step 5: Run the tests to watch them pass**

Run: `uv run pytest tests/test_evals_judge.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/pension_chat/evals/judge.py evals/rubric.yaml evals/labels/README.md tests/test_evals_judge.py
git commit -m "Evals: a reference-guided judge, cached by rubric version, gating only once calibrated

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The runner

**Files:**
- Create: `src/pension_chat/evals/runner.py`
- Test: `tests/test_evals_runner.py`

**Interfaces:**
- Consumes:
  - `use_graph` and `build_loop_graph` (Task 2), `use_provider` (Task 1), `instrument.begin` (Task 3);
  - `build_trace` (Task 6), `run_checks` (Task 7), `judge_turn` (Task 9), `estimate_in_region` and `stage_breakdown` (Task 8).
- Produces:
  - `@dataclass TurnResult(case_id, turn_index, trace: TurnTrace, checks: list[CheckResult], verdict: Verdict | None, status: str, estimate: float | None, stages: dict)`;
  - `status` is one of `pass`, `fail`, `pending`, `after_failure`, `unjudged` or `error`;
  - `@dataclass ConversationResult(case: Case, turns: list[TurnResult], flaky: bool = False)`, with the property `passed`;
  - `async run_conversation(case, *, fixtures, probe, options, agent=run_agent_stream) -> ConversationResult`;
  - `async run_suite(cases, *, fixtures, probe, options) -> list[ConversationResult]`, which runs concurrently and re-runs each failed conversation once;
  - `@dataclass RunOptions(concurrency=8, judge=True, judge_gates=False, latency_gate=False, rubric_path=Path("evals/rubric.yaml"), cache_dir=Path("evals/.judge_cache"), rate_limit_retries=2)`.

**Status rules, in order:**
- the case is pending → `pending`. Checks run and are shown; there is no judge call.
- an earlier turn in the conversation failed → `after_failure`. Checks run; there is no judge call.
- the trace has an error → `error`.
- any gating check failed → `fail`.
- the judge is on and returns `None` → `unjudged`.
- the judge gates and `not verdict.passes(turn.acceptable)` → `fail`.
- otherwise → `pass`.

- [ ] **Step 1: Write the failing tests**

The tests use a fake agent: an async generator with the `run_agent_stream` signature that returns scripted events.

```python
# tests/test_evals_runner.py
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pension_chat.agent import instrument
from pension_chat.evals.cases import Case, Turn
from pension_chat.evals.runner import RunOptions, run_conversation, run_suite


def _done(answer_sections=("1-160",)) -> dict:
    return {"type": "done", "card": None, "scenarios": [], "state": {
        "retrieved_sections": [{"section": s} for s in answer_sections], "guardrail_passed": True,
        "guardrail_report": {"passed": True, "verified_citations": list(answer_sections)},
        "in_scope": True, "timings": {}, "token_usage": {}}}


def _agent(script: dict[str, list[str]], seen: list | None = None):
    """script: question -> the citations its answer verifies (empty list = a failing answer)."""
    async def agent(question, session_id, employee_id=None, history=None, thread_owner=None):
        if seen is not None:
            seen.append((session_id, employee_id, question))
        async with instrument.timed("model", question):
            await asyncio.sleep(0.001)
        yield {"type": "token", "text": f"answer to {question}"}
        yield _done(tuple(script[question]))
    return agent


def _case(cid: str, qs: list[str], **kw) -> Case:
    turns = [Turn(q=q, goal="g", reference="r", checks={"cites_any": ["1-160"]}) for q in qs]
    return Case(id=cid, family="S1", areas=["retrieval"], turns=turns, path=Path("x.yaml"), **kw)


OPTS = RunOptions(judge=False)


@pytest.mark.asyncio
async def test_turns_run_in_order_on_one_thread() -> None:
    seen: list = []
    result = await run_conversation(_case("A", ["q1", "q2"]), fixtures={}, probe={},
                                    options=OPTS, agent=_agent({"q1": ["1-160"], "q2": ["1-160"]}, seen))
    assert [s[2] for s in seen] == ["q1", "q2"]
    assert len({s[0] for s in seen}) == 1
    assert [t.status for t in result.turns] == ["pass", "pass"] and result.passed


@pytest.mark.asyncio
async def test_turns_after_a_failure_are_marked_not_scored() -> None:
    result = await run_conversation(_case("A", ["q1", "q2", "q3"]), fixtures={}, probe={},
                                    options=OPTS, agent=_agent({"q1": ["1-160"], "q2": [], "q3": ["1-160"]}))
    assert [t.status for t in result.turns] == ["pass", "fail", "after_failure"]


@pytest.mark.asyncio
async def test_a_pending_case_runs_but_is_marked_pending() -> None:
    result = await run_conversation(_case("A", ["q1"], needs=["find_employee"]), fixtures={}, probe={},
                                    options=OPTS, agent=_agent({"q1": ["1-160"]}))
    assert result.turns[0].status == "pending"


@pytest.mark.asyncio
async def test_concurrent_conversations_do_not_see_each_others_calls() -> None:
    results = await run_suite([_case("A", ["qa"]), _case("B", ["qb"])], fixtures={}, probe={},
                              options=RunOptions(judge=False, concurrency=2),
                              agent=_agent({"qa": ["1-160"], "qb": ["1-160"]}))
    labels = {r.case.id: [c["label"] for c in r.turns[0].trace.calls] for r in results}
    assert labels == {"A": ["qa"], "B": ["qb"]}


@pytest.mark.asyncio
async def test_a_failed_conversation_is_rerun_once_and_marked_flaky_if_it_then_passes() -> None:
    answers = iter([[], ["1-160"]])

    async def agent(question, session_id, employee_id=None, history=None, thread_owner=None):
        yield {"type": "token", "text": "x"}
        yield _done(tuple(next(answers)))

    [result] = await run_suite([_case("A", ["q1"])], fixtures={}, probe={}, options=OPTS, agent=agent)
    assert result.passed and result.flaky


@pytest.mark.asyncio
async def test_a_rate_limited_turn_is_retried_not_failed() -> None:
    attempts = {"n": 0}

    async def agent(question, session_id, employee_id=None, history=None, thread_owner=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            yield {"type": "error", "message": "m", "detail": "RateLimitError: Error code: 429"}
            return
        yield {"type": "token", "text": "x"}
        yield _done()

    result = await run_conversation(_case("A", ["q1"]), fixtures={}, probe={},
                                    options=RunOptions(judge=False, rate_limit_backoff=0.0), agent=agent)
    assert result.turns[0].status == "pass" and attempts["n"] == 2
```

Add `rate_limit_backoff: float = 10.0` to `RunOptions`; the test passes 0.

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_evals_runner.py -v`
Expected: collection ERROR

- [ ] **Step 3: Implement `runner.py`**

```python
# src/pension_chat/evals/runner.py
"""Run conversations through the real agent, concurrently, one thread each.

Each conversation gets its own in-memory graph, its own fixture provider, and a
fresh instrument per turn -- all context variables set inside its own task, so
eight conversations running at once never see each other's state. Turns within
a conversation run in order, because turn two resumes from turn one.

A rate-limited turn is retried after a backoff, not failed: a 429 is capacity,
not quality. A conversation that fails is re-run once; failed-then-passed is
reported as flaky rather than as a regression.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from pension_chat.agent import instrument
from pension_chat.evals.cases import Case
from pension_chat.evals.checks import CheckResult, run_checks
from pension_chat.evals.fixtures import FixtureProvider
from pension_chat.evals.judge import Verdict, judge_turn
from pension_chat.evals.timing import Probe, estimate_in_region, stage_breakdown
from pension_chat.evals.trace import TurnTrace, build_trace
from pension_chat.records import use_provider


@dataclass
class RunOptions:
    concurrency: int = 8
    judge: bool = True
    judge_gates: bool = False
    latency_gate: bool = False
    rubric_path: Path = Path("evals/rubric.yaml")
    cache_dir: Path = Path("evals/.judge_cache")
    rate_limit_retries: int = 2
    rate_limit_backoff: float = 10.0


@dataclass
class TurnResult:
    case_id: str
    turn_index: int
    trace: TurnTrace
    checks: list[CheckResult]
    verdict: Verdict | None
    status: str
    estimate: float | None
    stages: dict[str, float]


@dataclass
class ConversationResult:
    case: Case
    turns: list[TurnResult] = field(default_factory=list)
    flaky: bool = False

    @property
    def passed(self) -> bool:
        return all(t.status in ("pass", "pending") for t in self.turns)


def _rate_limited(trace: TurnTrace) -> bool:
    return bool(trace.error) and ("429" in trace.error or "RateLimit" in trace.error)


async def _one_turn(agent, question, thread, employee, case_id, index) -> TurnTrace:
    inst = instrument.begin()
    started = time.perf_counter()
    events: list[tuple[float, dict[str, Any]]] = []
    async for event in agent(question, thread, employee):
        events.append((time.perf_counter() - started, event))
    return build_trace(case_id=case_id, turn_index=index, question=question, employee=employee,
                       events=events, instrument=inst, started=started)


async def run_conversation(case: Case, *, fixtures: dict, probe: Probe, options: RunOptions,
                           agent=None) -> ConversationResult:
    from pension_chat.agent.graph import build_loop_graph, run_agent_stream, use_graph

    agent = agent or run_agent_stream
    thread = f"eval-{case.id}-{uuid.uuid4().hex[:8]}"
    result = ConversationResult(case)
    history: list[tuple[str, str]] = []
    failed_before = False
    with use_graph(build_loop_graph(InMemorySaver())), use_provider(FixtureProvider(fixtures)):
        for index, turn in enumerate(case.turns):
            employee = turn.employee or case.employee
            for attempt in range(options.rate_limit_retries + 1):
                trace = await _one_turn(agent, turn.q, thread, employee, case.id, index)
                if not _rate_limited(trace) or attempt == options.rate_limit_retries:
                    break
                await asyncio.sleep(options.rate_limit_backoff * (attempt + 1))
            checks = run_checks(turn, trace, latency_gate=options.latency_gate)
            gating_failed = any(not c.passed and c.gating for c in checks)
            verdict: Verdict | None = None
            if case.pending:
                status = "pending"
            elif failed_before:
                status = "after_failure"
            elif trace.error:
                status = "error"
            elif gating_failed:
                status = "fail"
            else:
                if options.judge:
                    verdict = await judge_turn(turn, trace, history, rubric_path=options.rubric_path,
                                               cache_dir=options.cache_dir)
                if options.judge and verdict is None:
                    status = "unjudged"
                elif options.judge_gates and verdict is not None and not verdict.passes(turn.acceptable):
                    status = "fail"
                else:
                    status = "pass"
            failed_before = failed_before or status in ("fail", "error", "unjudged")
            result.turns.append(TurnResult(case.id, index, trace, checks, verdict, status,
                                           estimate_in_region(trace, probe) if probe else None,
                                           stage_breakdown(trace)))
            history.append((turn.q, trace.final_answer))
    return result


async def run_suite(cases: list[Case], *, fixtures: dict, probe: Probe, options: RunOptions,
                    agent=None) -> list[ConversationResult]:
    gate = asyncio.Semaphore(options.concurrency)

    async def guarded(case: Case) -> ConversationResult:
        async with gate:
            return await run_conversation(case, fixtures=fixtures, probe=probe, options=options, agent=agent)

    results = list(await asyncio.gather(*(guarded(c) for c in cases)))
    for i, first in enumerate(results):
        if not first.passed:
            second = await guarded(first.case)
            if second.passed:
                second.flaky = True
                results[i] = second
    return results
```

- [ ] **Step 4: Run the tests to watch them pass**

Run: `uv run pytest tests/test_evals_runner.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/evals/runner.py tests/test_evals_runner.py
git commit -m "Evals: concurrent conversation runner with in-memory state, rate-limit retry and flaky re-runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Selection (`--changed`, tiers)

**Files:**
- Create: `src/pension_chat/evals/select.py`
- Test: `tests/test_evals_select.py`

**Interfaces:**
- Consumes: `Case` and `AREAS` (Task 5).
- Produces:
  - `AREA_PATHS: dict[str, tuple[str, ...]]` and `CORE: tuple[str, ...]`;
  - `areas_for(changed_paths: list[str]) -> set[str]`;
  - `changed_files(base: str = "main") -> list[str]`, which uses `git diff --name-only <base>...HEAD` plus the working tree;
  - `select(cases, *, tier="full", families=None, ids=None, changed=None) -> list[Case]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evals_select.py
from __future__ import annotations

from pathlib import Path

from pension_chat.evals.cases import AREAS, Case, Turn
from pension_chat.evals.select import AREA_PATHS, areas_for, select


def _case(cid, family, areas, critical=False, tiers=("full",)) -> Case:
    return Case(id=cid, family=family, areas=list(areas), path=Path(f"evals/cases/{family}/{cid}.yaml"),
                turns=[Turn("q", "g", "r")], critical=critical, tiers=list(tiers))


CASES = [
    _case("S1-001", "S1", ["retrieval"], critical=True, tiers=("smoke", "full")),
    _case("S4-001", "S4", ["calculation"]),
    _case("S11-001", "S11", ["continuity"], tiers=("smoke", "full")),
]


def test_every_area_has_paths() -> None:
    assert set(AREA_PATHS) == AREAS


def test_a_retrieval_change_selects_retrieval_cases_and_criticals() -> None:
    picked = select(CASES, changed=["src/pension_chat/agent/retrieval.py"])
    assert [c.id for c in picked] == ["S1-001"]


def test_a_calculation_change_keeps_the_criticals() -> None:
    picked = select(CASES, changed=["src/pension_chat/calculation/dsl_interpreter.py"])
    assert {c.id for c in picked} == {"S1-001", "S4-001"}


def test_a_core_change_selects_everything() -> None:
    assert areas_for(["src/pension_chat/agent/loop.py"]) == set(AREAS)


def test_a_changed_case_file_selects_itself() -> None:
    picked = select(CASES, changed=["evals/cases/S4/S4-001.yaml"])
    assert "S4-001" in {c.id for c in picked}


def test_an_unrelated_change_selects_only_criticals() -> None:
    assert [c.id for c in select(CASES, changed=["frontend/src/app/page.tsx"])] == ["S1-001"]


def test_tier_family_and_id_filters() -> None:
    assert {c.id for c in select(CASES, tier="smoke")} == {"S1-001", "S11-001"}
    assert [c.id for c in select(CASES, families=["S4"])] == ["S4-001"]
    assert [c.id for c in select(CASES, ids=["S11-001"])] == ["S11-001"]
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_evals_select.py -v`
Expected: collection ERROR

- [ ] **Step 3: Implement `select.py`**

```python
# src/pension_chat/evals/select.py
"""Run what a change touched. A path maps to the areas it can affect; the
shared core (the loop, the graph, the guardrails, the tools, the model
client, the eval harness itself) can affect every area, so it selects
everything. Critical cases always run.
"""

from __future__ import annotations

import subprocess

from pension_chat.evals.cases import AREAS, Case

P = "src/pension_chat/"
AREA_PATHS: dict[str, tuple[str, ...]] = {
    "retrieval": (P + "agent/retrieval.py", P + "agent/corpus.py", P + "ingestion/"),
    "citations": (P + "agent/grounding.py",),
    "calculation": (P + "calculation/", P + "agent/registry.py", P + "agent/derivation.py",
                    P + "agent/facts.py", P + "agent/card.py"),
    "clarification": (P + "agent/gaps.py",),
    "assumptions": (P + "agent/worksheet.py",),
    "boundary": (P + "agent/coverage.py", P + "funds.py"),
    "continuity": (P + "agent/ledger.py", P + "agent/memory.py", P + "agent/checkpointer.py"),
    "recommendation": (P + "agent/recommend.py", P + "agent/objectives.py"),
    "records": (P + "records.py", P + "agent/nodes.py"),
    "voice": (P + "agent/presentation.py", P + "agent/blocks.py"),
}
CORE: tuple[str, ...] = (
    P + "agent/loop.py", P + "agent/graph.py", P + "agent/guardrails.py", P + "agent/tools.py",
    P + "agent/llm.py", P + "agent/state.py", P + "config.py", P + "evals/", "evals/rubric.yaml",
    "evals/fixtures/",
)


def areas_for(changed_paths: list[str]) -> set[str]:
    areas: set[str] = set()
    for path in changed_paths:
        path = path.replace("\\", "/")
        if any(path.startswith(core) for core in CORE):
            return set(AREAS)
        areas |= {area for area, prefixes in AREA_PATHS.items() if any(path.startswith(p) for p in prefixes)}
    return areas


def changed_files(base: str = "main") -> list[str]:
    committed = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                               capture_output=True, text=True, check=True).stdout.split()
    working = subprocess.run(["git", "diff", "--name-only", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.split()
    return sorted(set(committed) | set(working))


def select(cases: list[Case], *, tier: str = "full", families: list[str] | None = None,
           ids: list[str] | None = None, changed: list[str] | None = None) -> list[Case]:
    picked = [c for c in cases if tier in c.tiers or tier == "full"]
    if families:
        picked = [c for c in picked if c.family in families]
    if ids:
        picked = [c for c in picked if c.id in ids]
    if changed is not None:
        areas = areas_for(changed)
        touched = {p.replace("\\", "/") for p in changed}
        picked = [c for c in picked if c.critical or set(c.areas) & areas
                  or c.path.as_posix() in touched or any(c.path.as_posix().endswith(t) for t in touched)]
    return picked
```

- [ ] **Step 4: Run the tests to watch them pass**

Run: `uv run pytest tests/test_evals_select.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/evals/select.py tests/test_evals_select.py
git commit -m "Evals: select cases by tier, family, id, or the areas a change touched

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Report and the command line

**Files:**
- Create: `src/pension_chat/evals/report.py`, `scripts/run_evals.py`
- Modify: `.gitignore` (add `evals/runs/`, `evals/.judge_cache/`)
- Test: `tests/test_evals_report.py`

**Interfaces:**
- Consumes: `ConversationResult` and `TurnResult` (Task 10), `FIRST_CLASS` (Task 5).
- Produces:
  - `summarise(results, *, judge_calibrated: bool, probe: dict) -> dict`, returning the report JSON with keys `families`, `critical_failures`, `turns`, `latency`, `soft_scores`, `verification`, `semantic_fallbacks`, `flaky` and `judge_calibrated`;
  - `diff(report, baseline: dict | None) -> dict`, with keys `new_failures`, `fixed` and `latency_moves`;
  - `render_markdown(report, changes) -> str`;
  - `exit_code(report, changes) -> int`: 1 when there are critical failures, or new failures that aren't flaky.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evals_report.py
from __future__ import annotations

from pathlib import Path

from pension_chat.evals.cases import Case, Turn
from pension_chat.evals.checks import CheckResult
from pension_chat.evals.report import diff, exit_code, render_markdown, summarise
from pension_chat.evals.runner import ConversationResult, TurnResult
from pension_chat.evals.trace import TurnTrace


def _conv(cid, family, statuses, critical=False, ttft=2.0, flaky=False) -> ConversationResult:
    case = Case(id=cid, family=family, areas=["retrieval"], path=Path("x"),
                turns=[Turn("q", "g", "r", checks={"ttft_class": "simple"}) for _ in statuses], critical=critical)
    turns = [TurnResult(cid, i, TurnTrace(case_id=cid, turn_index=i, question="q", employee=None, ttft=ttft,
                                          guardrail_passed=True, notes=[{"at": 0, "kind": "semantic_fallback", "detail": ""}]),
                        [CheckResult("x", s == "pass")], None, s, ttft - 1.0, {"model": 1.0})
             for i, s in enumerate(statuses)]
    return ConversationResult(case, turns, flaky=flaky)


RESULTS = [
    _conv("S1-001", "S1", ["pass", "pass"], critical=True),
    _conv("S4-001", "S4", ["pass", "fail", "after_failure"]),
    _conv("S8-001", "S8", ["pending"]),
]


def test_family_rates_count_scored_turns_only() -> None:
    r = summarise(RESULTS, judge_calibrated=False, probe={})
    assert r["families"]["S1"] == {"passed": 2, "scored": 2, "first_class": True}
    assert r["families"]["S4"] == {"passed": 1, "scored": 2, "first_class": True}   # after_failure not scored
    assert r["families"]["S8"]["scored"] == 0
    assert r["semantic_fallbacks"] == 6


def test_a_critical_failure_fails_the_run() -> None:
    bad = [_conv("S1-001", "S1", ["fail"], critical=True)]
    r = summarise(bad, judge_calibrated=False, probe={})
    assert r["critical_failures"] == ["S1-001#0"]
    assert exit_code(r, diff(r, None)) == 1


def test_diff_against_baseline() -> None:
    base = summarise([_conv("S4-001", "S4", ["pass", "pass", "pass"])], judge_calibrated=False, probe={})
    now = summarise(RESULTS, judge_calibrated=False, probe={})
    changes = diff(now, base)
    assert changes["new_failures"] == ["S4-001#1"]
    assert exit_code(now, changes) == 1


def test_markdown_leads_with_changes() -> None:
    now = summarise(RESULTS, judge_calibrated=False, probe={})
    text = render_markdown(now, diff(now, None))
    assert text.index("Changes since baseline") < text.index("Families")
    assert "judge not calibrated" in text.lower()
    assert "estimated" in text.lower()
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_evals_report.py -v`
Expected: collection ERROR

- [ ] **Step 3: Implement `report.py`**

```python
# src/pension_chat/evals/report.py
"""Score a run and say what changed since the last accepted one.

The report opens with the diff against evals/baseline.json, because a run
repeated after every change is read for what moved, not re-read in full.
Then: the critical block (any failure fails the run), pass rates per family
with the first-class families first, latency measured and estimated per class
and per stage, verification, semantic-ranker fallbacks, and the judge's soft
scores.
"""

from __future__ import annotations

import statistics
from typing import Any

from pension_chat.evals.cases import FAMILIES, FIRST_CLASS
from pension_chat.evals.runner import ConversationResult

SCORED = ("pass", "fail", "error", "unjudged")


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def summarise(results: list[ConversationResult], *, judge_calibrated: bool, probe: dict) -> dict[str, Any]:
    families: dict[str, dict[str, Any]] = {}
    turns: dict[str, dict[str, Any]] = {}
    latency: dict[str, dict[str, list[float]]] = {}
    critical: list[str] = []
    soft: dict[str, list[int]] = {"voice": [], "hedging": [], "clarity": []}
    fallbacks = verified = replaced = total = 0
    for conv in results:
        fam = families.setdefault(conv.case.family, {"passed": 0, "scored": 0,
                                                      "first_class": conv.case.family in FIRST_CLASS})
        for tr in conv.turns:
            key = f"{conv.case.id}#{tr.turn_index}"
            turns[key] = {"status": tr.status, "family": conv.case.family, "flaky": conv.flaky,
                          "failed_checks": [f"{c.name}: {c.detail}" for c in tr.checks if not c.passed],
                          "ttft": tr.trace.ttft, "estimate": tr.estimate}
            if tr.status in SCORED:
                fam["scored"] += 1
                fam["passed"] += tr.status == "pass"
                if conv.case.critical and tr.status != "pass":
                    critical.append(key)
            cls = conv.case.turns[tr.turn_index].checks.get("ttft_class")
            if cls and tr.trace.ttft is not None:
                slot = latency.setdefault(cls, {"measured": [], "estimated": []})
                slot["measured"].append(tr.trace.ttft)
                if tr.estimate is not None:
                    slot["estimated"].append(tr.estimate)
            fallbacks += sum(1 for n in tr.trace.notes if n.get("kind") == "semantic_fallback")
            total += 1
            verified += bool(tr.trace.guardrail_passed)
            replaced += bool(tr.trace.replacements)
            if tr.verdict:
                for k in soft:
                    soft[k].append(getattr(tr.verdict, k))
    return {
        "families": {f: families[f] for f in FAMILIES if f in families},
        "critical_failures": critical,
        "turns": turns,
        "latency": {cls: {kind: {"median": _pct(v, 0.5), "p90": _pct(v, 0.9), "n": len(v)}
                          for kind, v in slot.items()} for cls, slot in latency.items()},
        "soft_scores": {k: (statistics.mean(v) if v else None) for k, v in soft.items()},
        "verification": {"passed": verified, "replaced": replaced, "turns": total},
        "semantic_fallbacks": fallbacks,
        "flaky": sorted({k.split("#")[0] for k, t in turns.items() if t["flaky"]}),
        "judge_calibrated": judge_calibrated,
        "probe": probe,
    }


def diff(report: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    if not baseline:
        return {"new_failures": [], "fixed": [], "latency_moves": {}, "no_baseline": True}
    before, now = baseline.get("turns", {}), report["turns"]
    bad = ("fail", "error", "unjudged")
    new = [k for k, t in now.items() if t["status"] in bad and before.get(k, {}).get("status") == "pass"]
    fixed = [k for k, t in now.items() if t["status"] == "pass" and before.get(k, {}).get("status") in bad]
    moves = {}
    for cls, slot in report["latency"].items():
        old = baseline.get("latency", {}).get(cls, {}).get("measured", {}).get("median")
        new_median = slot.get("measured", {}).get("median")
        if old is not None and new_median is not None:
            moves[cls] = round(new_median - old, 2)
    return {"new_failures": new, "fixed": fixed, "latency_moves": moves, "no_baseline": False}


def exit_code(report: dict[str, Any], changes: dict[str, Any]) -> int:
    flaky = set(report.get("flaky") or [])
    real_new = [k for k in changes["new_failures"] if k.split("#")[0] not in flaky]
    return 1 if report["critical_failures"] or real_new else 0


def render_markdown(report: dict[str, Any], changes: dict[str, Any]) -> str:
    lines = ["## Changes since baseline"]
    if changes.get("no_baseline"):
        lines.append("No baseline yet — accept this run with `--accept` to make it one.")
    else:
        lines.append(f"New failures: {', '.join(changes['new_failures']) or 'none'}")
        lines.append(f"Fixed: {', '.join(changes['fixed']) or 'none'}")
        lines.append(f"Median TTFT moves (s): {changes['latency_moves'] or 'none'}")
    lines += ["", "## Critical", ", ".join(report["critical_failures"]) or "all critical cases passed", "",
              "## Families"]
    for fam, f in report["families"].items():
        mark = " (first-class)" if f["first_class"] else ""
        rate = f"{f['passed']}/{f['scored']}" if f["scored"] else "pending"
        lines.append(f"- {fam}{mark}: {rate}")
    lines += ["", "## Latency (time to first answer token; the estimate is an in-region *estimated* figure)"]
    for cls, slot in report["latency"].items():
        m, e = slot.get("measured", {}), slot.get("estimated", {})
        lines.append(f"- {cls}: measured median {m.get('median')} s, p90 {m.get('p90')} s; "
                     f"estimated median {e.get('median')} s, p90 {e.get('p90')} s")
    v = report["verification"]
    lines += ["", f"Verification: {v['passed']}/{v['turns']} passed, {v['replaced']} replaced; "
                  f"semantic-ranker fallbacks: {report['semantic_fallbacks']}",
              f"Judge soft scores: {report['soft_scores']}"]
    if not report["judge_calibrated"]:
        lines.append("Judge not calibrated — its verdicts are reported, not gating (see evals/labels/README.md).")
    lines += ["", "## Failures"]
    for key, t in report["turns"].items():
        if t["status"] in ("fail", "error", "unjudged"):
            lines.append(f"- {key} [{t['status']}]{' (flaky)' if t['flaky'] else ''}: {'; '.join(t['failed_checks'])}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to watch them pass**

Run: `uv run pytest tests/test_evals_report.py -v`
Expected: 4 passed

- [ ] **Step 5: Write `scripts/run_evals.py`**

```python
"""Run the eval suite. See docs/superpowers/specs/2026-09-30-eval-suite-design.md.

    uv run python scripts/run_evals.py                      # smoke tier
    uv run python scripts/run_evals.py --tier full
    uv run python scripts/run_evals.py --changed            # what this branch touched, plus criticals
    uv run python scripts/run_evals.py --family S4 --family S4b
    uv run python scripts/run_evals.py --id S1-003
    uv run python scripts/run_evals.py --tier full --accept # make this run the baseline
    uv run python scripts/run_evals.py --calibrate --from evals/runs/<ts>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

# Nothing a run does may write to the client's databases or the repo.
for key, value in {"APP_ENV": "test", "LOG_LEVEL": "WARNING", "CAPTURE_FAILURES": "false",
                   "ANSWER_MEMORY_ENABLED": "false", "PERSIST_DERIVED_RECIPES": "false"}.items():
    os.environ.setdefault(key, value)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
EVALS = ROOT / "evals"


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tier", choices=["smoke", "full"], default="smoke")
    p.add_argument("--family", action="append")
    p.add_argument("--id", action="append")
    p.add_argument("--changed", action="store_true")
    p.add_argument("--base", default="main")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--latency-gate", action="store_true")
    p.add_argument("--with-memory", action="store_true",
                   help="let the run write answer memory and derived recipes (the production knowledge path)")
    p.add_argument("--accept", action="store_true")
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--from", dest="from_run", type=Path)
    return p.parse_args()


async def _calibrate(run_dir: Path) -> int:
    from pension_chat.evals.judge import Verdict, calibration, rubric_version
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    verdicts = {k: Verdict(**t["verdict"]) for k, t in report.get("verdicts", {}).items()}
    agreed, total = calibration(EVALS / "labels", verdicts)
    (EVALS / "calibration.json").write_text(json.dumps(
        {"rubric_version": rubric_version(EVALS / "rubric.yaml"), "agreed": agreed, "total": total}), encoding="utf-8")
    print(f"Judge agreed with {agreed}/{total} labels ({'gating' if total >= 20 and agreed >= 18 else 'not gating'}).")
    return 0


async def main() -> int:
    args = _args()
    if args.with_memory:
        os.environ["ANSWER_MEMORY_ENABLED"] = "true"
        os.environ["PERSIST_DERIVED_RECIPES"] = "true"
    if args.calibrate:
        return await _calibrate(args.from_run)

    from dataclasses import asdict

    from pension_chat.agent.llm import close_clients
    from pension_chat.config import get_settings
    from pension_chat.evals.cases import load_cases
    from pension_chat.evals.fixtures import load_fixtures
    from pension_chat.evals.judge import is_calibrated
    from pension_chat.evals.report import diff, exit_code, render_markdown, summarise
    from pension_chat.evals.runner import RunOptions, run_suite
    from pension_chat.evals.select import changed_files, select
    from pension_chat.evals.timing import probe

    fixtures = load_fixtures(EVALS / "fixtures" / "employees", date.today())
    cases = select(load_cases(EVALS / "cases", set(fixtures)), tier=args.tier, families=args.family,
                   ids=args.id, changed=changed_files(args.base) if args.changed else None)
    print(f"{len(cases)} conversations, {sum(len(c.turns) for c in cases)} turns")
    measured = await probe(get_settings())
    calibrated = is_calibrated(EVALS, EVALS / "rubric.yaml")
    options = RunOptions(concurrency=args.concurrency, judge=not args.no_judge, judge_gates=calibrated,
                         latency_gate=args.latency_gate, rubric_path=EVALS / "rubric.yaml",
                         cache_dir=EVALS / ".judge_cache")
    started = time.perf_counter()
    try:
        results = await run_suite(cases, fixtures=fixtures, probe=measured, options=options)
    finally:
        await close_clients()

    report = summarise(results, judge_calibrated=calibrated, probe=measured)
    report["verdicts"] = {f"{r.case.id}#{t.turn_index}": asdict(t.verdict)
                          for r in results for t in r.turns if t.verdict}
    report["wall_seconds"] = round(time.perf_counter() - started, 1)
    baseline_path = EVALS / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else None
    changes = diff(report, baseline)

    run_dir = EVALS / "runs" / time.strftime("%Y%m%dT%H%M%S")
    (run_dir / "traces").mkdir(parents=True)
    for r in results:
        for t in r.turns:
            (run_dir / "traces" / f"{r.case.id}#{t.turn_index}.json").write_text(
                json.dumps(t.trace.to_dict(), indent=1), encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(render_markdown(report, changes))
    print(f"\nWall time {report['wall_seconds']} s. Saved to {run_dir}")
    if args.accept:
        baseline_path.write_text(json.dumps({k: report[k] for k in ("turns", "latency", "families")},
                                            indent=1, default=str), encoding="utf-8")
        print("Accepted as the new baseline.")
    return exit_code(report, changes)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

Add to `.gitignore`:

```
# Eval runs and the judge's cache (evals/baseline.json is committed)
evals/runs/
evals/.judge_cache/
```

- [ ] **Step 6: A smoke test of the command line (no network)**

Run: `uv run python scripts/run_evals.py --help`
Expected: usage text listing `--tier`, `--changed`, `--calibrate`

- [ ] **Step 7: Commit**

```bash
git add src/pension_chat/evals/report.py scripts/run_evals.py .gitignore tests/test_evals_report.py
git commit -m "Evals: report with baseline diff and critical block, and the run_evals command

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: The case inventory

**Files:**
- Create: `evals/cases/<family>/*.yaml`, about 50 conversations, as listed below
- Test: `tests/test_evals_inventory.py`

**Interfaces:**
- Consumes: the loader (Task 5) and the fixture keys (Task 5).

**How to write a `reference`.**
1. Find the governing text:
   ```
   uv run python -c "import re;t=open('data/statutes/article_8.txt',encoding='utf-8').read();i=t.find('(40 ILCS 5/8-129)');print(t[i:i+4000])"
   ```
   Use `article_1.txt` for §1-xxx and `article_20.txt` for §20-xxx. §1-160 exists in 3 versions; they agree except in (g) and (g-5).
2. Write the facts a correct answer must state, each followed by its section and subsection, e.g. "(§1-160(c))".
3. Put the key facts in `must_state`, using the words and numbers a correct answer would contain.
4. If the text does not settle a fact, the reference says so. The case then expects the answer to say it can't be determined, not a guess.

The product owner reviews every reference before the first `--accept` (Task 14).

- [ ] **Step 1: Write the failing inventory test**

```python
# tests/test_evals_inventory.py
"""The starting inventory the spec sets, and that it all loads."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from pension_chat.evals.cases import FIRST_CLASS, load_cases
from pension_chat.evals.fixtures import load_fixtures

EVALS = Path(__file__).parents[1] / "evals"
STATUTES = Path(__file__).parents[1] / "data" / "statutes"


@pytest.fixture(scope="module")
def cases():
    fixtures = load_fixtures(EVALS / "fixtures" / "employees", date.today())
    return load_cases(EVALS / "cases", set(fixtures))


def test_inventory_matches_the_spec(cases) -> None:
    per_family = Counter(c.family for c in cases)
    assert per_family["S1"] >= 8 and per_family["S4"] >= 6 and per_family["S4b"] >= 6
    assert per_family["S11"] >= 5 and per_family["S6"] >= 3 and per_family["S12"] >= 8
    for fam in ("S2", "S3", "S5", "S7", "S10"):
        assert per_family[fam] >= 1, fam
    for fam in ("S8", "S9"):
        assert per_family[fam] >= 2, fam


def test_smoke_tier_has_every_critical_and_each_first_class_family(cases) -> None:
    smoke = [c for c in cases if "smoke" in c.tiers]
    assert all("smoke" in c.tiers for c in cases if c.critical)
    assert {c.family for c in smoke} >= set(FIRST_CLASS)
    assert 10 <= len(smoke) <= 18


def test_the_tier2_case_is_critical_and_checks_the_evidence(cases) -> None:
    [case] = [c for c in cases if c.id == "S1-003"]
    assert case.critical
    assert case.turns[0].checks["evidence_has"] == ["1-160(c)"]


@pytest.mark.skipif(not STATUTES.exists(), reason="data/ is not in the repo")
def test_every_section_a_reference_names_exists_in_the_corpus(cases) -> None:
    corpus = "".join(p.read_text(encoding="utf-8", errors="replace") for p in STATUTES.glob("article_*.txt"))
    missing = []
    for c in cases:
        for t in c.turns:
            for section in re.findall(r"§\s?(\d+-[\d.]+[a-z]?)", t.reference):
                if f"(40 ILCS 5/{section})" not in corpus:
                    missing.append(f"{c.id}: §{section}")
    assert not missing, missing
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_evals_inventory.py -v`
Expected: FAIL. `evals/cases` is missing, or the inventory counts are 0.

- [ ] **Step 3: Write the cases**

One file per conversation, at `evals/cases/<family>/<id>.yaml`. The complete S1-003 is below. Every other case follows the same format, with the questions, goals and checks listed here. References are written from the statute text by the procedure above. `tiers: [smoke, full]` goes on the ids marked **(smoke)**.

```yaml
# evals/cases/S1/S1-003.yaml
id: S1-003
family: S1
areas: [retrieval, citations]
critical: true
tiers: [smoke, full]
turns:
  - q: "Under Article 8, what is the minimum age and service to retire for a Tier 2 member, and which section says so?"
    goal: "The Tier 2 minimum retirement age and service for MEABF, and the governing section."
    reference: >
      Age 67 with at least 10 years of service credit (§1-160(c)); a reduced annuity from age 62 with
      10 years, reduced by 1/2 of 1% for each month under 67 (§1-160(c)). Members first joining on or
      after July 6, 2017 under (c-5): age 65 with 10 years (§1-160(c-5)).
    checks:
      cites_any: ["1-160"]
      evidence_has: ["1-160(c)"]
      must_state: ["67", "10 years"]
      answers_from_article: "8"
      ttft_class: simple
  - q: "And for someone who joined in 2019?"
    goal: "The rule for a member first joining after July 6, 2017."
    reference: "Age 65 with 10 years of service credit (§1-160(c-5)); the answer says which rule applies and why."
    checks:
      cites_any: ["1-160"]
      must_state: ["65"]
      ttft_class: simple
```

**S1: statute questions (8)**
- **S1-001 (smoke, critical)**
  - Turn 1: "Who does the Municipal Employees' Annuity and Benefit Fund cover?" Goal: who is an employee under Article 8. Checks: `cites_any: ["8-113", "8-101"]`, `answers_from_article: "8"`.
  - Turn 2: "Does that include part-time employees of the Board of Education?" (§8-108.3).
- **S1-002**
  - Turn 1: "What is the minimum age and service for a Tier 1 age-and-service annuity under Article 8?" Checks: `cites_any: ["8-129"]` and `must_state` from the text.
  - Turn 2: "Is the annuity reduced if they retire before 60?" (the reduction in §8-129 / §8-133).
- **S1-003 (smoke, critical):** as written above.
- **S1-004**
  - Turn 1: "How is final average salary calculated for a Tier 2 MEABF member?" Checks: `cites_any: ["1-160"]`, `evidence_has: ["1-160(b)"]`.
  - Turn 2: "Is there a cap on the salary that counts?" (§1-160(b-5)).
- **S1-005**
  - Turn 1: "What do Tier 3 members contribute, compared with other members?" (§8-174(a-10); the 9.5% vs 6.5% was confirmed 2026-09-29). Checks: `cites_any: ["8-174"]`.
  - Turn 2: "Since when?"
- **S1-006**
  - Turn 1: "If an employee leaves before retirement age, can they get their contributions back?" Checks: `cites_any: ["8-168"]`.
  - Turn 2: "Does taking the refund end their service credit?"
- **S1-007**
  - Turn 1: "What does a duty disability benefit pay under Article 8?" Checks: `cites_any: ["8-160"]`.
  - Turn 2: "When does it stop?" (§8-163 and §8-160).
- **S1-008**
  - Turn 1: "An employee has service in another Illinois pension system too. Can it be combined?" Checks: `cites_any: ["20-121", "20-122", "20-123"]`.
  - Turn 2: "Which system pays the annuity then?"

**S4: calculation for a named employee (6)**
All have `needs: []` today. Once employer voice ships, add `employer_voice` to `needs` and move the member-voiced wording to employer wording.
- **S4-001 (smoke)**, `employee: tier1_veteran`
  - Turn 1: "What would this employee's annuity be if they retired at the end of this year?" Checks: `calculation: true`, `worked_from_article: "8"`, `figures_from: [record, engine, statute]`, `ttft_class: heavy`.
  - Turn 2: "Show me how that was worked out." Checks: `cites_any` naming the section the figure was worked from, and `verification_passed: true`.
- **S4-002**, `employee: tier2_standard`
  - Turn 1: "What would their annuity be at 67?" Checks: `calculation: true`, `cites_any: ["1-160"]`.
  - Turn 2: "And at 62?" (the reduced annuity).
- **S4-003**, `employee: tier3_recent`
  - Turn 1: "Estimate their annuity at 65." Checks: `calculation: true` and `cites_any: ["1-160"]`, or a statement of what is missing.
- **S4-004**, `employee: near_threshold`
  - Turn 1: "Can they retire now with an annuity?" (9.8 years is under the 10-year minimum). Checks: `no_calculation: true`, `must_state: ["10 years"]`.
  - Turn 2: "What if they work one more year?"
- **S4-005**, `employee: no_hire_date`
  - Turn 1: "Estimate their pension at 65." `acceptable: [ask_critical, branch_on_critical]`. Checks: `critical_inputs: [hire_date]`, `max_questions: 2`.
  - Turn 2: "They joined in 2004." Checks: `calculation: true`, `no_reask: [participation_date, age]`.
- **S4-006**, `employee: no_stored_service`
  - Turn 1: "What would their annuity be now?" `acceptable: [ask_other, branch_on_critical]`. Checks: `critical_inputs: [total_service_years]`.
  - Turn 2: "Use 21.5 years of service." Checks: `calculation: true`, `figures_from: [user, record, engine, statute]`.

**S4b: calculation for a described employee (6)**
- **S4b-001 (smoke):** the 56-year-old earning $72,000 with 18 years, at 60. `acceptable: [ask_critical, branch_on_critical]`. Checks: `critical_inputs: [participation_date]`, `max_assumed: 2`, `assumptions_disclosed: true`, `figures_from: [engine, user, statute, assumed]`, `ttft_class: heavy`. Follow-up turn: "They joined in 2009." Checks: `calculation: true`, `worked_from_article: "8"`, `no_reask: [age, salary, service, participation_date]`.
- **S4b-002:** "An employee who joined in 2015 is 62, earns $80,000 and has 11 years of service. What is their annuity if they retire now?" Checks: `calculation: true`, `cites_any: ["1-160"]`, `must_state: ["62"]`. Follow-up: "What if they wait until 67?"
- **S4b-003:** "An employee joined in 2001, is 60, and has 24 years. What would their annuity be?" (no salary). `acceptable: [ask_other]`. Checks: `no_calculation: true`, `max_questions: 2`. Follow-up: "Their salary was about $90,000 for the last several years." Checks: `calculation: true`, `max_assumed: 2`.
- **S4b-004:** "A Tier 3 employee aged 64 with 10 years and $65,000 salary — annuity at 65?" Checks: `cites_any: ["1-160"]`, `calculation: true`.
- **S4b-005:** "An employee aged 66 with 9.5 years who joined in 2016 — can they retire now?" Checks: `no_calculation: true`, `must_state: ["10 years"]`.
- **S4b-006:** "If an employee earns $75,000, joined in 1998 and is 58 with 27 years, what are their options?" `acceptable: [answer, branch_on_critical]`. Checks: `calculation: true`, `assumptions_disclosed: true`.

**S11: continuity (5)**
- **S11-001 (smoke):** a correction carries through.
  - Turn 1: S4b-002's question.
  - Turn 2: "Sorry, the salary is $86,000." Checks: `calculation: true`, `no_reask: [age, service, participation_date]`, `must_state: ["86,000"]`.
  - Turn 3: "So how much more is that per year?"
- **S11-002:** follow-up pronouns, `employee: tier2_standard`.
  - Turns: "What is their annuity at 67?" / "and at 65?" / "which of those two is higher, and by how much?"
- **S11-003:** a held scenario, then leaving it.
  - Turns: "Suppose this employee had joined in 2009 instead." / "What would the annuity be at 60 then?" / "Back to their actual record — what is it at 67?" Checks on turn 3: `cites_any: ["1-160"]`.
- **S11-004:** revisiting a provision.
  - Turns: S1-003 turn 1 / "What does Article 8 say about widow's annuities?" / "Going back to the Tier 2 rule — does it apply to someone rehired after a break?" Checks on turn 3: `cites_any: ["1-160"]`.
- **S11-005:** an employee switch within one chat, `needs: [find_employee]` (pending).
  - Turns: "Look up A. Rivera." / "What tier is she?" / "Now M. Okafor." / "Compare their earliest unreduced retirement ages."

**S6: recommendations (3)**
- **S6-001**, `employee: tier2_standard`: "Which retirement age gives the best annuity, 62, 65 or 67?" Checks: `calculation: true`. Follow-up: "Why that one?"
- **S6-002**, `employee: near_threshold`: "Should they retire now or work another year?" Checks: `must_state: ["10 years"]`.
- **S6-003**: "An employee aged 55 with 12 years is leaving the city. Should they take a refund or leave contributions in for a later annuity?" Checks: `cites_any: ["8-168"]`.

**S2 / S3 / S5 / S7 / S10 (1–2 each)**
- **S2-001**, `employee: tier1_veteran`: "When was this employee hired and what tier are they?" Checks: `about_employee: tier1_veteran`, `ttft_class: simple`.
- **S3-001**, `employee: tier2_standard`: "Can they retire next year?" Checks: `cites_any: ["1-160"]`.
- **S5-001**, `employee: tier2_standard`: "What if they worked two more years?"
- **S5-002**: "What would change if they had joined before 2011?"
- **S7-001**, `employee: unmapped_status`: "Is this employee active?" `acceptable: [ask_other, answer]`. Checks: `must_not_state: ["is active"]`.
- **S7-002**, `employee: no_hire_date`: "What tier are they?" `acceptable: [ask_critical, answer]`.
- **S10-001 (smoke)**: "What are the retirement rules for Chicago police officers?" `acceptable: [refuse]`. Checks: `cites_none_outside: ["8"]`.
- **S10-002**: "Which of my employees can retire this year?" (D1 is deferred). `acceptable: [refuse]`. Checks: `no_calculation: true`.
- **S10-003**: "Write me a poem about pensions." Checks: `refuses: true`.

**S8 / S9 (pending `find_employee`)**
- **S8-001**: "Look up L. Nguyen." / "and M. Okafor?" / "who retires first?"
- **S8-002**: "What is A. Rivera's tier?" / "and E. Walsh's status?"
- **S9-001**: "Look up J. Smith." `acceptable: [ask_other]`. Checks: `max_questions: 1`. Follow-up: "the one at the library."
- **S9-002**: "What is J. Smith's service?" `acceptable: [ask_other]`.

**S12: journeys (8, 6–10 turns each)**
- **S12-001:** S1-003 → "Look at A. Rivera" (`tier2_standard`) → "annuity at 67?" → "what if she waits to 68?" → "which section caps salary?" → "back to the 67 figure — explain it." Needs `find_employee`.
- **S12-002:** described employee: facts → ask/branch → answer → correction → recommendation → "summarise for the employee."
- **S12-003:** statute → out of boundary (police) → back to Article 8 → calculation.
- **S12-004:** a near-threshold employee: eligibility → one more year → recommendation → refund option.
- **S12-005:** a Tier 3 employee: contribution rate → retirement age → estimate → compare with Tier 2 rules.
- **S12-006:** a missing hire date: ask → answer → calculation → correction of the hire date → a new figure.
- **S12-007:** a reciprocal employee: combined service → which system pays → an estimate with a caveat.
- **S12-008:** a mixed journey: greeting → statute → hypothetical → leave the hypothetical → record question → recommendation.

S12 cases carry `critical: false` and `tiers: [full]`. The smoke tier is:
- S1-001 and S1-003;
- S4-001;
- S4b-001;
- S11-001;
- S10-001;
- plus S1-004, S4-002, S4b-002, S6-001 and S2-001, giving 11 conversations.

- [ ] **Step 4: Run the inventory test and the loader over the real cases**

Run: `uv run pytest tests/test_evals_inventory.py -v`
Expected: 4 passed. If `data/` is present, the section-existence test runs; any missing section is a wrong reference, so fix the reference.

- [ ] **Step 5: Commit**

```bash
git add evals/cases tests/test_evals_inventory.py
git commit -m "Evals: the starting inventory -- S1, S4, S4b and S11 first-class, S12 journeys

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Retire the old suite, document it, first live run

**Files:**
- Delete:
  - `scripts/eval_agent.py`, `scripts/replay_coverage.py`, `scripts/promote_captures.py`
  - `evals/*.yaml` (the 18 old files at the top level of `evals/`), `evals/_captured/`
  - `tests/test_eval_checker.py`, `tests/test_eval_latency.py`, `tests/test_eval_suite.py`
- Modify:
  - `CLAUDE.md`: the `### Evaluation` section, and the command block;
  - `src/pension_chat/evals/__init__.py`: the module docstring carries the three ideas forward;
  - `.gitignore`: remove the old `eval_agent.py --json` lines.

- [ ] **Step 1: Delete, and confirm nothing imports the old harness**

```bash
git rm -q scripts/eval_agent.py scripts/replay_coverage.py scripts/promote_captures.py tests/test_eval_checker.py tests/test_eval_latency.py tests/test_eval_suite.py
git rm -q evals/*.yaml
git rm -rq evals/_captured
```

Run: `uv run python -c "import subprocess;print(subprocess.run(['git','grep','-l','eval_agent import\\|from eval_agent'],capture_output=True,text=True).stdout or 'none')"`
Expected: `none`

- [ ] **Step 2: The package docstring**

```python
# src/pension_chat/evals/__init__.py
"""The eval suite (docs/superpowers/specs/2026-09-30-eval-suite-design.md).

Three ideas carried over from the member-first harness it replaced:
a case is a conversation, not a question -- the failures that matter are a
correction that did not propagate or a provision forgotten by turn five; a
critical case fails the run whatever the overall score says; and a case for a
capability not yet built is run and reported, not scored.
"""
```

- [ ] **Step 3: Rewrite `CLAUDE.md`'s Evaluation section**

Replace the whole `### Evaluation` section with:

```markdown
### Evaluation

`scripts/run_evals.py` runs `evals/cases/<family>/*.yaml` through the real agent (`run_agent_stream`) against the live index and model. The spec is `docs/superpowers/specs/2026-09-30-eval-suite-design.md`.

- **A case is a conversation.** Each turn states its `goal` and `reference`, the facts a correct answer rests on.
- **Code checks can veto a turn.** They form a closed vocabulary in `evals/cases.py` and read the `TurnTrace`: verified citations, the subsections actually *rendered to the model* (`evidence_has`), each input's origin, assumptions, and replacements.
- **A judge grades the turn against its reference.** It decides whether the goal was accomplished, whether the answer was relevant, and whether it stayed within the boundary. Its verdicts gate only once calibrated against the labels in `evals/labels/`, with at least 18 of 20 agreeing.
- **Families S1–S12 plus S4b.** The first-class families are S1 (statute questions), S4/S4b (calculations for a named or described employee) and S11 (continuity).
- **Records come from `evals/fixtures/employees/*.yaml`.** They are dated relative to the run date and served through the same `RecordProvider` interface the production PBMS read layer implements. Checkpoints live in memory. A run writes nothing to any database.
- **Latency is time to the first answer token.** It is measured and also estimated for in-region, per stage, and the gate stays provisional until an in-region run calibrates the estimate.

Typical runs:
- `--tier smoke` (default) after every change;
- `--changed` for what the branch touched, plus the critical cases;
- `--tier full --accept` before a merge, which updates `evals/baseline.json`.

The report opens with the diff against that baseline.
```

In the Commands block, replace any `eval_agent.py` line with `uv run python scripts/run_evals.py --changed   # evals for what this branch touched`.

- [ ] **Step 4: The full unit suite, lint, types**

Run: `uv run pytest -q && uv run ruff check src/pension_chat/evals src/pension_chat/records.py src/pension_chat/agent/instrument.py scripts/run_evals.py tests/test_evals_*.py && uv run mypy src/pension_chat/evals src/pension_chat/records.py src/pension_chat/agent/instrument.py`
Expected:
- pytest: all pass;
- ruff: clean;
- mypy: no errors in the new modules. Errors that were already there before in files this plan touched are left as they were.

- [ ] **Step 5: First live smoke run**

Before the first full run, check the quota: in the Azure portal (or `az cognitiveservices account deployment show`), note `gpt-6-luna`'s tokens-per-minute. If the default of 8 concurrent conversations exceeds it (one conversation turn uses about 25k tokens per minute), lower `--concurrency` to fit.

Run: `uv run python scripts/run_evals.py --tier smoke`
Expected:
- about 11 conversations complete, in under 3 minutes of wall time;
- the report shows measured and estimated TTFT per stage;
- S1-003 passes: its `evidence_has: 1-160(c)` holds after `89dc540`.

Every failure is a finding for the product owner, not something to tune away.

Then confirm the client's database is untouched: with `get_admin_session`, `select count(*)` from `agent_checkpoints`, `agent_checkpoint_writes`, `sessions` and `session_messages` must all be 0.

- [ ] **Step 6: Commit**

```bash
git add -A CLAUDE.md .gitignore src/pension_chat/evals/__init__.py
git commit -m "Evals: retire the member-first harness; CLAUDE.md describes the new suite

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

The first `--tier full --accept` (the baseline) is **not** part of this plan. It happens after the product owner has reviewed the case references and labelled the 20 calibration turns.
