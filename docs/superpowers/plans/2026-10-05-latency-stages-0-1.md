# Latency Stages 0–1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the per-turn model-call budget a measured, test-enforced property of the codebase, then remove the hot-path costs that don't belong there (the separate extraction call, the always-offered coverage tool, the unprefetched named sections, repeated section text, the separate embedding round trip).

**Architecture:**
- A new `agent/budgets.py` owns the budget table, the turn-type classifier, and a sequential-call counter. Production logs one budget record per turn; a static test makes every model-call site register its node there; the eval suite checks `model_calls_max` and reports p50/p95 per turn type.
- Stage 1 changes then go in one by one, each gated on the new latency suite (`L1`).

**Tech Stack:** Python 3.12, FastAPI, LangGraph, Azure OpenAI (`gpt-6-luna`), Azure AI Search (`azure-search-documents` 12.0.0), pytest (`asyncio_mode=auto`), `uv`.

**Spec:** `docs/superpowers/specs/2026-10-05-latency-stages-0-1-design.md`

## Global Constraints

- The time measured is to the first answer word (D3a: progress lines don't stop the clock), estimated in-region from this laptop.
- Targets: most turns **p50 < 3 s, p95 < 4.5 s**; heavy turns **p50 < 7 s, p95 < 9 s**.
- No degradation (D3): never cap the investigation or drop evidence to save time.
- Model is `gpt-6-luna` only; token limit 1M per minute; semantic ranker on.
- **P2:** a capability is deterministic code, arguments of an existing call, or prepared ahead of time. Never a new sequential model call, except a registered exception.
- **P3:** anything the model must not get wrong is checked in code. A `stated` value is verified against the user's words.
- Every task leaves `uv run pytest -q` green and `uv run ruff check` clean on the files it touches. Mypy must not gain errors in touched files.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Live runs (L1, S4, S4b, S11, S13) use `--concurrency 1`. Nothing else may use the model during a timed run.

## Review Focus

1. **A `stated` value the user never wrote** (the model infers "62" from "joined in 2015"): it must be rejected and never used as `stated`. Pinned in Task 7.
2. **A correction in a later turn** ("actually it's 23 years of service") with the separate extraction call off: it must still override the record. Pinned in Task 7.
3. **A self-described turn on a single-Article deployment** ("I am a park employee"): the coverage tool must still be offered. Pinned in Task 8.
4. **A named section that doesn't exist** ("Section 8-999"): the prefetch must not fail the turn. Pinned in Task 9.
5. **Overlapping (parallel) model calls,** e.g. two people's computations: they count as one sequential call. Pinned in Task 1.

---

## File structure

| File | Responsibility |
|---|---|
| `src/pension_chat/agent/budgets.py` (new) | Budget table, model-node registry, turn classifier, sequential counter, repeated-section measure, per-turn record |
| `src/pension_chat/agent/graph.py` | Starts an instrument per turn when none is running; logs and attaches the budget record |
| `src/pension_chat/agent/state.py` | `turn_budget` and `unused_stated` keys |
| `src/pension_chat/evals/trace.py`, `cases.py`, `checks.py`, `report.py`, `runner.py`, `timing.py` | Trace fields, the `model_calls_max` check, the `L1` family, p50/p95 per turn type, `--repeat`, the PBMS probe |
| `src/pension_chat/pbms/connection.py` | Times PBMS reads |
| `scripts/run_evals.py` | `--repeat` |
| `evals/cases/L1/*.yaml` (new) | The latency suite |
| `src/pension_chat/agent/tools.py` | The `stated` argument |
| `src/pension_chat/agent/nodes.py` | Verifies and uses `stated`; the separate extraction call behind a setting |
| `src/pension_chat/config.py` | `extract_with_separate_call` |
| `src/pension_chat/agent/loop.py` | Coverage tool offered by rule; named sections prefetched; each section's text once |
| `src/pension_chat/agent/retrieval.py`, `ingestion/indexer.py` | Integrated vectorizer (Task 11) |
| `tests/test_budgets.py`, `tests/test_budget_registry.py`, plus the existing test files named in each task | Tests |

---

### Task 1: The budget module

**Files:**
- Create: `src/pension_chat/agent/budgets.py`
- Test: `tests/test_budgets.py`

**Interfaces:**
- Produces:
  - `MODEL_NODES: dict[str, str]`
  - `BUDGETS: dict[str, tuple[int, frozenset[str]]]`
  - `ModelCall(node: str, start: float, end: float)`
  - `sequential_model_calls(calls: Iterable[ModelCall], first_token_at: float | None) -> int`
  - `classify_turn(*, tools: Iterable[str], model_nodes: Iterable[str], people: int, has_figure: bool, asked: bool) -> str`
  - `over_budget(turn_type: str, sequential: int, model_nodes: Iterable[str]) -> list[str]`
  - `repeated_sections(messages: Iterable[Mapping[str, Any]]) -> dict[str, int]`
  - `turn_budget_record(state: Mapping[str, Any], calls: Iterable[Any], started: float, first_token_at: float | None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_budgets.py
"""The model-call budget: counting, classifying, and the per-turn record."""
from __future__ import annotations

from types import SimpleNamespace

from pension_chat.agent.budgets import (
    BUDGETS, MODEL_NODES, ModelCall, classify_turn, over_budget, repeated_sections,
    sequential_model_calls, turn_budget_record,
)


def test_calls_one_after_another_each_count() -> None:
    calls = [ModelCall("investigate", 0.0, 1.4), ModelCall("generate_answer", 1.5, 2.9)]
    assert sequential_model_calls(calls, first_token_at=None) == 2


def test_overlapping_calls_count_once() -> None:
    # Two people's computations issued together are one wait, not two.
    calls = [ModelCall("investigate", 0.0, 1.4), ModelCall("derive_recipe", 1.5, 3.0),
             ModelCall("derive_recipe", 1.6, 3.2), ModelCall("generate_answer", 3.3, 4.6)]
    assert sequential_model_calls(calls, first_token_at=None) == 3


def test_calls_after_the_first_answer_word_do_not_count() -> None:
    calls = [ModelCall("investigate", 0.0, 1.4), ModelCall("generate_answer", 1.5, 9.0),
             ModelCall("repair_answer", 9.5, 11.0)]
    assert sequential_model_calls(calls, first_token_at=2.9) == 2


def test_turn_types_come_from_what_the_turn_did() -> None:
    assert classify_turn(tools=["search_statute"], model_nodes=["investigate", "generate_answer"],
                         people=0, has_figure=False, asked=False) == "statute"
    assert classify_turn(tools=["search_statute", "compute_benefit"], model_nodes=[], people=1,
                         has_figure=True, asked=False) == "figure"
    assert classify_turn(tools=["compute_benefit"], model_nodes=[], people=0,
                         has_figure=False, asked=True) == "asks"
    assert classify_turn(tools=["compute_benefit"], model_nodes=[], people=2,
                         has_figure=True, asked=False) == "comparison"
    assert classify_turn(tools=["check_coverage"], model_nodes=["assess_coverage"], people=0,
                         has_figure=False, asked=False) == "self_described"
    assert classify_turn(tools=["compute_benefit"], model_nodes=["derive_recipe"], people=0,
                         has_figure=True, asked=False) == "derivation"


def test_over_budget_names_the_count_and_any_stray_node() -> None:
    assert over_budget("figure", 2, ["investigate", "generate_answer"]) == []
    problems = over_budget("figure", 3, ["investigate", "resolve_inputs", "generate_answer"])
    assert "3 sequential model calls, budget 2" in problems[0]
    assert "resolve_inputs" in problems[1]


def test_every_budgeted_node_is_registered() -> None:
    for turn_type, (_, nodes) in BUDGETS.items():
        assert nodes <= set(MODEL_NODES), turn_type


def test_repeated_section_text_is_measured() -> None:
    body = "x" * 400
    messages = [
        {"role": "system", "content": f"### 40 ILCS 5/8-138 — Minimum annuities\n{body}"},
        {"role": "tool", "content": f"### 40 ILCS 5/8-138 — Minimum annuities\n{body}\n### 40 ILCS 5/1-160 — Tier 2\n{body}"},
    ]
    repeated = repeated_sections(messages)
    assert list(repeated) == ["8-138"] and repeated["8-138"] > 90


def test_the_turn_record_reads_state_and_calls() -> None:
    calls = [SimpleNamespace(kind="embedding", label="query", start=100.0, end=100.5),
             SimpleNamespace(kind="model", label="investigate", start=100.6, end=102.0),
             SimpleNamespace(kind="model", label="generate_answer", start=102.1, end=104.0)]
    state = {"tool_calls_spent": {"search_statute": 1}, "people": {}, "calculation_result": {},
             "missing_inputs": [], "answer_messages": []}
    record = turn_budget_record(state, calls, started=100.0, first_token_at=3.5)
    assert record == {"turn_type": "statute", "sequential_model_calls": 2,
                      "model_nodes": ["investigate", "generate_answer"], "over_budget": [],
                      "repeated_sections": {}}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_budgets.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'pension_chat.agent.budgets'`.

- [ ] **Step 3: Write the module**

```python
# src/pension_chat/agent/budgets.py
"""The model-call budget: which model calls a turn may make before its first answer word.

On gpt-6-luna each call costs about 1.4 s server-side whatever its size
(measured 2026-10-05), so latency is set by how many calls run one after
another. Every call on today's hot path arrived with a feature; this module is
where a new one has to be declared (spec §3 P1/P2), and where a turn's record
of what it actually did is built.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: Every node that may call the model. A call site naming anything else fails
#: tests/test_budget_registry.py.
MODEL_NODES: dict[str, str] = {
    "investigate": "the decision call: read, search, compute, or stop",
    "generate_answer": "writes the answer; its first token ends the clock",
    "repair_answer": "rewrites an answer verification rejected; always after the first token",
    "assess_coverage": "coverage, only when the member described themselves (exception: self_described)",
    "derive_recipe": "derives a formula no stored one covers (exception: derivation)",
    "resolve_inputs": "the separate extraction call; off unless settings.extract_with_separate_call",
    "eval_judge": "the eval harness's judge; never part of a turn",
}

#: turn type -> (most sequential model calls before the first answer word, nodes allowed)
BUDGETS: dict[str, tuple[int, frozenset[str]]] = {
    "statute": (2, frozenset({"investigate", "generate_answer"})),
    "figure": (2, frozenset({"investigate", "generate_answer"})),
    "asks": (2, frozenset({"investigate", "generate_answer"})),
    "comparison": (2, frozenset({"investigate", "generate_answer"})),
    "self_described": (3, frozenset({"investigate", "assess_coverage", "generate_answer"})),
    "derivation": (3, frozenset({"investigate", "derive_recipe", "generate_answer"})),
}


@dataclass(frozen=True)
class ModelCall:
    node: str
    start: float
    end: float


def sequential_model_calls(calls: Iterable[ModelCall], first_token_at: float | None) -> int:
    """How many model calls ran one after another before the first answer word.

    Calls that overlap in time count once: two people's computations issued
    together are one wait.
    """
    spans = sorted(
        (c.start, c.end) for c in calls if first_token_at is None or c.start < first_token_at
    )
    waves, reach = 0, float("-inf")
    for start, end in spans:
        if start >= reach:
            waves += 1
            reach = end
        else:
            reach = max(reach, end)
    return waves


def classify_turn(*, tools: Iterable[str], model_nodes: Iterable[str], people: int,
                  has_figure: bool, asked: bool) -> str:
    """The turn type, read from what the turn did -- never declared by hand."""
    used, nodes = set(tools), set(model_nodes)
    if "derive_recipe" in nodes:
        return "derivation"
    if "assess_coverage" in nodes or "check_coverage" in used:
        return "self_described"
    if people > 1:
        return "comparison"
    if has_figure:
        return "figure"
    if "compute_benefit" in used:
        return "asks" if asked else "figure"
    return "statute"


def over_budget(turn_type: str, sequential: int, model_nodes: Iterable[str]) -> list[str]:
    limit, allowed = BUDGETS[turn_type]
    problems: list[str] = []
    if sequential > limit:
        problems.append(f"{sequential} sequential model calls, budget {limit}")
    stray = sorted(set(model_nodes) - allowed - {"repair_answer"})
    if stray:
        problems.append(f"nodes outside the {turn_type} budget: {', '.join(stray)}")
    return problems


_HEADING = re.compile(r"^### 40 ILCS 5/(\S+)", re.MULTILINE)


def repeated_sections(messages: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Sections whose text appears more than once in a prompt -> estimated repeated tokens."""
    seen: set[str] = set()
    repeated: dict[str, int] = {}
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        heads = list(_HEADING.finditer(content))
        for i, head in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(content)
            section = head.group(1)
            if section in seen:
                repeated[section] = repeated.get(section, 0) + (end - head.start()) // 4
            seen.add(section)
    return repeated


def _has_figure(state: Mapping[str, Any]) -> bool:
    for entry in (state.get("calculation_result") or {}).values():
        entry = entry or {}
        if (entry.get("base") or {}).get("ok") or any(
            s.get("annual_amount") for s in entry.get("scenarios") or []
        ):
            return True
    return False


def turn_budget_record(state: Mapping[str, Any], calls: Iterable[Any], started: float,
                       first_token_at: float | None) -> dict[str, Any]:
    """One turn's budget record: its type, its sequential model calls, and any breach.

    ``calls`` are `instrument.Call`s (absolute perf_counter times); ``first_token_at``
    is relative to ``started``, as `run_agent_stream` keeps it.
    """
    first_abs = None if first_token_at is None else started + first_token_at
    model = [ModelCall(str(c.label), float(c.start), float(c.end)) for c in calls
             if c.kind == "model" and (first_abs is None or c.start < first_abs)]
    nodes = [m.node for m in sorted(model, key=lambda m: m.start)]
    turn_type = classify_turn(
        tools=list((state.get("tool_calls_spent") or {}).keys()),
        model_nodes=nodes,
        people=len(state.get("people") or {}),
        has_figure=_has_figure(state),
        asked=bool(state.get("missing_inputs")),
    )
    sequential = sequential_model_calls(model, None)
    return {
        "turn_type": turn_type,
        "sequential_model_calls": sequential,
        "model_nodes": nodes,
        "over_budget": over_budget(turn_type, sequential, nodes),
        "repeated_sections": repeated_sections(state.get("answer_messages") or []),
    }
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_budgets.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/pension_chat/agent/budgets.py tests/test_budgets.py
git add src/pension_chat/agent/budgets.py tests/test_budgets.py
git commit -m "Budgets: the model-call budget per turn type, a turn classifier read from what the turn did, and a sequential-call counter

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Every model call site registers its node

**Files:**
- Test: `tests/test_budget_registry.py`

**Interfaces:**
- Consumes: `budgets.MODEL_NODES` (Task 1).

- [ ] **Step 1: Write the test**

```python
# tests/test_budget_registry.py
"""A model call can't be added without registering its node in agent/budgets.py (spec §4.2).

Every call goes through llm.complete, llm.call_with_tools or llm.stream with a
literal node= name. This scans src/ for those calls; a new node fails here until
it is added to MODEL_NODES -- and, if it runs before the first answer word, to a
turn type's budget -- in the same change, where review sees it.
"""
from __future__ import annotations

import ast
from pathlib import Path

from pension_chat.agent.budgets import MODEL_NODES

SRC = Path(__file__).resolve().parents[1] / "src" / "pension_chat"
ENTRY_POINTS = {"complete", "call_with_tools", "stream", "stream_llm"}


def _call_sites() -> list[tuple[str, int, str | None]]:
    sites = []
    for path in SRC.rglob("*.py"):
        if path.name == "llm.py":   # the entry points themselves forward node=node
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in ENTRY_POINTS:
                continue
            keyword = next((k.value for k in node.keywords if k.arg == "node"), None)
            literal = keyword.value if isinstance(keyword, ast.Constant) else None
            sites.append((str(path.relative_to(SRC)), node.lineno, literal))
    return sites


def test_every_model_call_names_a_registered_node() -> None:
    sites = _call_sites()
    assert sites, "the scan found no model calls -- the entry-point names have changed"
    unnamed = [f"{p}:{line}" for p, line, node in sites if node is None]
    assert not unnamed, f"model calls without a literal node= name: {unnamed}"
    unknown = [f"{p}:{line} node={node!r}" for p, line, node in sites if node not in MODEL_NODES]
    assert not unknown, f"register these in agent/budgets.py MODEL_NODES: {unknown}"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_budget_registry.py -q`
Expected: PASS. It must report at least the six non-eval nodes plus `eval_judge`.

If it reports a call site without `node=` that isn't a model call (a method coincidentally named `stream` or `complete`), add that file's relative path to a `NOT_MODEL_CALLS = {"…"}` set in the test, with a comment naming what it calls, and skip it in `_call_sites`.

- [ ] **Step 3: Prove the guard fires**

Temporarily add `await complete(node="sneaky", messages=[])` inside any function body in `src/pension_chat/agent/memory.py`.
Run: `uv run pytest tests/test_budget_registry.py -q`
Expected: FAIL naming `node='sneaky'`. Then remove the line.

- [ ] **Step 4: Commit**

```bash
git add tests/test_budget_registry.py
git commit -m "Budgets: a model call can't be added without registering its node

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Every turn records and logs its budget

**Files:**
- Modify: `src/pension_chat/agent/graph.py` (`run_agent_stream`: start, short-circuit path, done path)
- Modify: `src/pension_chat/agent/state.py` (`PensionAgentState`, `initial_state`)
- Test: `tests/test_view_events.py` (extend; its `_run` helper already fakes the graph and the stream)

**Interfaces:**
- Consumes: `budgets.turn_budget_record` (Task 1); `instrument.current()` and `instrument.begin()`.
- Produces: `state["turn_budget"]` on the `done` event (Task 4 reads it), and the log line `turn_budget {json}`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_view_events.py`)

```python
async def test_the_done_state_carries_the_turn_budget(monkeypatch):
    events = await _run(monkeypatch, {})
    done = next(e for e in events if e["type"] == "done")
    budget = done["state"]["turn_budget"]
    assert budget["turn_type"] == "statute"
    assert set(budget) == {"turn_type", "sequential_model_calls", "model_nodes", "over_budget",
                           "repeated_sections"}
```

Before writing it, read `_run`'s signature at the top of the file. If it takes the state as its second positional argument (as `_run(monkeypatch, views)` there suggests), pass the minimal state it expects.

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/test_view_events.py -q -k turn_budget`
Expected: FAIL with `KeyError: 'turn_budget'`.

- [ ] **Step 3: Implement**

In `src/pension_chat/agent/state.py`, add to `PensionAgentState` (beside `unavailable_people`):

```python
    # This turn's model-call budget record (agent/budgets.py): its type, its
    # sequential model calls, and any breach. Logged; read by the eval trace.
    turn_budget: dict[str, Any] | None
```

and to `initial_state`'s returned dict: `"turn_budget": None,`.

In `src/pension_chat/agent/graph.py`:
1. Add to the imports: `from pension_chat.agent import instrument` and `from pension_chat.agent.budgets import turn_budget_record`. Add `import json` if absent.
2. In `run_agent_stream`, right after `started = time.perf_counter()`:

```python
    # A record of this turn's remote calls, so its budget can be logged in
    # production too. The eval harness starts its own; reuse it when present.
    turn_instrument = instrument.current() or instrument.begin()
```

3. Add this helper near `_defer_record`:

```python
def _record_budget(state: dict[str, Any], calls: Any, started: float,
                   first_token_at: float | None) -> None:
    """Attach and log the turn's budget record (spec §5.1). Never raises."""
    try:
        state["turn_budget"] = turn_budget_record(state, calls, started, first_token_at)
        logger.info("turn_budget %s", json.dumps(state["turn_budget"]))
    except Exception:
        logger.exception("Could not record the turn budget")
```

4. In the short-circuit branch (`if state.get("final_answer"):`), before its `yield {"type": "done", ...}`:

```python
        _record_budget(state, turn_instrument.calls, started, 0.0)
```

5. In the main path, after `state["timings"] = {...}` and before the cards block:

```python
    _record_budget(state, turn_instrument.calls, started, first_token_at)
```

- [ ] **Step 4: Run the test and the full suite**

Run: `uv run pytest tests/test_view_events.py -q && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/pension_chat/agent/graph.py src/pension_chat/agent/state.py tests/test_view_events.py
git add src/pension_chat/agent/graph.py src/pension_chat/agent/state.py tests/test_view_events.py
git commit -m "Budgets: every turn records and logs its type, sequential model calls and any breach

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The eval trace, the `model_calls_max` check, the L1 family, and p50/p95 per turn type

**Files:**
- Modify: `src/pension_chat/evals/trace.py` (`TurnTrace`, `build_trace`)
- Modify: `src/pension_chat/evals/cases.py` (`FAMILIES`, `FIRST_CLASS`, `CHECKS`)
- Modify: `src/pension_chat/evals/checks.py` (`_check`)
- Modify: `src/pension_chat/evals/report.py` (`summarise`, `diff`, `render_markdown`)
- Test: `tests/test_evals_checks.py`, `tests/test_evals_report.py` (extend)

**Interfaces:**
- Consumes: `state["turn_budget"]` (Task 3).
- Produces:
  - `TurnTrace.turn_type: str | None`, `TurnTrace.model_calls: int | None`, `TurnTrace.repeated_sections: dict[str, int]`, `TurnTrace.unused_stated: dict[str, Any]`;
  - report key `"latency_by_turn_type": {type: {"measured": {"p50","p95","n"}, "estimated": {...}}}`;
  - report key `"prompt_tokens": {node: median}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evals_checks.py`:

```python
def test_model_calls_max_reads_the_trace():
    from pension_chat.evals.cases import Turn
    from pension_chat.evals.checks import run_checks
    from pension_chat.evals.trace import TurnTrace

    turn = Turn(q="q", goal="g", reference="r", checks={"model_calls_max": 2})
    ok = run_checks(turn, TurnTrace(case_id="c", turn_index=0, question="q", employee=None, model_calls=2))
    over = run_checks(turn, TurnTrace(case_id="c", turn_index=0, question="q", employee=None, model_calls=3))
    assert ok[0].passed and not over[0].passed and "3" in over[0].detail
```

Append to `tests/test_evals_report.py` (reuse that file's existing helpers for building a `ConversationResult`; if it has none, build one directly as shown):

```python
def test_latency_is_reported_per_turn_type_with_p95():
    from pension_chat.evals.cases import Case, Turn
    from pension_chat.evals.report import summarise
    from pension_chat.evals.runner import ConversationResult, TurnResult
    from pension_chat.evals.trace import TurnTrace

    case = Case(id="L1-001", family="L1", areas=["retrieval"],
                turns=[Turn(q="q", goal="g", reference="r", checks={})], path=None)
    turns = []
    for i, t in enumerate((2.0, 2.5, 3.0, 6.0)):
        trace = TurnTrace(case_id="L1-001", turn_index=0, question="q", employee=None,
                          ttft=t, turn_type="statute", usage={"by_node": {"investigate": {"prompt": 10000}}})
        turns.append(TurnResult("L1-001", 0, trace, [], None, "pass", t - 1.0, {}))
    report = summarise([ConversationResult(case, [tr]) for tr in turns], judge_calibrated=False, probe={})
    slot = report["latency_by_turn_type"]["statute"]
    assert slot["measured"]["n"] == 4 and slot["measured"]["p95"] == 6.0
    assert report["prompt_tokens"]["investigate"] == 10000
```

If `Case(...)`'s required fields differ from these, read the `Case` dataclass in `cases.py` and pass what it requires. The `TurnResult` positional order is `(case_id, turn_index, trace, checks, verdict, status, estimate, …)`; confirm it against the dataclass before running.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_evals_checks.py tests/test_evals_report.py -q`
Expected: FAIL on `model_calls`, `turn_type` (unexpected keyword) or `"latency_by_turn_type"` (KeyError).

- [ ] **Step 3: Implement**

`evals/trace.py`: add to `TurnTrace` after `usage`:

```python
    #: The turn's budget record (agent/budgets.py): what kind of turn it was,
    #: and how many model calls ran one after another before the first word.
    turn_type: str | None = None
    model_calls: int | None = None
    #: Sections whose text the answer prompt carried more than once -> repeated tokens.
    repeated_sections: dict[str, int] = field(default_factory=dict)
    #: Stated values the model passed under names no formula reads (Task 7).
    unused_stated: dict[str, Any] = field(default_factory=dict)
```

In `build_trace`, in the `elif kind == "done":` branch after `trace.usage = ...`:

```python
            budget = state.get("turn_budget") or {}
            trace.turn_type = budget.get("turn_type")
            trace.model_calls = budget.get("sequential_model_calls")
            trace.repeated_sections = dict(budget.get("repeated_sections") or {})
            trace.unused_stated = dict(state.get("unused_stated") or {})
```

`evals/cases.py`:
- `FAMILIES = (…, "S13", "L1")`
- `FIRST_CLASS = ("S1", "S4", "S4b", "S11", "S13", "L1")`
- add `"model_calls_max": int,` to `CHECKS`.

`evals/checks.py`, in `_check` before `raise KeyError(name)`:

```python
    if name == "model_calls_max":
        calls = trace.model_calls
        return CheckResult(name, calls is not None and calls <= value,
                           f"{calls} sequential model calls before the first word vs {value}")
```

`evals/report.py`, in `summarise`:
- Beside `latency`, add:

  ```python
  by_type: dict[str, dict[str, list[float]]] = {}
  prompt: dict[str, list[int]] = {}
  ```

- Inside the per-turn loop, after the `ttft_class` block:

  ```python
            if tr.trace.turn_type and tr.trace.ttft is not None:
                slot = by_type.setdefault(tr.trace.turn_type, {"measured": [], "estimated": []})
                slot["measured"].append(tr.trace.ttft)
                if tr.estimate is not None:
                    slot["estimated"].append(tr.estimate)
            for node, used in ((tr.trace.usage or {}).get("by_node") or {}).items():
                if used.get("prompt"):
                    prompt.setdefault(node, []).append(int(used["prompt"]))
  ```

- In the returned dict, add:

  ```python
        "latency_by_turn_type": {t: {kind: {"p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "n": len(v)}
                                     for kind, v in slot.items()} for t, slot in by_type.items()},
        "prompt_tokens": {node: int(statistics.median(v)) for node, v in prompt.items()},
  ```

In `diff`, add estimated-p50 and p95 moves per turn type. Before `return`:

```python
    type_moves = {}
    for t, slot in report.get("latency_by_turn_type", {}).items():
        old = baseline.get("latency_by_turn_type", {}).get(t, {}).get("estimated", {})
        new = slot.get("estimated", {})
        if old.get("p50") is not None and new.get("p50") is not None:
            type_moves[t] = {"p50": round(new["p50"] - old["p50"], 2),
                             "p95": round((new.get("p95") or 0) - (old.get("p95") or 0), 2)}
```

and return `"type_moves": type_moves` in both the dict and the `no_baseline` dict (as `{}` there).

In `render_markdown`, after the per-class latency lines:

```python
    lines += ["", "## Latency by turn type (estimated in-region p50 / p95, s)"]
    for t, slot in report.get("latency_by_turn_type", {}).items():
        e, m = slot.get("estimated", {}), slot.get("measured", {})
        lines.append(f"- {t}: estimated {e.get('p50')} / {e.get('p95')}; measured {m.get('p50')} / {m.get('p95')} (n={m.get('n')})")
    lines.append(f"Prompt tokens per call (median): {report.get('prompt_tokens') or {}}")
    if not changes.get("no_baseline"):
        lines.append(f"Turn-type moves vs baseline (s): {changes.get('type_moves') or 'none'}")
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `uv run pytest tests/test_evals_checks.py tests/test_evals_report.py -q && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/pension_chat/evals tests/test_evals_checks.py tests/test_evals_report.py
git add src/pension_chat/evals tests/test_evals_checks.py tests/test_evals_report.py
git commit -m "Evals: the budget on every trace, a model_calls_max check, the L1 family, and p50/p95 per turn type

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `--repeat`, and PBMS in the timing

**Files:**
- Modify: `src/pension_chat/evals/runner.py` (`RunOptions`, `ConversationResult`, `run_suite`)
- Modify: `src/pension_chat/evals/report.py` (`summarise` reads repeats)
- Modify: `src/pension_chat/evals/timing.py` (`KIND_ENDPOINT`, `probe`)
- Modify: `src/pension_chat/pbms/connection.py` (`fetch` is timed)
- Modify: `scripts/run_evals.py` (flag)
- Test: `tests/test_evals_runner.py`, `tests/test_evals_timing.py` (extend)

**Interfaces:**
- Produces:
  - `RunOptions.repeat: int = 1`;
  - `ConversationResult.repeats: list[ConversationResult]`;
  - instrument calls of kind `"pbms"`;
  - `probe(...)["pbms"]` when `settings.pbms_sql_server` is set.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evals_runner.py`. Use that file's existing fake agent; if it has none, use the one shown:

```python
async def test_repeat_runs_add_timing_only_runs(monkeypatch):
    from pension_chat.evals import runner
    from pension_chat.evals.cases import Case, Turn

    runs = []

    async def fake_conversation(case, **kwargs):
        runs.append(kwargs["options"].judge)
        return runner.ConversationResult(case, [])

    monkeypatch.setattr(runner, "run_conversation", fake_conversation)
    case = Case(id="L1-001", family="L1", areas=["retrieval"],
                turns=[Turn(q="q", goal="g", reference="r", checks={})], path=None)
    results = await runner.run_suite([case], fixtures={}, probe={},
                                     options=runner.RunOptions(repeat=3, concurrency=1))
    assert len(runs) == 3 and runs[1:] == [False, False]   # repeats are not judged
    assert len(results[0].repeats) == 2
```

Append to `tests/test_evals_timing.py`:

```python
def test_pbms_calls_count_as_pbms_round_trips():
    from pension_chat.evals.timing import KIND_ENDPOINT
    assert KIND_ENDPOINT["pbms"] == "pbms"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_evals_runner.py tests/test_evals_timing.py -q`
Expected: FAIL (`repeat` unexpected keyword; `KeyError: 'pbms'`).

- [ ] **Step 3: Implement**

`runner.py`:
- Add `repeat: int = 1` to `RunOptions`, with the comment: "Timing-only re-runs of every case, so p95 means something; not judged, not checked for pass/fail."
- Add `repeats: list["ConversationResult"] = field(default_factory=list)` to `ConversationResult`.
- At the end of `run_suite`, before `return results`:

```python
    if options.repeat > 1:
        quiet = dataclasses.replace(options, judge=False, judge_all=False, progress=False)
        for result in results:
            for _ in range(options.repeat - 1):
                async with gate:
                    try:
                        result.repeats.append(await run_conversation(
                            result.case, fixtures=fixtures, probe=probe, options=quiet, agent=agent))
                    except Exception:
                        logger.exception("Timing repeat failed for %s", result.case.id)
```

Add `import dataclasses` at the top. If `logger` isn't defined in `runner.py`, add `logger = logging.getLogger(__name__)` with `import logging`.

`report.py`, in `summarise`: the per-turn-type latency also reads `conv.repeats`. Before the main loop, define:

```python
    def _latency_turns(conv: ConversationResult):
        yield from conv.turns
        for again in conv.repeats:
            yield from again.turns
```

Then move the `by_type` and `prompt` accumulation into a separate loop:

```python
    for conv in results:
        for tr in _latency_turns(conv):
            ...  # the by_type and prompt lines from Task 4
```

Leave pass/fail and the per-class latency on `conv.turns` only.

`timing.py`:
- `KIND_ENDPOINT` gains `"pbms": "pbms"`.
- In `probe`, before `return out`:

```python
    if getattr(settings, "pbms_sql_server", None):
        host, _, port = str(settings.pbms_sql_server).partition(",")
        try:
            timings = []
            for _ in range(2):
                t = time.perf_counter()
                _, writer = await asyncio.wait_for(asyncio.open_connection(host.strip(), int(port or 1433)), 10)
                timings.append(time.perf_counter() - t)
                writer.close()
            out["pbms"] = {"cold": timings[0], "warm": timings[1]}
        except Exception as exc:
            raise ProbeError(f"pbms unreachable at {host}: {exc}") from exc
```

Add `import asyncio` at the top of `timing.py`.

`pbms/connection.py`, in `fetch`, wrap the `wait_for`:

```python
        try:
            async with instrument.timed("pbms", name):
                return await asyncio.wait_for(
                    asyncio.to_thread(self._run, name, query, args, timeout), timeout=timeout * 2 + 1
                )
```

with `from pension_chat.agent import instrument` at the top. Check for an import cycle by running `uv run python -c "import pension_chat.pbms.connection"`.

`scripts/run_evals.py`:
- `p.add_argument("--repeat", type=int, default=1, help="timing-only re-runs per case, for p95")`;
- pass `repeat=args.repeat` into `RunOptions(...)`.

- [ ] **Step 4: Run the tests and the full suite**

Run: `uv run pytest tests/test_evals_runner.py tests/test_evals_timing.py -q && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/pension_chat/evals src/pension_chat/pbms/connection.py scripts/run_evals.py tests/test_evals_runner.py tests/test_evals_timing.py
git add src/pension_chat/evals src/pension_chat/pbms/connection.py scripts/run_evals.py tests/test_evals_runner.py tests/test_evals_timing.py
git commit -m "Evals: --repeat for p95, and PBMS round trips in the probe and the estimate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The L1 latency suite, and the baseline

**Files:**
- Create: `evals/cases/L1/L1-001.yaml` … `L1-014.yaml`
- Test: `tests/test_evals_cases.py` (the existing loader test picks up new files; add one assertion)

**Interfaces:**
- Consumes: the `L1` family and `model_calls_max` (Task 4); the fixtures `tier2_at_62`, `people_a`, `people_b` in `evals/fixtures/employees/`.

- [ ] **Step 1: Write the cases**

One file per case. Fourteen cases, at least two per turn type. The questions come from `demo-script.md`, which were verified on 2026-10-01 and 2026-10-05.

```yaml
# evals/cases/L1/L1-001.yaml
id: L1-001
family: L1
areas: [retrieval, citations]
tiers: [smoke, full]
turns:
- q: How is the retirement annuity calculated under Section 8-138(g-1)?
  goal: The (g-1) formula from the statute.
  reference: '2.4% of final average salary per year of service (2.2% before 2002), at least 10 years, from age 60; final average salary is the best 4 consecutive years in the last 10 (§8-138(g-1)).'
  checks: {cites_any: ['8-138'], must_state: ['2.4%', '60'], model_calls_max: 2, ttft_class: simple}
```

Write the other thirteen the same way. Each turn gets `model_calls_max` from the budget table and `ttft_class` (`simple` or `heavy`):

| id | turns (q) | type | checks beyond `model_calls_max` / `ttft_class` |
|---|---|---|---|
| L1-002 | What is the difference between Tier 1 and Tier 2? | statute | `cites_any: ['1-160']`, `must_state: ['2011']` |
| L1-003 | Who counts as an employee under Section 8-113? | statute | `cites_any: ['8-113']` |
| L1-004 | What is the maximum pension under Section 8-138(g-1)? | statute | `cites_any: ['8-138']`, `must_state: ['80%']` |
| L1-005 | What does Section 8-137 say about automatic increases after retirement? | statute | `cites_any: ['8-137']`, `must_state: ['3%']` |
| L1-006 | A member who joined in 2005 is 61, has 10 years of service and an average salary of $50,000. What would they get if they retired today? / What if they retire 5 years later? | figure, figure | turn 1 `expected_figure: {annual: 12000.00}`; turn 2 `calculation: true` |
| L1-007 | An employee who joined in 2015 is 62, earns $80,000 and has 11 years of service. What is their annuity if they retire now? / What if they wait until 67? | figure, figure | turn 1 `expected_figure: {annual: 15417.60}`; turn 2 `calculation: true` |
| L1-008 | (`employee: tier2_at_62`) What would this employee's annuity be if they retired now? | figure | `expected_figure: {annual: 19770.89}` |
| L1-009 | (`employee: tier2_at_62`) What would this employee's annuity be if they retired now? / And at 67? | figure, figure | turn 2 `calculation: true` |
| L1-010 | What would someone aged 61 with 20 years of service get? / Their average salary is $70,000 | asks, figure | turn 1 `max_questions: 1`, `no_calculation: true`; turn 2 `calculation: true` |
| L1-011 | How much pension would an employee get? | asks | `no_calculation: true` |
| L1-012 | (`people: [people_a, people_b]`) Compare them if they both retired now | comparison | `calculation: true` |
| L1-013 | (`people: [people_a, people_b]`) Compare them at 65 | comparison | `calculation: true` |
| L1-014 | I am a park employee, how is my pension calculated? | self_described | `model_calls_max: 3` |

Use the multi-line style of `L1-001` for every file. For L1-012 and L1-013, open `evals/fixtures/employees/people_a.yaml` and `people_b.yaml` and refer to the people the way `evals/cases/S13/S13-002.yaml` does (copy its `people:` line and wording). For L1-008 and L1-009, copy `employee:` from `evals/cases/S4/S4-007.yaml`.

- [ ] **Step 2: Assert the suite has every turn type**

Append to `tests/test_evals_cases.py`:

```python
def test_the_latency_suite_covers_every_turn_type_twice():
    from pathlib import Path

    from pension_chat.evals.cases import load_cases
    fixtures_dir = Path("evals/fixtures/employees")
    cases = [c for c in load_cases(Path("evals/cases"), {p.stem for p in fixtures_dir.glob("*.yaml")})
             if c.family == "L1"]
    assert len(cases) >= 12
    assert all("model_calls_max" in t.checks for c in cases for t in c.turns)
```

If `load_cases`' second argument has a different type, read its signature in `cases.py` and match it.

- [ ] **Step 3: Run the loader tests**

Run: `uv run pytest tests/test_evals_cases.py -q`
Expected: PASS. The loader rejects unknown checks, families or fixtures; fix any file it names.

- [ ] **Step 4: Record the baseline (live; one at a time; nothing else using the model)**

Run:

```bash
uv run python scripts/run_evals.py --family L1 --concurrency 1 --repeat 3 --accept
uv run python scripts/run_evals.py --family S4 --family S4b --family S11 --family S13 --concurrency 1
```

Expected:
- The report prints "Latency by turn type" with estimated p50/p95 per type, and "Prompt tokens per call".
- Note the L1 pass count and the S-family pass counts in the commit message; they're the gate's baseline.
- Turns over budget are expected now (the extraction call). They show as `model_calls_max` failures, and the baseline records them.

- [ ] **Step 5: Commit**

```bash
git add evals/cases/L1 tests/test_evals_cases.py evals/baseline.json
git commit -m "Evals: L1, the latency suite -- two or more cases per turn type -- and its baseline

Baseline (estimated in-region p50/p95 per turn type): <paste the report's lines>
S4/S4b/S11/S13 pass counts: <paste>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Stated values travel with the decision call

**Files:**
- Modify: `src/pension_chat/agent/tools.py` (`compute_benefit` schema in `CALCULATION_TOOLS`; `_compute`)
- Modify: `src/pension_chat/agent/nodes.py` (`resolve_inputs`; new helper `_stated_from_model`)
- Modify: `src/pension_chat/config.py` (`extract_with_separate_call`)
- Modify: `src/pension_chat/agent/state.py` (`stated_by_model`, `unused_stated`)
- Test: `tests/test_stated_values.py` (extend)

**Interfaces:**
- Consumes: `nodes._stated_in(value, text) -> bool` (accepts "$85k"); `nodes._member_words(state) -> str`; `facts.aliases_of(name) -> frozenset[str]`.
- Produces:
  - `compute_benefit` argument `stated: dict[str, number | string]`;
  - state key `stated_by_model` (per call, set by `_compute`);
  - `resolve_inputs` output key `unused_stated: dict`;
  - `settings.extract_with_separate_call: bool = False`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_stated_values.py`)

```python
import pytest
from langchain_core.messages import HumanMessage

from pension_chat.agent import nodes

_SPEC = {"formula_id": "f", "execution_type": "declarative",
         "inputs": [{"name": "service_years", "type": "years", "required": True},
                    {"name": "final_average_salary", "type": "money", "required": True},
                    {"name": "age_at_annuity_start", "type": "integer", "required": True}],
         "parameters": {}, "calculation": {"variable": "service_years"},
         "output_name": "annual_annuity", "output_type": "money"}


def _state(said: str, stated: dict, record: dict | None = None) -> dict:
    return {"employee_record": None, "member_facts": record or {}, "fact_ledger": {}, "salary_history": [],
            "question": said, "messages": [HumanMessage(content=said)], "turn_index": 1,
            "coverage": {"article": "8", "basis": "deployment", "record_article": None},
            "recipes": {"retirement_annuity": {"source": "registry", "spec": _SPEC}},
            "requires_calculation": True, "assumptions": {}, "stated_by_model": stated}


@pytest.fixture
def no_model(monkeypatch):
    async def refuse(**kwargs):
        raise AssertionError("resolve_inputs called the model")
    monkeypatch.setattr(nodes, "complete", refuse)


async def test_stated_values_reach_the_engine_without_a_model_call(no_model):
    said = "A member who joined in 2005 is 61, has 10 years of service and an average salary of $50,000."
    out = await nodes.resolve_inputs(_state(said, {"age": 61, "service_years": 10, "final_average_salary": 50000,
                                                   "year_joined": 2005}))
    assert out["resolved_inputs"]["service_years"] == 10
    assert out["resolved_inputs"]["age_at_annuity_start"] == 61
    assert out["input_sources"]["service_years"] == "stated"


async def test_a_value_the_user_never_wrote_is_rejected(no_model):
    said = "A member who joined in 2015 has 11 years of service."
    out = await nodes.resolve_inputs(_state(said, {"age": 62, "service_years": 11}))
    assert out["resolved_inputs"].get("age_at_annuity_start") is None    # "62" was never written
    assert out["resolved_inputs"]["service_years"] == 11


async def test_a_correction_overrides_the_record(no_model):
    said = "Actually it's 23 years of service, not 21.5."
    out = await nodes.resolve_inputs(_state(said, {"service_years": 23}, record={"service_years": 21.5}))
    assert out["resolved_inputs"]["service_years"] == 23


async def test_a_name_no_formula_reads_is_recorded_not_lost(no_model):
    said = "She is 61 with 10 years of service, salary $50,000 and a spouse aged 58."
    out = await nodes.resolve_inputs(_state(said, {"age": 61, "service_years": 10, "spouse_age": 58}))
    assert out["unused_stated"] == {"spouse_age": 58}


async def test_a_stated_salary_stands_in_for_the_average(no_model):
    said = "Someone aged 61 with 10 years of service earning $80k."
    out = await nodes.resolve_inputs(_state(said, {"age": 61, "service_years": 10, "salary": 80000}))
    assert out["resolved_inputs"]["final_average_salary"] == 80000
    assert out["input_sources"]["final_average_salary"] == "assumed"


async def test_the_separate_call_can_be_restored(monkeypatch):
    from pension_chat.config import get_settings
    called = []

    async def heard(**kwargs):
        called.append(1)
        return '{"resolved": {}}'

    monkeypatch.setattr(nodes, "complete", heard)
    monkeypatch.setattr(get_settings(), "extract_with_separate_call", True)
    await nodes.resolve_inputs(_state("I have 10 years of service.", {}))
    assert called == [1]
```

If `get_settings()` is cached and frozen, set the attribute with `monkeypatch.setattr(type(get_settings()), ...)` or patch `nodes.get_settings` to return `get_settings().model_copy(update={"extract_with_separate_call": True})`. Read `config.py` and pick whichever matches.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_stated_values.py -q -k "stated_values_reach or never_wrote or correction_overrides or no_formula or stands_in or can_be_restored"`
Expected: FAIL. The model is called (`AssertionError: resolve_inputs called the model`), or `unused_stated` is missing.

- [ ] **Step 3: Implement**

`config.py`, beside `reasoning_effort_extract`:

```python
    # The separate model call that read stated values out of the conversation.
    # Off: stated values now travel as compute_benefit's `stated` argument and are
    # verified in code (spec §5.2). On restores the old call, for rollback.
    extract_with_separate_call: bool = False
```

`state.py`, add to `PensionAgentState` and `initial_state` (default `{}` for both):

```python
    # What the model passed as compute_benefit's `stated`, for this computation only.
    stated_by_model: dict[str, Any]
    # Stated values under names no formula reads, recorded rather than lost.
    unused_stated: dict[str, Any]
```

`tools.py`, in the `compute_benefit` properties (after `stand_in_facts`):

```python
                    "stated": {
                        "type": "object",
                        "additionalProperties": {"type": ["number", "string"]},
                        "description": (
                            "Every value the user has stated in this conversation that "
                            "this figure could use, in plain names: age, service_years, "
                            "salary, final_average_salary, year_joined, and any other. "
                            "Only what they actually wrote, never a value you worked "
                            "out; include a correction they made ('actually 23 years'). "
                            "Each value is checked against their words."
                        ),
                    },
```

In `_compute`'s `slice_` dict, add:

```python
        # Checked against the user's own words in resolve_inputs; never trusted as given.
        "stated_by_model": dict(arguments.get("stated") or {}),
```

`nodes.py`: add this helper after `_latest_stated`:

```python
#: Plain names the model may use for the stated salary the missing-input
#: policy can take as the averaging-period salary.
_SALARY_NAMES = frozenset({"salary", "annual_salary", "current_salary", "pay"})


def _stated_from_model(
    stated: Mapping[str, Any], declared: Mapping[str, Any], spoken: str
) -> tuple[dict[str, Any], int | None, dict[str, Any]]:
    """The model's `stated` values, checked against the user's words and mapped to inputs.

    Returns (values for declared inputs, a stated salary for the averaging
    policy, values under names no declared input reads). A value the user did
    not write is dropped: the model may report what was said, never supply it.
    """
    for_inputs: dict[str, Any] = {}
    salary: int | None = None
    unused: dict[str, Any] = {}
    for name, value in (stated or {}).items():
        if value is None or not _stated_in(value, spoken):
            logger.info("Dropping stated %s=%r: not in the user's words", name, value)
            continue
        if name in _SALARY_NAMES:
            try:
                salary = int(float(value))
            except (TypeError, ValueError):
                unused[name] = value
            continue
        targets = [d for d in declared if d in aliases_of(name)]
        if not targets:
            unused[name] = value
            continue
        for target in targets:
            for_inputs[target] = value
    return for_inputs, salary, unused
```

In `resolve_inputs`, the block that starts at `if outstanding or question_has_figures or unheard:` and ends with the `for name, value in extracted.items(): … taken[name] = value` loop becomes:

```python
    model_stated, model_salary, unused_stated = _stated_from_model(
        state.get("stated_by_model") or {}, declared, spoken
    )
    extracted: dict[str, Any] = dict(model_stated)
    if settings.extract_with_separate_call and (outstanding or question_has_figures or unheard):
        # Rollback path: the separate model call, exactly as it was (spec §5.2).
        catalogue = [... unchanged ...]
        try:
            raw = await complete(... unchanged ...)
            extracted = {**json.loads(raw or "{}").get("resolved", {}), **extracted}
        except Exception as exc:
            logger.warning("Input extraction failed: %s", exc)

    for name, value in extracted.items():
        ... the existing loop body, unchanged ...
```

Keep the `catalogue` and `complete(...)` code exactly as it is today, only indented under the new condition. The loop moves out of the `if` so stated values always apply. `spoken` and `settings` are already defined above this block. Confirm `settings = get_settings()` exists at the top of `resolve_inputs`; add it if not.

Then, where the stated salary is read (`stated_salary = _latest_stated(_stated_annual_salary, state)`), change it to:

```python
        stated_salary = _latest_stated(_stated_annual_salary, state) or model_salary
```

and add `"unused_stated": unused_stated,` to the dict `resolve_inputs` returns.

- [ ] **Step 4: Run the tests, the continuity tests, and the full suite**

Run: `uv run pytest tests/test_stated_values.py tests/test_resolve_recipes_scenario.py tests/test_stated_survives.py -q && uv run pytest -q`
Expected: all pass.

An existing test that relied on the separate call running (patching `nodes.complete` to return extracted values) should now pass its values through `stated_by_model` instead. Update it to do so, keeping its assertion, and name it in the commit message.

- [ ] **Step 5: Gate (live)**

```bash
uv run python scripts/run_evals.py --family L1 --concurrency 1 --repeat 3
uv run python scripts/run_evals.py --family S4 --family S4b --family S11 --family S13 --concurrency 1
```

Expected:
- "figure" and "asks" estimated p50 drops by about 1.2–1.6 s, and their `model_calls_max` checks pass at 2;
- no new failures against the baseline;
- S11 (continuity) passes at its baseline rate.

If S11 regresses, the model is leaving corrections out of `stated`. Stop and report rather than re-enabling the call.

- [ ] **Step 6: Commit**

```bash
git add src/pension_chat/agent/tools.py src/pension_chat/agent/nodes.py src/pension_chat/config.py src/pension_chat/agent/state.py tests/test_stated_values.py
git commit -m "Inputs: stated values travel as compute_benefit's argument and are checked against the user's words; the separate extraction call leaves the hot path

L1 figure/asks p50: <before> -> <after> (estimated). S11: <rate>.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The coverage tool is offered only when the member describes themselves

**Files:**
- Modify: `src/pension_chat/agent/loop.py` (`_available`, the withdrawn-tool loop in `investigate`)
- Test: `tests/test_loop.py` (extend)

**Interfaces:**
- Consumes: `coverage_mod.classify_self_reference(question) -> SelfReference | None`.
- Produces: `loop._offered(self_described: bool) -> list[dict]`; `loop._available(spent, self_described) -> list[dict]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_loop.py`)

```python
async def test_coverage_is_not_offered_when_nobody_described_themselves(monkeypatch):
    seen: list = []
    _script(monkeypatch, [_turn(content="Here.")], calls_seen=seen)
    _tools(monkeypatch)
    await loop_mod.investigate({"question": "What is the Tier 2 retirement age?", "messages": []})
    offered = {t["function"]["name"] for t in seen[0]["tools"]}
    assert "check_coverage" not in offered and "search_statute" in offered


async def test_coverage_is_offered_when_the_member_describes_themselves(monkeypatch):
    seen: list = []
    _script(monkeypatch, [_turn(content="Here.")], calls_seen=seen)
    _tools(monkeypatch)
    await loop_mod.investigate({"question": "I am a park employee, how is my pension calculated?",
                                "messages": []})
    assert "check_coverage" in {t["function"]["name"] for t in seen[0]["tools"]}


async def test_an_unoffered_tool_is_not_announced_as_spent(monkeypatch):
    seen: list = []
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")],
            calls_seen=seen)
    _tools(monkeypatch)
    await loop_mod.investigate({"question": "What is the Tier 2 retirement age?", "messages": []})
    notes = [m["content"] for m in seen[-1]["messages"] if m.get("role") == "system"]
    assert not any("check_coverage calls left" in n for n in notes)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_loop.py -q -k "coverage_is or unoffered"`
Expected: the first and third FAIL (coverage offered; "no more check_coverage calls left" announced).

- [ ] **Step 3: Implement**

In `loop.py`, replace `_available`:

```python
def _offered(self_described: bool) -> list[dict[str, Any]]:
    """The tools this turn may use. Coverage is decided by rule (spec §5.3): the
    record or the deployment settles it, and the model is asked only when the
    member described themselves -- the rule that a question goes to the member
    only when the member spoke."""
    return [t for t in LOOP_TOOLS if self_described or t not in COVERAGE_TOOLS]


def _available(spent: dict[str, int], self_described: bool = True) -> list[dict[str, Any]]:
    return [
        tool
        for tool in _offered(self_described)
        if spent.get(tool["function"]["name"], 0) < BUDGETS.get(tool["function"]["name"], 0)
    ]
```

In `investigate`, before `prefetched: set[str] = set()`:

```python
    self_described = coverage_mod.classify_self_reference(state["question"]) is not None
```

Reuse it in the prefetch condition (`if not self_described:` replaces `if coverage_mod.classify_self_reference(state["question"]) is None:`). In the loop, `available = _available(spent, self_described)`, and the withdrawn loop iterates `for tool in _offered(self_described):` instead of `for tool in LOOP_TOOLS:`.

- [ ] **Step 4: Run the tests and the coverage tests**

Run: `uv run pytest tests/test_loop.py tests/test_tools_coverage.py tests/test_coverage.py tests/test_coverage_node.py -q && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Gate (live) and commit**

Run: `uv run python scripts/run_evals.py --family L1 --concurrency 1 --repeat 3`
Expected: L1-014 (self-described) still passes; no turn of another type calls `assess_coverage`; no new failures.

```bash
git add src/pension_chat/agent/loop.py tests/test_loop.py
git commit -m "Coverage: decided by rule; the coverage tool is offered only when the member describes themselves

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Prefetch the sections a question names

**Files:**
- Modify: `src/pension_chat/agent/loop.py` (`investigate`'s prefetch block)
- Test: `tests/test_prefetch.py` (extend)

**Interfaces:**
- Consumes: `state["explicit_sections"]` (set by `triage`); `loop._read_sections(targets, working) -> list[tuple[str, dict] | None]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_prefetch.py`; read its top for the fakes it already uses, and follow them)

```python
async def test_a_named_section_is_read_before_the_first_model_call(monkeypatch):
    from pension_chat.agent import loop as loop_mod
    seen: list = []
    read: list = []

    async def fake_call(**kwargs):
        seen.append([m["content"] for m in kwargs["messages"] if m.get("role") == "system"])
        from pension_chat.agent.llm import ToolTurn
        return ToolTurn(calls=(), content="Here.", raw_message={"role": "assistant", "content": "Here."})

    async def fake_run(name, arguments, state):
        return "searched text", {"retrieved_sections": [{"section": "1-160", "content": "t"}]}

    async def fake_read(targets, working):
        read.append(list(targets))
        return [(f"### 40 ILCS 5/{t} — title\ntext", {"retrieved_sections": [{"section": t, "content": "x"}]})
                for t in targets]

    monkeypatch.setattr(loop_mod, "call_with_tools", fake_call)
    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    monkeypatch.setattr(loop_mod, "_read_sections", fake_read)
    await loop_mod.investigate({"question": "How is the annuity calculated under Section 8-138(g-1)?",
                                "explicit_sections": ["8-138"], "messages": []})
    assert ["8-138"] in read
    assert any("8-138" in content for content in seen[0])


async def test_a_named_section_that_does_not_exist_does_not_fail_the_turn(monkeypatch):
    from pension_chat.agent import loop as loop_mod

    async def fake_call(**kwargs):
        from pension_chat.agent.llm import ToolTurn
        return ToolTurn(calls=(), content="Here.", raw_message={"role": "assistant", "content": "Here."})

    async def fake_run(name, arguments, state):
        return "searched text", {}

    async def fake_read(targets, working):
        return [None for _ in targets]

    monkeypatch.setattr(loop_mod, "call_with_tools", fake_call)
    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    monkeypatch.setattr(loop_mod, "_read_sections", fake_read)
    out = await loop_mod.investigate({"question": "What does Section 8-999 say?",
                                      "explicit_sections": ["8-999"], "messages": []})
    assert out is not None
```

- [ ] **Step 2: Run them and confirm the first fails**

Run: `uv run pytest tests/test_prefetch.py -q -k named_section`
Expected: the first FAILS (no read of `8-138` before the first call); the second passes or fails only on missing keys.

- [ ] **Step 3: Implement**

In `investigate`'s prefetch block, replace the single `run_tool("search_statute", ...)` await with a concurrent search and named-section read:

```python
        named = [s for s in (state.get("explicit_sections") or []) if s]
        (result, update), named_reads = await asyncio.gather(
            run_tool("search_statute", prefetch_args, working),
            _read_sections(named, working) if named else asyncio.sleep(0, result=[]),
        )
```

Keep the existing handling of `result` and `update`. After the search's message is appended, add the named sections:

```python
        read_named: list[str] = []
        for target, fetched in zip(named, named_reads or []):
            if fetched is None:
                continue   # a section that doesn't exist must not end the turn
            text, named_update = fetched
            if named_update.get("retrieved_sections"):
                sections = _merge_sections(sections, named_update["retrieved_sections"])
                working["retrieved_sections"] = sections
            read_named.append(text)
        if read_named:
            prefetched = {str(s["section"]) for s in sections if s.get("section")}
            messages.append({"role": "system", "content": (
                "The sections the question names have been read for you:"
                + chr(10) + chr(10) + (chr(10) * 2).join(read_named))})
```

Add `import asyncio` at the top of `loop.py` if absent. `prefetch_args` is already the dict built above; pass it instead of the duplicated literal.

- [ ] **Step 4: Run the tests and the full suite**

Run: `uv run pytest tests/test_prefetch.py tests/test_prefetch_closure.py tests/test_loop.py -q && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Gate (live) and commit**

Run: `uv run python scripts/run_evals.py --family L1 --concurrency 1 --repeat 3`
Expected: L1-001, L1-003, L1-004 and L1-005 show no `get_section` or second search in their traces; "statute" estimated p50 drops; no new failures.

```bash
git add src/pension_chat/agent/loop.py tests/test_prefetch.py
git commit -m "Retrieval: the sections a question names are read alongside the first search, before the model is asked

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Each section's text once per prompt (only if the baseline shows repetition)

**Precondition:** the Task 6 baseline report's per-turn `repeated_sections` (in `evals/runs/<baseline>/traces/*.json`) is non-empty for any L1 turn. Check with:

```bash
uv run python -c "import json,glob; print(sum(bool(json.load(open(f)).get('repeated_sections')) for f in glob.glob('evals/runs/'+sorted(__import__('os').listdir('evals/runs'))[-1]+'/traces/*.json')))"
```

If it prints `0`, mark this task done with the note "no repetition measured" and skip to Task 11.

**Files:**
- Modify: `src/pension_chat/agent/loop.py` (where a fetch's text is appended: the closure fetch, the completion fetch, and named-section reads)
- Test: `tests/test_loop.py` (extend)

**Interfaces:**
- Produces: `loop._once(text: str, shown: set[str]) -> str`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_section_already_shown_is_referred_to_not_repeated():
    from pension_chat.agent.loop import _once
    shown: set[str] = set()
    first = _once("### 40 ILCS 5/8-138 — Minimum annuities\nlong text", shown)
    again = _once("### 40 ILCS 5/8-138 — Minimum annuities\nlong text\n### 40 ILCS 5/1-160 — Tier 2\nmore", shown)
    assert "long text" in first
    assert again.count("long text") == 0 and "Section 8-138 is above" in again and "more" in again
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/test_loop.py -q -k already_shown`
Expected: FAIL (`ImportError: cannot import name '_once'`).

- [ ] **Step 3: Implement**

```python
_SECTION_HEAD = re.compile(r"^### 40 ILCS 5/(\S+)[^\n]*\n", re.MULTILINE)


def _once(text: str, shown: set[str]) -> str:
    """Text to add to the prompt, with any section already shown replaced by a pointer.

    No evidence is removed: the section's text is already above (spec §5.5, P4).
    """
    heads = list(_SECTION_HEAD.finditer(text))
    if not heads:
        return text
    parts = [text[: heads[0].start()]]
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        section = head.group(1)
        if section in shown:
            parts.append(f"(Section {section} is above.)\n")
        else:
            shown.add(section)
            parts.append(text[head.start(): end])
    return "".join(parts)
```

In `investigate`, create `shown: set[str] = set()` beside `prefetched`. Wrap every text that goes into `messages` from a search or a section read (the prefetch `result`, the closure `closed` texts, the named reads, and the completion fetch's `result`) with `_once(text, shown)`. Tool results the model's own calls return go through the same wrapper where the loop appends `role: "tool"` messages: find that append (search for `"role": "tool"` in `investigate`) and wrap its content.

- [ ] **Step 4: Run the tests and the full suite**

Run: `uv run pytest tests/test_loop.py tests/test_rendered_evidence.py tests/test_stream_grounding.py -q && uv run pytest -q`
Expected: all pass. Verification must still find each cited section rendered once.

- [ ] **Step 5: Gate (live) and commit**

Run: `uv run python scripts/run_evals.py --family L1 --concurrency 1 --repeat 3`
Expected: `repeated_sections` empty on every turn; prompt tokens per call lower; citation and verification checks unchanged.

```bash
git add src/pension_chat/agent/loop.py tests/test_loop.py
git commit -m "Prompts: each section's text appears once; a repeat points to it instead

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Embedding inside the search service (needs the Search service allowed to call the embedding deployment)

**Precondition:** the person approving has confirmed the Search service may call Azure OpenAI with the key in `.env` (open decision 2). If not confirmed, skip this task and note it.

**Files:**
- Modify: `src/pension_chat/ingestion/indexer.py` (`create_search_index`'s `VectorSearch`)
- Modify: `src/pension_chat/agent/retrieval.py` (`retrieve`, `_search_one`)
- Modify: `src/pension_chat/config.py` (`retrieval_integrated_vectorizer`)
- Create: `scripts/enable_vectorizer.py`
- Test: `tests/test_retrieval_variants.py` (extend)

**Interfaces:**
- Produces: `settings.retrieval_integrated_vectorizer: bool = False`; `_search_one(query, vector, filter_expr, top)` accepts `vector=None` with the flag on, and uses `VectorizableTextQuery`.

- [ ] **Step 1: Write the failing test**

```python
async def test_with_the_vectorizer_the_query_is_sent_as_text(monkeypatch):
    from pension_chat.agent import retrieval
    sent = {}

    class FakeClient:
        async def search(self, **kwargs):
            sent.update(kwargs)

            async def none():
                return
                yield
            return none()

    monkeypatch.setattr(retrieval, "get_async_search_client", lambda: FakeClient())
    monkeypatch.setattr(retrieval.get_settings(), "retrieval_integrated_vectorizer", True)
    await retrieval._search_one("tier 2 age", None, None, 10)
    (query,) = sent["vector_queries"]
    assert type(query).__name__ == "VectorizableTextQuery" and query.text == "tier 2 age"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/test_retrieval_variants.py -q -k vectorizer`
Expected: FAIL (no `vector_queries`, or the setting is missing).

- [ ] **Step 3: Implement**

`config.py`:

```python
    # Let Azure AI Search embed the query itself (its integrated vectorizer):
    # one round trip instead of two. Needs scripts/enable_vectorizer.py run once.
    retrieval_integrated_vectorizer: bool = False
```

`indexer.py`, in `create_search_index`: import `AzureOpenAIVectorizer` and `AzureOpenAIVectorizerParameters` from `azure.search.documents.indexes.models`, and build `VectorSearch` with:

```python
        vectorizers=[
            AzureOpenAIVectorizer(
                vectorizer_name="pension-openai",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=settings.azure_openai_endpoint,
                    deployment_name=settings.azure_openai_embedding_deployment,
                    model_name="text-embedding-3-large",
                    api_key=settings.azure_openai_api_key,
                ),
            ),
        ],
```

and `vectorizer_name="pension-openai"` on the `VectorSearchProfile`.

`scripts/enable_vectorizer.py`:

```python
"""Add the integrated vectorizer to the live index without touching its documents.

    uv run python scripts/enable_vectorizer.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dotenv import load_dotenv

load_dotenv()
from pension_chat.ingestion.indexer import create_search_index  # noqa: E402

create_search_index()   # create_or_update: adds the vectorizer; stored vectors are unchanged
print("vectorizer enabled")
```

Before writing it, read `create_search_index`. If it calls `create_index` rather than `create_or_update_index`, switch it to `create_or_update_index`.

`retrieval.py`:
- In `retrieve`, skip `_embed` when the flag is on: `vectors = [] if settings.retrieval_integrated_vectorizer else await _embed(queries)`.
- In `_search_one`, build the vector query as:

```python
    if get_settings().retrieval_integrated_vectorizer:
        kwargs["vector_queries"] = [VectorizableTextQuery(
            text=query, k_nearest_neighbors=settings.retrieval_vector_k, fields="content_vector")]
    elif vector:
        kwargs["vector_queries"] = [VectorizedQuery(...unchanged...)]
```

with `VectorizableTextQuery` imported from `azure.search.documents.models`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_retrieval_variants.py -q && uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Enable, compare, gate**

1. Run `uv run python scripts/enable_vectorizer.py`.
2. Compare top sections for the 14 L1 first questions with the flag off, then on. Expect the same set, rank within ±2:

   ```bash
   uv run python -c "import asyncio,yaml,glob; from pension_chat.agent.retrieval import retrieve; qs=[yaml.safe_load(open(f))['turns'][0]['q'] for f in sorted(glob.glob('evals/cases/L1/*.yaml'))]; print([[s.section for s in asyncio.run(retrieve([q]))] for q in qs])"
   ```

   Run it with `RETRIEVAL_INTEGRATED_VECTORIZER=false`, then with `=true`, and compare the two lists.
3. Set `RETRIEVAL_INTEGRATED_VECTORIZER=true` in `.env`, then run `uv run python scripts/run_evals.py --family L1 --concurrency 1 --repeat 3`.

   Expected: retrieval time in the breakdown drops; no new failures.

- [ ] **Step 6: Commit**

```bash
git add src/pension_chat/ingestion/indexer.py src/pension_chat/agent/retrieval.py src/pension_chat/config.py scripts/enable_vectorizer.py tests/test_retrieval_variants.py
git commit -m "Retrieval: the search service embeds the query itself (integrated vectorizer), one round trip instead of two

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Calibrate "in-region" against Azure Monitor (only with metric access)

**Precondition:** read access to the Azure OpenAI resource's metrics (open decision 3). Without it, skip this task; estimates stay labelled "estimated".

**Files:**
- Create: `scripts/model_ttft_metrics.py`
- Modify: `pyproject.toml` (dev dependency `azure-monitor-query`)

- [ ] **Step 1: Add the dependency**

Run: `uv add --dev azure-monitor-query azure-identity`

- [ ] **Step 2: Write the script**

```python
"""Compare the estimator's per-call model time with Azure's server-side first-token time.

    uv run python scripts/model_ttft_metrics.py --run evals/runs/<ts> --resource-id <OpenAI resource id>

Reads AzureOpenAITimeToResponse (average, 1-minute grain) over the run's window
and prints it beside the median model-call time from the run's traces minus
the probe's warm round trip. A gap over 0.2 s means the estimator's per-call
overhead should be corrected in evals/timing.py.
"""
import argparse
import glob
import json
import statistics
from datetime import datetime, timedelta, timezone

from azure.identity import DefaultAzureCredential
from azure.monitor.query import MetricsQueryClient

p = argparse.ArgumentParser()
p.add_argument("--run", required=True)
p.add_argument("--resource-id", required=True)
args = p.parse_args()

report = json.load(open(f"{args.run}/report.json"))
warm = report["probe"]["openai"]["warm"]
calls = [c["end"] - c["start"] for f in glob.glob(f"{args.run}/traces/*.json")
         for c in json.load(open(f))["calls"] if c["kind"] == "model"]
estimated = statistics.median(calls) - warm

stamp = datetime.strptime(args.run.rstrip("/\\")[-15:], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
client = MetricsQueryClient(DefaultAzureCredential())
result = client.query_resource(args.resource_id, ["AzureOpenAITimeToResponse"],
                               timespan=(stamp - timedelta(minutes=5), stamp + timedelta(hours=2)),
                               granularity=timedelta(minutes=1), aggregations=["Average"])
points = [d.average for m in result.metrics for ts in m.timeseries for d in ts.data if d.average]
server = statistics.median(points) / 1000 if points else None
print(f"estimator per-call model time: {estimated:.2f}s; Azure server-side first token: {server}")
if server is not None and abs(estimated - server) > 0.2:
    print("gap > 0.2 s: adjust the model overhead in evals/timing.py::_overhead")
```

The run folder's timestamp is local time. If the window returns no points, widen `timespan` by the machine's UTC offset.

- [ ] **Step 3: Run it against the latest L1 run and commit**

Run: `uv run python scripts/model_ttft_metrics.py --run evals/runs/<latest> --resource-id <id>`
Expected: two numbers printed.

```bash
git add scripts/model_ttft_metrics.py pyproject.toml uv.lock
git commit -m "Evals: compare the in-region estimate with Azure's server-side first-token metric

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## After the plan

Run the gate once more on everything:

```bash
uv run pytest -q
uv run python scripts/run_evals.py --family L1 --concurrency 1 --repeat 3
uv run python scripts/run_evals.py --family S4 --family S4b --family S11 --family S13 --concurrency 1
```

Report the estimated p50/p95 per turn type against the baseline. Stage 2 (single-call answering) gets its own spec next.
