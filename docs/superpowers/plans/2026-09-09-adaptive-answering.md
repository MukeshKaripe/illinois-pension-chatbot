# Adaptive Answering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pension assistant's regex-driven control layer with a working sheet, model-driven turn understanding, a policy-bounded assumption layer and a template-driven presentation layer, so a member gets the most useful answer the evidence supports instead of a refusal.

**Architecture:** A durable `WorkSheet` consolidates the conversation's facts (each with a provenance `source`), the figures computed from them (each naming the facts it `rests_on`), and what is still open. One model call per turn (`understand_turn`) replaces every regex that decides what a message is. Missing inputs may be filled from a declared assumption policy, disclosed in the answer and enforced by a guardrail. A degradation ladder picks the best answer the sheet supports, and that rung selects a response template whose computed blocks are rendered by Python rather than transcribed by the model.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, SQLAlchemy 2.0, Azure OpenAI (`gpt-5.6-luna-1`), Azure AI Search, pytest (`asyncio_mode = "auto"`), mypy strict, ruff. Frontend: Next.js 16, React 19, `react-markdown` 10 + `remark-gfm` 4, Tailwind v4.

**Spec:** `docs/superpowers/specs/2026-09-09-adaptive-answering-design.md`

## Global Constraints

- Python is managed with `uv`. Every command in this plan is run as `uv run <cmd>` from the repo root, `C:\dev\pension_chat`.
- `mypy` runs in **strict** mode (`pyproject.toml`, `[tool.mypy] strict = true`). Every new function needs full type annotations, including `-> None`.
- Lint and type-check must pass before every commit: `uv run ruff check .` and `uv run mypy .`.
- **No model call may grade another model's output.** Every guardrail and every check added by this plan is deterministic Python.
- **The model never does arithmetic.** Absolute rule 1 of `ANSWER_SYSTEM` stays. This plan removes the model's role in *formatting* computed values, never in producing them.
- The chat deployment is a reasoning model: it rejects `temperature` and `max_tokens`. Use `max_completion_tokens` and `reasoning_effort` in `{none, low, medium, high}`.
- Model deployment stays `gpt-5.6-luna-1`. Do not introduce a second deployment; measurement showed `gpt-5-mini-1` is both slower (3.99s vs 2.27s median) and less accurate on this task.
- `reasoning_effort_answer` stays `"none"`. Measurement showed `low` and `medium` cost latency without improving quality.
- New unit tests must run with **no network and no database**. Tests that need either belong in `evals/`.
- Frontend: no new dependencies. `npm run build` must type-check. `npm run lint` currently reports 7 pre-existing `any` problems; do not exceed 7.
- Every task ends with a commit. Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy
  ```

## File Structure

**New modules**

| file | responsibility |
|---|---|
| `src/pension_chat/agent/worksheet.py` | The conversation's durable facts, figures and open items, with provenance and dependency tracking. Pure; no I/O. |
| `src/pension_chat/calculation/assumption_policy.py` | Which inputs may be assumed, their defaults, their plausible ranges, and the words that count as disclosing them. Pure data plus lookups. |
| `src/pension_chat/agent/understanding.py` | The `understand_turn` prompt, its parsed result type, its fail-open fallback, and the node itself. |
| `src/pension_chat/agent/presentation.py` | Degradation-rung selection, the response-template registry, and status labels. Pure. |
| `src/pension_chat/agent/blocks.py` | Renderers for the computed answer blocks (tables, assumptions, legal basis) and the streaming marker substituter. Pure. |

**Modified**

| file | change |
|---|---|
| `src/pension_chat/agent/state.py` | `rests_on` on stored figures; no new durable key. |
| `src/pension_chat/agent/nodes.py` | `triage` reduced; `plan_request` deleted; `resolve_inputs` filters asks and applies assumptions; `prepare_answer` uses templates. |
| `src/pension_chat/agent/graph.py` | `understand_turn` replaces `plan_request`; recompute routing; repair branch; marker substitution during streaming. |
| `src/pension_chat/agent/guardrails.py` | `classify_scope` accept-path removed; `ASSUMPTION_NOT_DISCLOSED`; `OUT_OF_CODE_ANSWERED`; sheet-built `fallback_answer`. |
| `src/pension_chat/config.py` | Five new settings. |
| `scripts/eval_agent.py` | Expose and check `in_scope` and `recompute`. |
| `frontend/src/components/chat/ChatMessage.tsx` | Table overflow, chips from verified citations, heading scale. |
| `frontend/src/components/chat/ChatInterface.tsx` | Stream the incomplete tail as plain text. |

**New tests:** `tests/test_worksheet.py`, `tests/test_assumptions.py`, `tests/test_understanding.py`, `tests/test_degradation.py`, `tests/test_templates.py`, `tests/test_presentation.py`. **New evals:** `evals/routing.yaml`, additions to `evals/gold_end_to_end.yaml`.

---

### Task 1: The working sheet

**Files:**
- Create: `src/pension_chat/agent/worksheet.py`
- Test: `tests/test_worksheet.py`

**Interfaces:**
- Consumes: nothing. This is the foundation task.
- Produces: `WorkSheet`, `Fact`, `Figure`, `OpenItem`; constants `SOURCE_RECORD`, `SOURCE_STATED`, `SOURCE_HYPOTHETICAL`, `SOURCE_ASSUMED`, `SOURCE_WITHHELD`, `USABLE_SOURCES`. Methods used by later tasks: `WorkSheet.from_state(state) -> WorkSheet`, `.to_state_update() -> dict[str, Any]`, `.record_fact(name, value, source, basis="", turn=0) -> None`, `.fact(name) -> Fact | None`, `.knows(name) -> bool`, `.usable_values() -> dict[str, Any]`, `.assumed_facts() -> dict[str, Fact]`, `.record_figure(label, result, provision, rests_on, turn) -> None`, `.apply_revisions(revised, turn) -> set[str]`, `.stale_figures() -> list[str]`, `.filter_asks(asks) -> list[dict[str, Any]]`.

**Background for the implementer.** The agent's state is a `TypedDict` blackboard (`src/pension_chat/agent/state.py`) that LangGraph carries between nodes. A subset of keys listed in `DURABLE_KEYS` survives from one conversational turn to the next; everything else is cleared. Two durable keys matter here: `assumptions`, which is already `name -> {value, source, turn}`, and `baseline`, which holds the last figures worked out together with the inputs they used. This task adds no new durable key — the sheet is a *view* over the keys that already exist, which is why `from_state`/`to_state_update` are the only accessors.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_worksheet.py`:

```python
"""Tests for the conversation working sheet.

The sheet is a view over durable state keys that already exist, so these
tests pin two things: that a round trip through state loses nothing, and
that provenance actually governs what a figure is allowed to rest on.
"""

from __future__ import annotations

from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_RECORD,
    SOURCE_STATED,
    SOURCE_WITHHELD,
    WorkSheet,
)


def _state() -> dict:
    return {
        "turn_index": 2,
        "member_facts": {"age": 56, "service_years": 21.5, "final_average_salary": 69956.4},
        "assumptions": {},
        "baseline": None,
        "scenario_set": {},
        "open_question": None,
    }


def test_member_facts_arrive_as_record_provenance():
    sheet = WorkSheet.from_state(_state())
    assert sheet.fact("age").source == SOURCE_RECORD
    assert sheet.fact("age").value == 56


def test_a_stated_value_overrides_the_record_and_keeps_its_source():
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 23.0, SOURCE_STATED, turn=2)
    assert sheet.fact("service_years").value == 23.0
    assert sheet.fact("service_years").source == SOURCE_STATED


def test_withheld_facts_are_known_but_not_usable():
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 21.5, SOURCE_WITHHELD, basis="premise is Article 12")
    assert sheet.knows("service_years") is True
    assert "service_years" not in sheet.usable_values()


def test_assumed_facts_are_usable_and_reportable():
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("withdrawal_date", "2026-09-09", SOURCE_ASSUMED, basis="today")
    assert sheet.usable_values()["withdrawal_date"] == "2026-09-09"
    assert "withdrawal_date" in sheet.assumed_facts()


def test_revising_an_input_makes_the_figures_that_used_it_stale():
    sheet = WorkSheet.from_state(_state())
    sheet.record_figure(
        "now",
        {"annual_amount": "32126.78"},
        {"section": "11-134", "subsection": "(a)"},
        rests_on=("service_years", "final_average_salary"),
        turn=1,
    )
    stale = sheet.apply_revisions({"service_years": 23.0}, turn=2)
    assert stale == {"now"}
    assert sheet.stale_figures() == ["now"]
    assert sheet.fact("service_years").source == SOURCE_STATED


def test_revising_an_unrelated_input_leaves_figures_alone():
    sheet = WorkSheet.from_state(_state())
    sheet.record_figure(
        "now", {"annual_amount": "1"}, {"section": "11-134"},
        rests_on=("service_years",), turn=1,
    )
    assert sheet.apply_revisions({"spouse_age": 53}, turn=2) == set()
    assert sheet.stale_figures() == []


def test_asks_for_things_already_known_are_dropped():
    sheet = WorkSheet.from_state(_state())
    kept = sheet.filter_asks([
        {"name": "age", "ask_for": "your date of birth"},
        {"name": "withdrawal_date", "ask_for": "the date you plan to stop working"},
    ])
    assert [item["name"] for item in kept] == ["withdrawal_date"]


def test_a_withheld_ask_is_kept_but_marked_so_it_can_be_phrased_honestly():
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 21.5, SOURCE_WITHHELD, basis="premise is Article 12")
    kept = sheet.filter_asks([{"name": "service_years", "ask_for": "your years of service"}])
    assert len(kept) == 1
    assert kept[0]["withheld"] is True
    assert kept[0]["withheld_basis"] == "premise is Article 12"


def test_a_round_trip_through_state_preserves_facts_figures_and_dependencies():
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("withdrawal_date", "2026-09-09", SOURCE_ASSUMED, basis="today", turn=2)
    sheet.record_figure(
        "now", {"annual_amount": "32126.78"}, {"section": "11-134"},
        rests_on=("service_years",), turn=2,
    )
    state = {**_state(), **sheet.to_state_update()}
    restored = WorkSheet.from_state(state)
    assert restored.fact("withdrawal_date").source == SOURCE_ASSUMED
    assert restored.fact("withdrawal_date").basis == "today"
    assert restored.figures["now"].rests_on == ("service_years",)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_worksheet.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.agent.worksheet'`

- [ ] **Step 3: Write the module**

Create `src/pension_chat/agent/worksheet.py`:

```python
"""The conversation's working sheet: what is known, what was computed from it,
and what is still open.

This is a view over durable state keys that already exist -- `assumptions`,
`baseline`, `scenario_set`, `open_question` -- not a thirteenth key alongside
them. Two keys would drift; one accessor cannot.

The load-bearing field is `Fact.source`. A figure may rest on a value from the
member's record, from something they said, or from a premise they asked us to
suppose, and it may rest on a value we filled in ourselves so long as the answer
says so. It may never rest on a value that is on file but not usable under the
premise being explored -- that is `withheld`, and it exists because starving a
record fact and reporting it as merely *missing* makes the assistant ask a
member for an age it already knows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SOURCE_RECORD = "record"
SOURCE_STATED = "stated"
SOURCE_HYPOTHETICAL = "hypothetical"
SOURCE_ASSUMED = "assumed"
SOURCE_WITHHELD = "withheld"

#: Sources a computed figure is allowed to rest on. `withheld` is deliberately
#: absent: the value is known, and using it anyway is the bug this exists to stop.
USABLE_SOURCES = frozenset(
    {SOURCE_RECORD, SOURCE_STATED, SOURCE_HYPOTHETICAL, SOURCE_ASSUMED}
)


@dataclass
class Fact:
    """One value the conversation holds, and where it came from."""

    name: str
    value: Any
    source: str
    basis: str = ""
    turn: int = 0
    asked: bool = False


@dataclass
class Figure:
    """One computed result, and the facts it was computed from."""

    label: str
    result: dict[str, Any]
    provision: dict[str, Any]
    rests_on: tuple[str, ...]
    turn: int
    stale: bool = False


@dataclass
class OpenItem:
    """Something the conversation still needs settled."""

    what: str
    why: str
    asked_turn: int
    bound_to: str | None = None


@dataclass
class WorkSheet:
    """Facts, figures and open items for one conversation."""

    facts: dict[str, Fact] = field(default_factory=dict)
    figures: dict[str, Figure] = field(default_factory=dict)
    open_items: list[OpenItem] = field(default_factory=list)
    turn: int = 0

    # ── Construction ─────────────────────────────────────────────

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> WorkSheet:
        """Build the sheet from the durable keys the checkpointer carries.

        `member_facts` is the record and arrives as `record`. `assumptions`
        overlays it, because a value the member stated or a premise they
        supposed outranks the file.
        """
        sheet = cls(turn=int(state.get("turn_index") or 0))

        for name, value in (state.get("member_facts") or {}).items():
            if value is not None:
                sheet.facts[name] = Fact(name, value, SOURCE_RECORD, turn=sheet.turn)

        for name, entry in (state.get("assumptions") or {}).items():
            if not isinstance(entry, Mapping):
                continue
            sheet.facts[name] = Fact(
                name=name,
                value=entry.get("value"),
                source=str(entry.get("source") or SOURCE_STATED),
                basis=str(entry.get("basis") or ""),
                turn=int(entry.get("turn") or 0),
                asked=bool(entry.get("asked")),
            )

        baseline = state.get("baseline")
        if isinstance(baseline, Mapping) and baseline.get("result") is not None:
            sheet.figures["now"] = _figure_from(baseline, "now")
        for label, entry in (state.get("scenario_set") or {}).items():
            if isinstance(entry, Mapping) and entry.get("result") is not None:
                sheet.figures[label] = _figure_from(entry, label)

        open_question = state.get("open_question")
        if isinstance(open_question, Mapping) and open_question.get("what"):
            sheet.open_items.append(
                OpenItem(
                    what=str(open_question.get("what")),
                    why=str(open_question.get("why") or ""),
                    asked_turn=int(open_question.get("asked_turn") or 0),
                    bound_to=open_question.get("bound_to"),
                )
            )
        return sheet

    def to_state_update(self) -> dict[str, Any]:
        """The state update that persists this sheet.

        Only non-record facts are written back: the record is re-read from the
        database every turn, and storing a copy would let a stale one win.
        """
        assumptions = {
            name: {
                "value": fact.value,
                "source": fact.source,
                "basis": fact.basis,
                "turn": fact.turn,
                "asked": fact.asked,
            }
            for name, fact in self.facts.items()
            if fact.source != SOURCE_RECORD
        }
        update: dict[str, Any] = {"assumptions": assumptions}

        if "now" in self.figures:
            update["baseline"] = _figure_to_dict(self.figures["now"])
        scenarios = {
            label: _figure_to_dict(figure)
            for label, figure in self.figures.items()
            if label != "now"
        }
        if scenarios:
            update["scenario_set"] = scenarios
        if self.open_items:
            item = self.open_items[0]
            update["open_question"] = {
                "what": item.what,
                "why": item.why,
                "asked_turn": item.asked_turn,
                "bound_to": item.bound_to,
            }
        return update

    # ── Facts ────────────────────────────────────────────────────

    def record_fact(
        self,
        name: str,
        value: Any,
        source: str,
        basis: str = "",
        turn: int = 0,
    ) -> None:
        """Record a value under an explicit provenance."""
        self.facts[name] = Fact(name, value, source, basis=basis, turn=turn or self.turn)

    def fact(self, name: str) -> Fact | None:
        return self.facts.get(name)

    def knows(self, name: str) -> bool:
        """Whether the conversation holds this value at all, usable or not."""
        return name in self.facts and self.facts[name].value is not None

    def usable_values(self) -> dict[str, Any]:
        """Values a figure may be computed from."""
        return {
            name: fact.value
            for name, fact in self.facts.items()
            if fact.source in USABLE_SOURCES and fact.value is not None
        }

    def assumed_facts(self) -> dict[str, Fact]:
        return {
            name: fact
            for name, fact in self.facts.items()
            if fact.source == SOURCE_ASSUMED
        }

    # ── Figures and dependencies ─────────────────────────────────

    def record_figure(
        self,
        label: str,
        result: dict[str, Any],
        provision: dict[str, Any],
        rests_on: tuple[str, ...],
        turn: int,
    ) -> None:
        self.figures[label] = Figure(label, result, provision, tuple(rests_on), turn)

    def apply_revisions(self, revised: Mapping[str, Any], turn: int) -> set[str]:
        """Apply values the member corrected, and return the figures they invalidate.

        This is what makes "actually it's more like 23 years" recompute. The
        member does not have to use a word the system recognises; they have to
        contradict something a figure was built on.
        """
        changed: set[str] = set()
        for name, value in revised.items():
            if value is None:
                continue
            existing = self.facts.get(name)
            if existing is not None and str(existing.value) == str(value):
                continue
            self.record_fact(name, value, SOURCE_STATED, turn=turn)
            changed.add(name)

        stale: set[str] = set()
        for label, figure in self.figures.items():
            if changed.intersection(figure.rests_on):
                figure.stale = True
                stale.add(label)
        return stale

    def stale_figures(self) -> list[str]:
        return [label for label, figure in self.figures.items() if figure.stale]

    # ── Asking ───────────────────────────────────────────────────

    def filter_asks(self, asks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Drop asks for values already held; mark the ones held but unusable.

        Asking a member for their age when it is on their file reads as not
        having listened, and it happened on three of eight measured cases.
        """
        kept: list[dict[str, Any]] = []
        for ask in asks:
            name = str(ask.get("name") or "")
            fact = self.facts.get(name)
            if fact is not None and fact.value is not None:
                if fact.source in USABLE_SOURCES:
                    continue
                item = dict(ask)
                item["withheld"] = True
                item["withheld_basis"] = fact.basis
                kept.append(item)
                continue
            kept.append(dict(ask))
        return kept


def _figure_from(entry: Mapping[str, Any], label: str) -> Figure:
    return Figure(
        label=label,
        result=dict(entry.get("result") or {}),
        provision=dict(entry.get("provision") or {}),
        rests_on=tuple(entry.get("rests_on") or ()),
        turn=int(entry.get("turn") or 0),
        stale=bool(entry.get("stale")),
    )


def _figure_to_dict(figure: Figure) -> dict[str, Any]:
    return {
        "result": figure.result,
        "provision": figure.provision,
        "rests_on": list(figure.rests_on),
        "turn": figure.turn,
        "stale": figure.stale,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_worksheet.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check . && uv run mypy .`
Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add src/pension_chat/agent/worksheet.py tests/test_worksheet.py
git commit -m "Give the conversation one place to hold what it knows

Facts, the figures computed from them, and what is still open, behind one
accessor over the durable keys that already carried them separately. The
field that earns its place is Fact.source: a figure may rest on the record,
on what the member said, on a premise they supposed, and on a value we
filled in ourselves so long as the answer says so -- and never on one that
is on file but not usable under the premise being explored.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 2: Stop asking for what the sheet already holds

**Files:**
- Modify: `src/pension_chat/agent/nodes.py` (`resolve_inputs`, near line 1326)
- Test: `tests/test_worksheet.py` (add integration-shaped cases)

**Interfaces:**
- Consumes: `WorkSheet.from_state`, `.filter_asks` from Task 1.
- Produces: `missing_inputs` entries may now carry `withheld: bool` and `withheld_basis: str`. Task 12's `prepare_answer` reads both.

**Background.** `resolve_inputs` builds `missing_inputs` from every declared recipe input it could not resolve. It has no view of the conversation, so it re-asks for values the sheet holds and reports a deliberately-starved record fact as simply absent.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_worksheet.py`:

```python
def test_filter_asks_is_idempotent_and_preserves_order():
    sheet = WorkSheet.from_state(_state())
    asks = [
        {"name": "withdrawal_date", "ask_for": "the date you plan to stop working"},
        {"name": "spouse_age", "ask_for": "your spouse's age"},
    ]
    once = sheet.filter_asks(asks)
    twice = sheet.filter_asks(once)
    assert [a["name"] for a in once] == ["withdrawal_date", "spouse_age"]
    assert [a["name"] for a in twice] == ["withdrawal_date", "spouse_age"]


def test_an_ask_already_put_to_the_member_is_not_repeated():
    sheet = WorkSheet.from_state(_state())
    sheet.facts["spouse_age"] = Fact("spouse_age", None, SOURCE_STATED, asked=True, turn=1)
    kept = sheet.filter_asks([{"name": "spouse_age", "ask_for": "your spouse's age"}])
    assert kept[0]["already_asked"] is True
```

Add `Fact` to the import block at the top of the file.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_worksheet.py::test_an_ask_already_put_to_the_member_is_not_repeated -v`
Expected: FAIL — `KeyError: 'already_asked'`

- [ ] **Step 3: Extend `filter_asks`**

In `src/pension_chat/agent/worksheet.py`, replace the body of the `for ask in asks:` loop's final `kept.append(dict(ask))` path so an already-asked fact is flagged:

```python
        for ask in asks:
            name = str(ask.get("name") or "")
            fact = self.facts.get(name)
            if fact is not None and fact.value is not None:
                if fact.source in USABLE_SOURCES:
                    continue
                item = dict(ask)
                item["withheld"] = True
                item["withheld_basis"] = fact.basis
                kept.append(item)
                continue
            item = dict(ask)
            if fact is not None and fact.asked:
                item["already_asked"] = True
            kept.append(item)
        return kept
```

- [ ] **Step 4: Wire it into `resolve_inputs`**

In `src/pension_chat/agent/nodes.py`, add the import at the top of the file, beside the other `pension_chat.agent` imports:

```python
from pension_chat.agent.worksheet import WorkSheet
```

Then in `resolve_inputs`, replace the `return` statement at the end of the function with:

```python
    sheet = WorkSheet.from_state(state)
    filtered = sheet.filter_asks(missing)

    return {
        "resolved_inputs": resolved,
        # An input the sheet holds is not missing, and an input it holds but
        # cannot use under this premise is not missing either -- it is withheld,
        # and saying so is the difference between "tell me your age" and "your
        # file has your age, but I am not using it while we talk about Article 12".
        "missing_inputs": filtered,
        "input_overrides": overrides,
        "timings": {"resolve_inputs": time.perf_counter() - started},
    }
```

- [ ] **Step 5: Run the whole unit suite**

Run: `uv run pytest tests/ -v`
Expected: PASS. If `tests/test_calculation.py` or `tests/test_scenarios.py` assert on `missing_inputs` contents, they still pass — `filter_asks` only removes entries the sheet can satisfy, and those tests build states with no `member_facts`.

- [ ] **Step 6: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/worksheet.py src/pension_chat/agent/nodes.py tests/test_worksheet.py
git commit -m "Stop asking a member for what is already on their file

resolve_inputs built its list of missing inputs from the recipe's declared
inputs and nothing else, so it asked for an age that was on the record and
for a service figure it was deliberately not using. Three of eight measured
answers did this. The sheet knows the difference between not knowing
something, holding it, and holding it but being unable to use it under the
premise in play, and only the first is a question.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 3: Repair a failed answer instead of deleting it

**Files:**
- Modify: `src/pension_chat/agent/graph.py` (`run_agent_stream`, near line 340)
- Modify: `src/pension_chat/agent/guardrails.py` (`fallback_answer`, near line 661)
- Modify: `src/pension_chat/config.py`
- Test: `tests/test_guardrails.py`

**Interfaces:**
- Consumes: `WorkSheet.from_state` from Task 1.
- Produces: `guardrails.REPAIR_SYSTEM: str`; `guardrails.fallback_answer(report, sheet=None) -> str`; `settings.answer_repair_enabled: bool`.

**Background.** Today one untraceable figure deletes the whole answer and replaces it with a fixed template that asks for the member's age, service and salary history — all three of which are usually on file. Measured: 0 hard failures in 42 live generations, so this branch is rare; and 2 of 2 forced failures were fully recovered by a single repair call at ~4s, keeping the correct figure and table.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_guardrails.py`:

```python
def test_the_fallback_names_what_is_established_rather_than_asking_for_it():
    from pension_chat.agent.worksheet import WorkSheet

    report = verify_answer(
        "Your pension is $99,999.99 a year.",
        retrieved_sections=SECTIONS,
        calculation_result=CALC,
        facts=FACTS,
    )
    assert not report.passed

    sheet = WorkSheet.from_state(
        {"turn_index": 1, "member_facts": FACTS, "assumptions": {}}
    )
    message = fallback_answer(report, sheet=sheet)
    assert "21.5" in message
    assert "your years of service" not in message.lower()


def test_the_fallback_without_a_sheet_still_reads_as_a_reply():
    report = verify_answer(
        "Your pension is $99,999.99 a year.",
        retrieved_sections=SECTIONS,
        calculation_result=CALC,
        facts=FACTS,
    )
    message = fallback_answer(report)
    assert message.strip()
    assert "retirement fund" in message
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_guardrails.py -k fallback -v`
Expected: FAIL — `fallback_answer() got an unexpected keyword argument 'sheet'`

- [ ] **Step 3: Rebuild `fallback_answer` and add the repair prompt**

In `src/pension_chat/agent/guardrails.py`, replace the `fallback_answer` function with:

```python
REPAIR_SYSTEM = """Your previous answer did not pass verification.

Rewrite it so that every problem listed is gone, changing as little else as
possible. Keep the voice, the shape, and every figure that was verified. A figure
you cannot trace to the worked figures, the statute text in front of you, the
member's record or the member's own messages must go -- say what would be needed
instead, or state the rule without totalling it. Never do arithmetic to replace
it. A provision whose text you do not have must be named in words, not cited in
brackets.

Return the corrected answer only. No preamble, no apology, no mention of the
correction."""


def _established_line(sheet: Any) -> str:
    """One sentence naming what the conversation already holds."""
    if sheet is None:
        return ""
    parts: list[str] = []
    values = sheet.usable_values()
    if (age := values.get("age")) is not None:
        parts.append(f"you are {age}")
    if (service := values.get("service_years")) is not None:
        parts.append(f"you have {service} years of service")
    if (fas := values.get("final_average_salary")) is not None:
        parts.append(f"your highest four-year average salary is ${float(fas):,.2f}")
    if not parts:
        return ""
    return "I still have " + ", and ".join(parts) + "."


def fallback_answer(report: GuardrailReport, sheet: Any | None = None) -> str:
    """The message shown when verification fails and repair could not fix it.

    This replaces an answer the member has already begun reading, so it has to
    be honest that the answer was withdrawn and still be a reply a person would
    want to receive.

    It used to be a fixed template asking for the member's age, years of service
    and salary history regardless of what was known -- which, for a member whose
    record holds all three, reads as amnesia rather than as care. What it says
    now is built from the sheet.
    """
    kinds = {failure["code"] for failure in report.hard_failures}
    established = _established_line(sheet)

    if "UNVERIFIED_FIGURE" in kinds:
        detail = (
            "I had a figure in that answer I could not trace back to the Pension Code, "
            "and an estimate of your pension is not worth having unless every number in "
            "it comes from the statute and your own record."
        )
        next_step = (
            established or
            "If you tell me your age, your years of service and your salary history, "
            "I can work it through again from the Code itself."
        )
    elif "UNVERIFIED_CITATION" in kinds:
        detail = (
            "I found myself relying on a provision I do not have the text of, and I "
            "would rather not describe a rule I cannot read to you directly."
        )
        next_step = (
            "Naming the Section you are interested in usually helps — I currently have "
            "the text of Articles 1, 8, 9, 11, 12 and 20."
        )
    else:
        detail = (
            "Something in that answer did not hold up when I checked it back against "
            "the Pension Code."
        )
        next_step = established or "It may help to ask again about one Section."

    return (
        "I have taken that answer back — sorry.\n\n"
        f"{detail}\n\n"
        f"{next_step} For anything binding, your retirement fund is the one to ask."
    )
```

Add `from typing import Any` to the imports if it is not already present (it is — line 34).

- [ ] **Step 4: Add the config switch**

In `src/pension_chat/config.py`, after `clarification_budget`:

```python
    # A hard verification failure used to delete the whole answer. Measured at
    # 0 failures in 42 live generations, so this branch is rare -- and 2 of 2
    # forced failures were fully recovered by one repair call in ~4s, keeping
    # the correct figure and table and losing only the offending sentence.
    # It costs nothing on the path that passes.
    answer_repair_enabled: bool = True
```

- [ ] **Step 5: Add the repair branch to the stream**

In `src/pension_chat/agent/graph.py`, add the import beside the others:

```python
from pension_chat.agent.worksheet import WorkSheet
```

Then replace the `if not report.passed:` block in `run_agent_stream` with:

```python
    if not report.passed and settings.answer_repair_enabled:
        # One attempt, never a loop. The unverified text has already been
        # streamed, so whatever comes out of this replaces it.
        problems = "\n".join(
            f"- {failure['code']}: {failure['message']}"
            for failure in report.hard_failures
        )
        repair_messages = list(messages) + [
            {"role": "assistant", "content": answer},
            {
                "role": "system",
                "content": f"{guardrails.REPAIR_SYSTEM}\n\nProblems found:\n{problems}",
            },
        ]
        try:
            repaired = "".join(
                [
                    delta
                    async for delta in stream_llm(
                        node="repair_answer",
                        messages=repair_messages,
                        reasoning_effort=settings.reasoning_effort_answer,
                    )
                ]
            )
        except Exception:
            logger.exception("Answer repair failed")
            repaired = ""

        if repaired:
            retry = _verify(state, repaired, settings)
            if retry.passed:
                answer = repaired
                report = retry
                state["final_answer"] = answer
                state["guardrail_passed"] = True
                state["guardrail_report"] = report.to_dict()
                yield {"type": "replace", "text": answer, "reason": {"repaired": True}}

    if not report.passed:
        replacement = guardrails.fallback_answer(report, sheet=WorkSheet.from_state(state))
        state["final_answer"] = replacement
        logger.warning(
            "Guardrail rejected answer for session %s: %s",
            session_id,
            report.hard_failures,
        )
        yield {"type": "replace", "text": replacement, "reason": report.to_dict()}
```

Note that `state["guardrail_passed"]` and `state["guardrail_report"]` are assigned before this block; the repair path overwrites them only on success.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_guardrails.py -v`
Expected: PASS.

- [ ] **Step 7: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/graph.py src/pension_chat/agent/guardrails.py src/pension_chat/config.py tests/test_guardrails.py
git commit -m "Fix a failed answer rather than deleting it

One untraceable figure withdrew the entire answer and replaced it with a
template asking for the member's age, service and salary history -- all
three of which are usually on their file. Forced failures showed one repair
call recovers the answer 2 times out of 2 in about four seconds, keeping the
correct figure and the worked-steps table and losing only the sentence that
failed. It costs nothing on the path that passes, which measured 42 out of
42. Where repair cannot save it, the apology is at least built from what the
conversation actually holds.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 4: The assumption policy

**Files:**
- Create: `src/pension_chat/calculation/assumption_policy.py`
- Test: `tests/test_assumptions.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AssumptionRule` dataclass with fields `name, assumable, basis, disclosure_cues, default, plausible`; `POLICY: dict[str, AssumptionRule]`; `rule_for(name) -> AssumptionRule | None`; `is_assumable(name) -> bool`; `default_for(name, known) -> Any`; `plausible_range(name, known) -> tuple[Any, Any] | None`; `NEVER_ASSUMABLE: frozenset[str]`.

**Background.** The system may assume things about *timing and framing*, never about who the member is or what they earned. Assuming a salary or a service figure fabricates the member; assuming that "retire now" means today does not.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_assumptions.py`:

```python
"""Tests for the assumption policy.

The last test is a bug-class guard rather than a behaviour test, in the style
of the alternative-reachability guards in test_planning.py and test_coverage.py:
it walks the policy table itself and requires every assumable entry to be
complete, so a new entry cannot be added half-finished.
"""

from __future__ import annotations

import pytest

from pension_chat.calculation.assumption_policy import (
    NEVER_ASSUMABLE,
    POLICY,
    default_for,
    is_assumable,
    plausible_range,
    rule_for,
)

KNOWN = {"age": 56, "as_of_date": "2026-09-09", "service_years": 21.5}


def test_timing_inputs_are_assumable():
    assert is_assumable("withdrawal_date")
    assert is_assumable("months_before_age_60")


@pytest.mark.parametrize(
    "name",
    ["article_number", "employment_status", "tier", "final_average_salary", "service_years"],
)
def test_identity_and_entitlement_inputs_are_never_assumable(name):
    assert not is_assumable(name)
    assert name in NEVER_ASSUMABLE
    assert default_for(name, KNOWN) is None


def test_withdrawal_date_defaults_to_today_as_the_member_knows_it():
    assert default_for("withdrawal_date", KNOWN) == "2026-09-09"


def test_months_before_sixty_assumes_a_mid_year_birthday():
    # 56 years old, so four years to 60, less six months for the unknown
    # position of the birthday within the year.
    assert default_for("months_before_age_60", KNOWN) == 42


def test_months_before_sixty_carries_the_whole_year_of_uncertainty():
    assert plausible_range("months_before_age_60", KNOWN) == (37, 48)


def test_a_range_is_absent_when_the_facts_do_not_support_one():
    assert plausible_range("months_before_age_60", {}) is None


def test_every_assumable_rule_is_complete():
    """A half-finished policy entry is a silent wrong number, so require the lot."""
    for name, rule in POLICY.items():
        if not rule.assumable:
            assert rule.default is None, f"{name}: not assumable but carries a default"
            continue
        assert rule.basis, f"{name}: assumable with no basis to state"
        assert rule.disclosure_cues, f"{name}: assumable with no words that disclose it"
        assert rule.default is not None, f"{name}: assumable with no default"
        assert rule.plausible is not None, f"{name}: assumable with no plausible range"
        assert default_for(name, KNOWN) is not None, f"{name}: default unusable on known facts"
        assert plausible_range(name, KNOWN) is not None, f"{name}: range unusable on known facts"


def test_an_unknown_input_is_not_assumable_by_default():
    assert rule_for("favourite_colour") is None
    assert not is_assumable("favourite_colour")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_assumptions.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the module**

Create `src/pension_chat/calculation/assumption_policy.py`:

```python
"""Which calculation inputs may be filled in, and on what basis.

An input is either known or it is not, and until now "not" was terminal: a
member who could not supply one value got no figure at all. This table is the
narrow exception. It may fill in things about *timing and framing* -- when a
member means by "retire now", where in the year an unstated birthday falls --
and it may never fill in things about who the member is or what they earned.
Assuming a salary or a service figure does not estimate a member's pension; it
invents a member.

Every assumable rule also carries the words that count as disclosing it, so the
guardrail can check that the answer said so rather than trusting that it did.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

#: Inputs that must be asked for, never filled in. Listed explicitly so the
#: absence of a rule is not the only thing standing between a member and a
#: fabricated salary.
NEVER_ASSUMABLE = frozenset(
    {
        "article_number",
        "employment_status",
        "tier",
        "final_average_salary",
        "highest_average_annual_salary_4yr",
        "service_years",
        "years_of_service",
        "creditable_service_years",
        "total_service_years",
        "current_annual_salary",
        "salary_history",
    }
)


@dataclass(frozen=True)
class AssumptionRule:
    """What may be assumed about one input, and how to say so."""

    name: str
    assumable: bool
    basis: str
    disclosure_cues: tuple[str, ...] = ()
    default: Callable[[Mapping[str, Any]], Any] | None = None
    plausible: Callable[[Mapping[str, Any]], tuple[Any, Any] | None] | None = None


def _today(known: Mapping[str, Any]) -> Any:
    return known.get("as_of_date")


def _months_before_sixty(known: Mapping[str, Any]) -> Any:
    """Whole months from a mid-year birthday to age 60.

    Knowing someone is 56 does not say where in the year their birthday falls,
    so the honest default sits in the middle of the range the statement allows
    and the spread is reported alongside it. Picking a point silently is what
    turns "age 56" into a confident 44 that nobody can check.
    """
    age = known.get("age")
    if age is None:
        return None
    months = (60 - int(age)) * 12
    if months <= 0:
        return 0
    return months - 6


def _months_before_sixty_range(known: Mapping[str, Any]) -> tuple[Any, Any] | None:
    age = known.get("age")
    if age is None:
        return None
    months = (60 - int(age)) * 12
    if months <= 0:
        return None
    # Birthday on the last day of the coming year: one month short of the full
    # count. Birthday today: the full count.
    return (months - 11, months)


POLICY: dict[str, AssumptionRule] = {
    "withdrawal_date": AssumptionRule(
        name="withdrawal_date",
        assumable=True,
        basis="the date you stop working, taken as today",
        disclosure_cues=("today", "retire now", "as of today", "stop working now"),
        default=_today,
        plausible=lambda known: (_today(known), _today(known)) if _today(known) else None,
    ),
    "annuity_start_date": AssumptionRule(
        name="annuity_start_date",
        assumable=True,
        basis="the date your annuity starts, taken as the date you stop working",
        disclosure_cues=("today", "starts when you stop", "begins now"),
        default=_today,
        plausible=lambda known: (_today(known), _today(known)) if _today(known) else None,
    ),
    "months_before_age_60": AssumptionRule(
        name="months_before_age_60",
        assumable=True,
        basis="your birthday falling mid-year, because your date of birth is not on file",
        disclosure_cues=("birthday", "date of birth", "born"),
        default=_months_before_sixty,
        plausible=_months_before_sixty_range,
    ),
}

for _name in NEVER_ASSUMABLE:
    POLICY.setdefault(
        _name,
        AssumptionRule(
            name=_name,
            assumable=False,
            basis="",
        ),
    )
del _name


def rule_for(name: str) -> AssumptionRule | None:
    return POLICY.get(name)


def is_assumable(name: str) -> bool:
    rule = POLICY.get(name)
    return bool(rule and rule.assumable)


def default_for(name: str, known: Mapping[str, Any]) -> Any:
    """The value to fill in, or ``None`` when this input must be asked for."""
    rule = POLICY.get(name)
    if rule is None or not rule.assumable or rule.default is None:
        return None
    return rule.default(known)


def plausible_range(name: str, known: Mapping[str, Any]) -> tuple[Any, Any] | None:
    rule = POLICY.get(name)
    if rule is None or not rule.assumable or rule.plausible is None:
        return None
    return rule.plausible(known)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_assumptions.py -v`
Expected: PASS, 9 tests (5 of them parametrized cases of one).

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/calculation/assumption_policy.py tests/test_assumptions.py
git commit -m "Say which inputs may be filled in, and which must be asked for

The system may assume things about timing and framing -- what 'retire now'
means, where in the year an unstated birthday falls -- and never about who
the member is or what they earned, because assuming a salary does not
estimate a pension, it invents a member. Each assumable rule carries the
words that count as disclosing it, so a later guardrail can check the answer
said so rather than trusting that it did, and a table walk requires every
assumable entry to be complete before it can be added.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 5: Applying assumptions, with the spread

**Files:**
- Modify: `src/pension_chat/agent/nodes.py` (`resolve_inputs`)
- Modify: `src/pension_chat/config.py`
- Test: `tests/test_assumptions.py`

**Interfaces:**
- Consumes: `assumption_policy.default_for`, `.plausible_range`, `.is_assumable` (Task 4); `WorkSheet.record_fact`, `.usable_values` (Task 1).
- Produces: `nodes.apply_assumptions(sheet, missing, known, ceiling) -> tuple[dict[str, Any], list[dict[str, Any]]]` returning `(filled, still_missing)`; state key `assumptions` gains `source="assumed"` entries.

**Background.** At most two assumed facts may stand behind one figure. Beyond that the figure stops being an estimate of anything and the turn should ask instead.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_assumptions.py`:

```python
def test_assumptions_fill_only_what_policy_allows():
    from pension_chat.agent.nodes import apply_assumptions
    from pension_chat.agent.worksheet import SOURCE_ASSUMED, WorkSheet

    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": KNOWN, "assumptions": {}})
    missing = [
        {"name": "withdrawal_date", "ask_for": "the date you plan to stop working"},
        {"name": "final_average_salary", "ask_for": "your salary history"},
    ]
    filled, still_missing = apply_assumptions(sheet, missing, KNOWN, ceiling=2)

    assert filled["withdrawal_date"] == "2026-09-09"
    assert [item["name"] for item in still_missing] == ["final_average_salary"]
    assert sheet.fact("withdrawal_date").source == SOURCE_ASSUMED
    assert sheet.fact("withdrawal_date").basis


def test_the_ceiling_stops_a_figure_resting_on_too_much_guesswork():
    from pension_chat.agent.nodes import apply_assumptions
    from pension_chat.agent.worksheet import WorkSheet

    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": KNOWN, "assumptions": {}})
    missing = [
        {"name": "withdrawal_date", "ask_for": "..."},
        {"name": "annuity_start_date", "ask_for": "..."},
        {"name": "months_before_age_60", "ask_for": "..."},
    ]
    filled, still_missing = apply_assumptions(sheet, missing, KNOWN, ceiling=2)

    assert len(filled) == 2
    assert [item["name"] for item in still_missing] == ["months_before_age_60"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_assumptions.py -k assumptions_fill -v`
Expected: FAIL — `ImportError: cannot import name 'apply_assumptions'`

- [ ] **Step 3: Add the two config settings**

In `src/pension_chat/config.py`, after `answer_repair_enabled`:

```python
    # How many values one figure may rest on that nobody supplied. Past two, the
    # figure stops being an estimate of this member and the turn should ask.
    assumption_ceiling: int = 2
    # How far an assumption must move the figure before it belongs in the
    # opening sentence rather than only in the assumptions block.
    materiality_threshold: float = 0.02
```

- [ ] **Step 4: Write `apply_assumptions`**

In `src/pension_chat/agent/nodes.py`, add above `resolve_inputs`:

```python
def apply_assumptions(
    sheet: WorkSheet,
    missing: list[dict[str, Any]],
    known: dict[str, Any],
    ceiling: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fill what policy allows, record why, and return what still has to be asked.

    Order is the order `missing` arrives in, so the ceiling bites the least
    important inputs rather than an arbitrary set of them.
    """
    filled: dict[str, Any] = {}
    still_missing: list[dict[str, Any]] = []

    for item in missing:
        name = str(item.get("name") or "")
        if len(filled) >= ceiling or not is_assumable(name):
            still_missing.append(item)
            continue
        value = default_for(name, known)
        if value is None:
            still_missing.append(item)
            continue
        rule = rule_for(name)
        basis = rule.basis if rule else ""
        sheet.record_fact(name, value, SOURCE_ASSUMED, basis=basis)
        filled[name] = value

    return filled, still_missing
```

Add the imports at the top of `nodes.py`:

```python
from pension_chat.agent.worksheet import SOURCE_ASSUMED, WorkSheet
from pension_chat.calculation.assumption_policy import (
    default_for,
    is_assumable,
    rule_for,
)
```

(The `WorkSheet` import was added in Task 2; extend that line rather than duplicating it.)

- [ ] **Step 5: Call it from `resolve_inputs`**

In `resolve_inputs`, replace the block added in Task 2 with:

```python
    sheet = WorkSheet.from_state(state)
    filtered = sheet.filter_asks(missing)
    settings_ceiling = settings.assumption_ceiling
    assumed, still_missing = apply_assumptions(sheet, filtered, facts, settings_ceiling)
    resolved.update(assumed)

    return {
        "resolved_inputs": resolved,
        "missing_inputs": still_missing,
        "input_overrides": overrides,
        **sheet.to_state_update(),
        "timings": {"resolve_inputs": time.perf_counter() - started},
    }
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_assumptions.py tests/test_worksheet.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole unit suite, lint, type-check and commit**

```bash
uv run pytest tests/ -q
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/nodes.py src/pension_chat/config.py tests/test_assumptions.py
git commit -m "Fill the gaps policy allows, and record that they were filled

An input was known or it was missing, and missing ended the answer. Timing
inputs may now be filled from the policy table, recorded as assumed with the
basis that produced them, and carried into the figure -- capped at two per
figure, because past that the number stops being an estimate of this member.
Everything the policy refuses still becomes a question.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 6: The disclosure guardrail

**Files:**
- Modify: `src/pension_chat/agent/guardrails.py` (`verify_answer`)
- Modify: `src/pension_chat/agent/graph.py` (`_verify`)
- Test: `tests/test_guardrails.py`

**Interfaces:**
- Consumes: `assumption_policy.rule_for` (Task 4); `WorkSheet.assumed_facts` (Task 1).
- Produces: `verify_answer(..., assumed_facts: Mapping[str, str] = {})` where the value is the fact's `basis`; new hard-failure code `ASSUMPTION_NOT_DISCLOSED`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_guardrails.py`:

```python
ASSUMED = {"months_before_age_60": "your birthday falling mid-year"}


def test_a_figure_resting_on_an_assumption_must_disclose_it():
    report = verify_answer(
        "Your pension is $32,126.78 a year ($2,677.23 a month).",
        retrieved_sections=SECTIONS,
        calculation_result=CALC,
        facts=FACTS,
        assumed_facts=ASSUMED,
    )
    assert not report.passed
    assert {f["code"] for f in report.hard_failures} == {"ASSUMPTION_NOT_DISCLOSED"}


def test_naming_the_assumption_satisfies_the_check():
    report = verify_answer(
        "Your pension is $32,126.78 a year ($2,677.23 a month). I have assumed your "
        "birthday falls mid-year, because your date of birth is not on file.",
        retrieved_sections=SECTIONS,
        calculation_result=CALC,
        facts=FACTS,
        assumed_facts=ASSUMED,
    )
    assert report.passed


def test_an_answer_with_no_figure_needs_no_disclosure():
    report = verify_answer(
        "Section 11-134 sets the annual rate at 2.40% for each year of service.",
        retrieved_sections=SECTIONS,
        calculation_result=None,
        facts=FACTS,
        assumed_facts=ASSUMED,
        check_numbers=False,
    )
    assert report.passed
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_guardrails.py -k assumption -v`
Expected: FAIL — `verify_answer() got an unexpected keyword argument 'assumed_facts'`

- [ ] **Step 3: Add the check**

In `src/pension_chat/agent/guardrails.py`, add the import:

```python
from pension_chat.calculation.assumption_policy import rule_for
```

Add the parameter to `verify_answer`'s signature, after `coverage`:

```python
    assumed_facts: Mapping[str, str] | None = None,
```

Add `Mapping` to the `collections.abc` import line. Then, immediately before `verify_answer` returns its report, add:

```python
    _check_assumptions_disclosed(report, answer, calculation_result, assumed_facts or {})
```

And define, beside the other check helpers:

```python
def _check_assumptions_disclosed(
    report: GuardrailReport,
    answer: str,
    calculation_result: dict | None,
    assumed_facts: Mapping[str, str],
) -> None:
    """A figure resting on a value nobody supplied must say so.

    The policy table owns the words that count as disclosure, so this is a
    string check against a declared vocabulary rather than a judgement. An
    answer with no figure in it has nothing to disclose.
    """
    if not assumed_facts or not calculation_result:
        return

    lowered = answer.lower()
    for name, basis in assumed_facts.items():
        rule = rule_for(name)
        cues = rule.disclosure_cues if rule else ()
        if any(cue.lower() in lowered for cue in cues):
            continue
        report.fail(
            "ASSUMPTION_NOT_DISCLOSED",
            f"The answer states a figure that rests on an assumed value "
            f"({name}: {basis}) without telling the member it was assumed.",
        )
```

- [ ] **Step 4: Pass the assumed facts through `_verify`**

In `src/pension_chat/agent/graph.py`, inside `_verify`, add before the call to `guardrails.verify_answer`:

```python
    sheet = WorkSheet.from_state(state)
    assumed = {name: fact.basis for name, fact in sheet.assumed_facts().items()}
```

and add `assumed_facts=assumed,` to the `verify_answer(...)` call's keyword arguments.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_guardrails.py -v`
Expected: PASS.

- [ ] **Step 6: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/guardrails.py src/pension_chat/agent/graph.py tests/test_guardrails.py
git commit -m "Make disclosing an assumption a condition of stating the figure

An assumption the member is not told about is indistinguishable from a wrong
number, so the check runs in the same pass that already traces every figure
to its source: a figure resting on a value nobody supplied fails
verification unless the answer names it. The words that count as naming it
come from the policy table that produced the assumption, so this is a string
check against a declared vocabulary and not a judgement about tone.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 7: Turn understanding — the parsed result

**Files:**
- Create: `src/pension_chat/agent/understanding.py`
- Test: `tests/test_understanding.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TurnUnderstanding` dataclass with fields `in_scope: bool`, `out_of_scope_reason: str`, `intent: str`, `benefit_types: list[str]`, `needs_figure: bool`, `recompute: bool`, `revised_facts: dict[str, Any]`, `scenarios: list[dict[str, Any]]`, `unresolved: list[dict[str, Any]]`, `answerable_part: str`, `answers_open_question: bool`, `search_queries: list[str]`; `UNDERSTAND_SYSTEM: str`; `parse_understanding(raw: str) -> TurnUnderstanding`; `fallback_understanding() -> TurnUnderstanding`.

**Background.** This task is the pure half — the prompt, the type and the parser, all testable without a network. Task 8 wires the node into the graph.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_understanding.py`:

```python
"""Tests for parsing the turn-understanding result.

The node's job is to replace roughly 200 lines of regex that decided what a
message was. What is tested here is the part that has to survive a model
returning something unexpected: a missing key, a wrong type, or no JSON at
all must never take a member's question down with it.
"""

from __future__ import annotations

import json

from pension_chat.agent.understanding import (
    fallback_understanding,
    parse_understanding,
)


def test_a_complete_result_parses():
    raw = json.dumps({
        "in_scope": True,
        "intent": "recalculate with corrected service",
        "benefit_types": ["retirement_annuity"],
        "needs_figure": True,
        "recompute": True,
        "revised_facts": {"service_years": 23},
        "scenarios": [{"label": "at 60", "retirement_age": 60}],
        "unresolved": [{"what": "date of birth", "why": "sets the reduction"}],
        "answerable_part": "",
        "answers_open_question": False,
        "search_queries": ["minimum annuity 20 years of service"],
    })
    result = parse_understanding(raw)
    assert result.in_scope is True
    assert result.recompute is True
    assert result.revised_facts == {"service_years": 23}
    assert result.scenarios[0]["retirement_age"] == 60
    assert result.search_queries == ["minimum annuity 20 years of service"]


def test_missing_keys_take_safe_defaults():
    result = parse_understanding('{"in_scope": true}')
    assert result.in_scope is True
    assert result.benefit_types == ["retirement_annuity"]
    assert result.needs_figure is False
    assert result.revised_facts == {}
    assert result.scenarios == []


def test_wrong_types_do_not_crash_the_turn():
    result = parse_understanding(
        '{"in_scope": "yes", "benefit_types": "retirement_annuity", '
        '"revised_facts": ["service_years"], "scenarios": {"label": "x"}}'
    )
    assert result.in_scope is True
    assert result.benefit_types == ["retirement_annuity"]
    assert result.revised_facts == {}
    assert result.scenarios == []


def test_unparseable_output_falls_open_not_closed():
    """A member wrongly refused has no recourse; a member wrongly admitted
    still meets the verifier and the calculation gate."""
    result = parse_understanding("the model said something else entirely")
    assert result.in_scope is True
    assert result.needs_figure is False
    assert result.benefit_types == ["retirement_annuity"]


def test_an_explicit_refusal_is_honoured():
    result = parse_understanding('{"in_scope": false, "out_of_scope_reason": "poem"}')
    assert result.in_scope is False
    assert result.out_of_scope_reason == "poem"


def test_the_fallback_is_permissive():
    result = fallback_understanding()
    assert result.in_scope is True
    assert result.needs_figure is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_understanding.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the module**

Create `src/pension_chat/agent/understanding.py`:

```python
"""What this turn is, decided by the model rather than by a word list.

Scope, whether a figure is wanted, which benefit is in play, which values the
member has just revised and which scenarios they want compared were all read
off regular expressions over the current message. Every gap in those patterns
was a silent refusal: "I'm thinking about retiring" was turned away because
"retiring" does not contain the substring "retire", and "my wife is worried
about what happens to her" because the vocabulary held "spouse" and "widow" but
not "wife". Measured against the live model this reads all eight of those cases
correctly in 2.27s median.

Parsing is defensive on purpose and fails **open**. The safety story is the
post-generation verifier and the deterministic calculation gate; neither is
weakened by letting a question through, and a member wrongly refused has no
recourse at all.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("pension_chat.agent.understanding")

BENEFIT_TYPES = (
    "retirement_annuity",
    "survivor_annuity",
    "disability_benefit",
    "refund_of_contributions",
    "service_credit",
)

UNDERSTAND_SYSTEM = """You read one turn of a conversation between a member of a
Chicago retirement fund and a pension assistant, and decide how the turn should
be routed. You do not answer the question.

You are given what the assistant already knows about this member, what has
already been worked out in this conversation, and the member's new message.

Return JSON only:
{
 "in_scope": true|false,
 "out_of_scope_reason": "<one clause, only when in_scope is false>",
 "intent": "<one short phrase describing what the member actually wants>",
 "benefit_types": ["retirement_annuity"|"survivor_annuity"|"disability_benefit"|"refund_of_contributions"|"service_credit"],
 "needs_figure": true|false,
 "recompute": true|false,
 "revised_facts": {"<fact name>": <value>},
 "scenarios": [{"label": "...", "retirement_age": <int|null>, "basis": "..."}],
 "unresolved": [{"what": "...", "why": "..."}],
 "answerable_part": "<if part of the message is outside the Pension Code, the part that is inside it>",
 "answers_open_question": true|false,
 "search_queries": ["<the statute-worded queries to retrieve on>"]
}

Rules:
- in_scope is true for anything about this member's pension, retirement,
  service, salary basis, survivors, disability, refunds, or the Pension Code,
  and for a bare continuation of an earlier turn. It is false only for a request
  that is plainly somebody else's job.
- needs_figure is true whenever the member wants a number, including a
  comparison of several retirement dates.
- recompute is true when the member corrects, revises or supplies a value that
  a figure already worked out in this conversation depended on.
- revised_facts carries only values the member actually stated, under the fact
  names used in the context you were given.
- You decide which benefit is in play. You never decide which Article covers the
  member; that is settled elsewhere and you must not report it."""


@dataclass
class TurnUnderstanding:
    """What one message is asking for."""

    in_scope: bool = True
    out_of_scope_reason: str = ""
    intent: str = ""
    benefit_types: list[str] = field(default_factory=lambda: ["retirement_annuity"])
    needs_figure: bool = False
    recompute: bool = False
    revised_facts: dict[str, Any] = field(default_factory=dict)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    answerable_part: str = ""
    answers_open_question: bool = False
    search_queries: list[str] = field(default_factory=list)


def fallback_understanding() -> TurnUnderstanding:
    """What to believe when the model could not be read. Deliberately permissive."""
    return TurnUnderstanding(in_scope=True, needs_figure=False)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return default


def _as_str_list(value: Any, allowed: tuple[str, ...] | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item) for item in value if isinstance(item, (str, int, float))]
    if allowed is not None:
        items = [item for item in items if item in allowed]
    return items


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def parse_understanding(raw: str) -> TurnUnderstanding:
    """Turn the model's JSON into a result, surviving anything it actually returns."""
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        logger.warning("Turn understanding was not JSON; falling open")
        return fallback_understanding()
    if not isinstance(data, dict):
        return fallback_understanding()

    benefit_types = _as_str_list(data.get("benefit_types"), BENEFIT_TYPES)
    revised = data.get("revised_facts")

    return TurnUnderstanding(
        in_scope=_as_bool(data.get("in_scope"), True),
        out_of_scope_reason=str(data.get("out_of_scope_reason") or ""),
        intent=str(data.get("intent") or ""),
        benefit_types=benefit_types or ["retirement_annuity"],
        needs_figure=_as_bool(data.get("needs_figure"), False),
        recompute=_as_bool(data.get("recompute"), False),
        revised_facts=dict(revised) if isinstance(revised, dict) else {},
        scenarios=_as_dict_list(data.get("scenarios")),
        unresolved=_as_dict_list(data.get("unresolved")),
        answerable_part=str(data.get("answerable_part") or ""),
        answers_open_question=_as_bool(data.get("answers_open_question"), False),
        search_queries=_as_str_list(data.get("search_queries")),
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_understanding.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/understanding.py tests/test_understanding.py
git commit -m "Read what a message is asking for, instead of matching words in it

Scope, whether a figure is wanted, which benefit is in play and which values
the member just revised were read off regular expressions over the current
sentence, and every gap in those patterns was a silent refusal -- 'I'm
thinking about retiring' turned away because 'retiring' does not contain the
substring 'retire'. This is the type and the parser; the parser fails open,
because the verifier and the calculation gate are what keep an answer honest
and a member wrongly refused has no recourse at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 8: The `understand_turn` node, behind a flag

**Files:**
- Modify: `src/pension_chat/agent/understanding.py` (add the node)
- Modify: `src/pension_chat/agent/graph.py` (`build_graph`, `_in_scope`, `_needs_calculation`, `STAGE_LABELS`)
- Modify: `src/pension_chat/config.py`
- Test: `tests/test_understanding.py`

**Interfaces:**
- Consumes: `TurnUnderstanding`, `parse_understanding`, `fallback_understanding`, `UNDERSTAND_SYSTEM` (Task 7); `WorkSheet` (Task 1); `llm.complete`.
- Produces: `async understand_turn(state) -> dict`; `settings.model_turn_understanding: bool`; `settings.reasoning_effort_understand: str`.

**Background.** `understand_turn` runs concurrently with `fetch_employee_record`, exactly where `plan_request` runs today. It reads the working sheet, which is durable, so from turn 2 onward it has the member's facts without waiting for the record.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_understanding.py`:

```python
async def test_the_node_falls_open_when_the_model_call_fails(monkeypatch):
    from pension_chat.agent import understanding

    async def boom(**kwargs):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(understanding, "complete", boom)
    update = await understanding.understand_turn(
        {"question": "how much pension will i get", "turn_index": 1, "messages": []}
    )
    assert update["in_scope"] is True
    assert update["requires_calculation"] is False


async def test_the_node_maps_its_result_onto_the_state_keys_the_graph_reads(monkeypatch):
    from pension_chat.agent import understanding

    async def canned(**kwargs):
        return (
            '{"in_scope": true, "needs_figure": true, "recompute": true, '
            '"benefit_types": ["retirement_annuity"], '
            '"revised_facts": {"service_years": 23}, '
            '"search_queries": ["minimum annuity"]}'
        )

    monkeypatch.setattr(understanding, "complete", canned)
    update = await understanding.understand_turn(
        {
            "question": "actually it's more like 23 years",
            "turn_index": 2,
            "messages": [],
            "member_facts": {"service_years": 21.5},
            "assumptions": {},
            "baseline": {
                "result": {"annual_amount": "32126.78"},
                "provision": {"section": "11-134"},
                "rests_on": ["service_years"],
                "turn": 1,
            },
        }
    )
    assert update["requires_calculation"] is True
    assert update["formula_types_needed"] == ["retirement_annuity"]
    assert update["search_queries"] == ["minimum annuity"]
    assert update["stale_figures"] == ["now"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_understanding.py -k node -v`
Expected: FAIL — `AttributeError: module has no attribute 'understand_turn'`

- [ ] **Step 3: Add the two config settings**

In `src/pension_chat/config.py`, after `materiality_threshold`:

```python
    # Routing by model rather than by word list. Off by default so the change
    # can be measured against the eval suite before it becomes the only path.
    model_turn_understanding: bool = False
    reasoning_effort_understand: str = "none"
```

- [ ] **Step 4: Add `stale_figures` to the state schema**

In `src/pension_chat/agent/state.py`, in the "Plan" block beside `requires_calculation`:

```python
    # Figures an earlier turn worked out whose inputs this turn contradicted.
    # Non-empty means the calculation branch runs whatever words were used.
    stale_figures: list[str]
```

`stale_figures` is per-turn, so it needs no entry in `DURABLE_KEYS`.

- [ ] **Step 5: Write the node**

Append to `src/pension_chat/agent/understanding.py`:

```python
import time

from pension_chat.agent.llm import complete
from pension_chat.agent.worksheet import WorkSheet
from pension_chat.config import get_settings


def _context(sheet: WorkSheet, state: dict[str, Any]) -> str:
    """What the model needs to know about this member and this conversation."""
    known = ", ".join(
        f"{name}={value}" for name, value in sorted(sheet.usable_values().items())
    )
    figures = "; ".join(
        f"{label}: {figure.result} from {list(figure.rests_on)} "
        f"under {figure.provision.get('section')}"
        for label, figure in sheet.figures.items()
    )
    open_items = "; ".join(item.what for item in sheet.open_items)

    lines = [f"What is known about this member: {known or 'nothing yet'}"]
    if figures:
        lines.append(f"Worked out earlier in this conversation: {figures}")
    if open_items:
        lines.append(f"Put to the member last turn: {open_items}")
    return "\n".join(lines)


async def understand_turn(state: dict[str, Any]) -> dict[str, Any]:
    """Decide what this turn is, and what an earlier turn's figures owe it.

    Runs concurrently with the record fetch. That is safe because the sheet is
    durable: from the second turn onwards the member's facts are already in the
    checkpoint, and on the first turn there is nothing yet to contradict.
    """
    started = time.perf_counter()
    settings = get_settings()
    sheet = WorkSheet.from_state(state)

    history = "\n".join(
        f"{getattr(message, 'type', '?')}: {message.content}"
        for message in (state.get("messages") or [])[-settings.history_turns * 2 :]
    )

    try:
        raw = await complete(
            node="understand_turn",
            messages=[
                {"role": "system", "content": UNDERSTAND_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"{_context(sheet, state)}\n\n"
                        f"Conversation so far:\n{history}\n\n"
                        f"Member's new message: {state['question']}"
                    ),
                },
            ],
            reasoning_effort=settings.reasoning_effort_understand,
            json_mode=True,
        )
        result = parse_understanding(raw)
    except Exception as exc:
        logger.warning("Turn understanding failed (%s); falling open", exc)
        result = fallback_understanding()

    stale = sorted(sheet.apply_revisions(result.revised_facts, state.get("turn_index") or 0))

    return {
        "in_scope": result.in_scope,
        "out_of_scope_reason": result.out_of_scope_reason,
        "intent": result.intent,
        "requires_calculation": result.needs_figure or bool(stale),
        "formula_types_needed": result.benefit_types if result.needs_figure or stale else [],
        "search_queries": result.search_queries,
        "scenarios": result.scenarios,
        "unresolved": result.unresolved,
        "answerable_part": result.answerable_part,
        "stale_figures": stale,
        **sheet.to_state_update(),
        "timings": {"understand_turn": time.perf_counter() - started},
    }
```

Add `intent: str`, `unresolved: list[dict]` and `answerable_part: str` to `PensionAgentState` in `state.py`, in the "Plan" block.

- [ ] **Step 6: Wire the node into the graph behind the flag**

In `src/pension_chat/agent/graph.py`:

Add to `STAGE_LABELS`: `"understand_turn": "reading your question",`

Import the node:

```python
from pension_chat.agent.understanding import understand_turn
```

In `build_graph`, register the node and choose the entry edge:

```python
    settings = get_settings()
    planner = "understand_turn" if settings.model_turn_understanding else "plan_request"

    workflow.add_node("understand_turn", understand_turn)
    ...
    workflow.add_conditional_edges(
        "triage",
        _in_scope,
        [planner, "fetch_employee_record", "decline"],
    )
    workflow.add_edge([planner, "fetch_employee_record"], "assess_coverage")
```

and change `_in_scope` to read the flag:

```python
def _in_scope(state: PensionAgentState) -> str | list[str]:
    """Fan out to both independent nodes, or short-circuit to the decline path."""
    if not state.get("in_scope", True):
        return "decline"
    planner = (
        "understand_turn"
        if get_settings().model_turn_understanding
        else "plan_request"
    )
    return [planner, "fetch_employee_record"]
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_understanding.py tests/ -q`
Expected: PASS. The flag defaults to off, so no existing test changes behaviour.

- [ ] **Step 8: Verify both paths build**

Run: `uv run python -c "from pension_chat.agent.graph import build_graph; build_graph(); print('default ok')"`
Then: `uv run env PENSION_CHAT_MODEL_TURN_UNDERSTANDING=true python -c "from pension_chat.config import get_settings; get_settings.cache_clear(); from pension_chat.agent.graph import build_graph; build_graph(); print('flagged ok')"`
Expected: both print. If the settings class uses a prefix other than `PENSION_CHAT_`, read `src/pension_chat/config.py`'s `model_config` and use the right one.

- [ ] **Step 9: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/understanding.py src/pension_chat/agent/graph.py src/pension_chat/agent/state.py src/pension_chat/config.py tests/test_understanding.py
git commit -m "Put the model on the routing decision, behind a flag

understand_turn takes the place plan_request holds in the graph, beside the
record fetch rather than after it -- the sheet is durable, so from the second
turn on it already knows the member without waiting. It also returns which
earlier figures this turn contradicted, so a member correcting their service
recomputes without having used a word anybody anticipated. Off by default
until the eval suite says otherwise.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 9: Route on staleness, and reduce `triage`

**Files:**
- Modify: `src/pension_chat/agent/graph.py` (`_needs_calculation`)
- Modify: `src/pension_chat/agent/nodes.py` (`triage`)
- Modify: `src/pension_chat/agent/guardrails.py` (`classify_scope`)
- Test: `tests/test_scope.py`

**Interfaces:**
- Consumes: `state["stale_figures"]` (Task 8).
- Produces: `classify_scope(question, *, has_history=False, model_routing=False) -> ScopeVerdict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scope.py`:

```python
def test_with_model_routing_only_the_blatant_cases_are_refused_deterministically():
    from pension_chat.agent.guardrails import classify_scope

    # High precision: when this pattern fires it is right, so it stays free.
    assert not classify_scope("write me a poem about my dog", model_routing=True).in_scope

    # Low recall in the other direction: these were all refused by the word
    # list and are all pension questions.
    for question in [
        "I'm starting to think seriously about retiring but I don't know where to begin",
        "My wife is worried about what happens to her if something happens to me",
        "Does any of this get taxed, and do I still get health insurance?",
    ]:
        assert classify_scope(question, model_routing=True).in_scope, question


def test_without_model_routing_the_old_behaviour_is_unchanged():
    from pension_chat.agent.guardrails import classify_scope

    assert classify_scope("how much pension will i get").in_scope
    assert not classify_scope("write me a poem about my dog").in_scope
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_scope.py -k model_routing -v`
Expected: FAIL — `classify_scope() got an unexpected keyword argument 'model_routing'`

- [ ] **Step 3: Add the parameter to `classify_scope`**

In `src/pension_chat/agent/guardrails.py`, change the signature and add the early return:

```python
def classify_scope(
    question: str,
    *,
    has_history: bool = False,
    model_routing: bool = False,
) -> ScopeVerdict:
```

and immediately after the `_OUT_OF_SCOPE.search(text)` check:

```python
    if model_routing:
        # The deny pattern above is high precision -- when it fires it is right,
        # so it stays as a free fast path. The vocabulary check below is the
        # opposite: when it says "no pension words here, refuse" it is wrong
        # about "retiring", "my wife" and "does this get taxed". Keep the regex
        # where it accuses; delete it where it acquits.
        return ScopeVerdict(True)
```

- [ ] **Step 4: Pass the flag from `triage`**

In `src/pension_chat/agent/nodes.py`, in `triage`:

```python
    settings = get_settings()
    scope = guardrails.classify_scope(
        question,
        has_history=len(state.get("messages") or []) > 1,
        model_routing=settings.model_turn_understanding,
    )
```

- [ ] **Step 5: Route on staleness**

In `src/pension_chat/agent/graph.py`, replace `_needs_calculation`:

```python
def _needs_calculation(state: PensionAgentState) -> str:
    """Run the calculation branch for a figure that is wanted or has gone stale.

    Staleness is the half that does not depend on the words used. A member who
    writes "actually it's more like 23 years" has asked for a recalculation
    without asking for one, and the figure they are correcting is what says so.
    """
    if state.get("stale_figures"):
        return "resolve_inputs"
    if state.get("requires_calculation") and state.get("formula_types_needed"):
        return "resolve_inputs"
    return "prepare_answer"
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/graph.py src/pension_chat/agent/nodes.py src/pension_chat/agent/guardrails.py tests/test_scope.py
git commit -m "Keep the regex where it accuses, drop it where it acquits

The out-of-scope pattern is high precision: when it matches a request for a
poem it is right, and it stays as a free fast path. The vocabulary
membership test is the opposite -- when it concludes 'no pension words here,
refuse' it is wrong about retiring, about a member's wife, and about whether
a pension is taxed. Under model routing the accept half goes and the deny
half stays.

The calculation branch now also runs on a stale figure, which is the half of
the decision that does not depend on which words the member reached for.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 10: The degradation ladder

**Files:**
- Create: `src/pension_chat/agent/presentation.py`
- Test: `tests/test_degradation.py`

**Interfaces:**
- Consumes: `WorkSheet` (Task 1).
- Produces: `RUNG_CALCULATED = 1` … `RUNG_ASK = 5`; `STATUS_LABELS: dict[int, str]`; `select_rung(sheet, state) -> int`; `shape_for(sheet, state) -> str` returning one of `"figure"`, `"comparison"`, `"legal"`, `"partial"`, `"clarify"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_degradation.py`:

```python
"""Tests for choosing how much of an answer the evidence supports.

The invariant that matters is monotonicity: adding a fact must never move the
answer down the ladder. Without it the ladder becomes its own source of
erratic behaviour, which is the thing it exists to remove.
"""

from __future__ import annotations

from pension_chat.agent.presentation import (
    RUNG_ASK,
    RUNG_ASSUMED,
    RUNG_CALCULATED,
    RUNG_ONE_MORE,
    RUNG_PATHWAYS,
    STATUS_LABELS,
    select_rung,
    shape_for,
)
from pension_chat.agent.worksheet import SOURCE_ASSUMED, WorkSheet

FACTS = {"age": 56, "service_years": 21.5, "final_average_salary": 69956.4}
CALC = {"retirement_annuity": {"base": {"ok": True, "annual_amount": "32126.78"}}}


def _sheet(**extra):
    return WorkSheet.from_state({"turn_index": 1, "member_facts": FACTS, "assumptions": {}, **extra})


def test_a_fully_sourced_figure_is_rung_one():
    assert select_rung(_sheet(), {"calculation_result": CALC, "missing_inputs": []}) == RUNG_CALCULATED


def test_a_figure_resting_on_an_assumption_is_rung_two():
    sheet = _sheet()
    sheet.record_fact("withdrawal_date", "2026-09-09", SOURCE_ASSUMED, basis="today")
    assert select_rung(sheet, {"calculation_result": CALC, "missing_inputs": []}) == RUNG_ASSUMED


def test_one_missing_input_is_rung_three():
    state = {"calculation_result": None, "missing_inputs": [{"name": "withdrawal_date"}]}
    assert select_rung(_sheet(), state) == RUNG_ONE_MORE


def test_competing_pathways_are_rung_four():
    state = {
        "calculation_result": None,
        "missing_inputs": [],
        "candidate_articles": ["11", "12"],
    }
    assert select_rung(_sheet(), state) == RUNG_PATHWAYS


def test_several_missing_inputs_is_rung_five():
    state = {
        "calculation_result": None,
        "missing_inputs": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
    }
    assert select_rung(_sheet(), state) == RUNG_ASK


def test_adding_a_fact_never_moves_the_answer_down_the_ladder():
    before = select_rung(
        _sheet(), {"calculation_result": None, "missing_inputs": [{"name": "a"}, {"name": "b"}]}
    )
    after = select_rung(_sheet(), {"calculation_result": CALC, "missing_inputs": []})
    assert after <= before


def test_every_rung_has_a_status_label():
    for rung in (RUNG_CALCULATED, RUNG_ASSUMED, RUNG_ONE_MORE, RUNG_PATHWAYS, RUNG_ASK):
        assert STATUS_LABELS[rung]


def test_a_comparison_takes_the_comparison_shape():
    state = {"calculation_result": CALC, "missing_inputs": [], "scenario_set": {"at 60": {}}}
    assert shape_for(_sheet(), state) == "comparison"


def test_a_two_part_question_takes_the_partial_shape():
    state = {"calculation_result": None, "missing_inputs": [], "answerable_part": "whether the annuity is taxed"}
    assert shape_for(_sheet(), state) == "partial"


def test_a_question_with_no_figure_wanted_takes_the_legal_shape():
    state = {"calculation_result": None, "missing_inputs": [], "requires_calculation": False}
    assert shape_for(_sheet(), state) == "legal"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_degradation.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the ladder**

Create `src/pension_chat/agent/presentation.py`:

```python
"""How much of an answer the evidence supports, and what shape it takes.

The system had two moves: state a figure, or say it could not. Everything
between them -- a figure resting on one stated assumption, the formula with the
one missing value shown in it, the law plus what is established -- did not
exist, and that gap is the whole of the "rigid" complaint.

The rung is chosen here, in Python, from the sheet. The model is then given one
shape rather than six, which is both less to get wrong and less to read.
"""

from __future__ import annotations

from typing import Any

from pension_chat.agent.worksheet import WorkSheet

RUNG_CALCULATED = 1
RUNG_ASSUMED = 2
RUNG_ONE_MORE = 3
RUNG_PATHWAYS = 4
RUNG_ASK = 5

#: What the member is shown. The rung is the status: the badge cannot drift
#: from reality because nobody writes it, it names the branch that produced
#: the answer.
STATUS_LABELS: dict[int, str] = {
    RUNG_CALCULATED: "Calculated",
    RUNG_ASSUMED: "Calculated, with assumptions",
    RUNG_ONE_MORE: "Needs one more detail",
    RUNG_PATHWAYS: "More than one pathway",
    RUNG_ASK: "Needs information",
}


def _has_figure(state: dict[str, Any]) -> bool:
    for entry in (state.get("calculation_result") or {}).values():
        if isinstance(entry, dict) and (entry.get("base") or {}).get("ok"):
            return True
    return False


def select_rung(sheet: WorkSheet, state: dict[str, Any]) -> int:
    """The highest rung the evidence supports."""
    if _has_figure(state):
        return RUNG_ASSUMED if sheet.assumed_facts() else RUNG_CALCULATED

    missing = state.get("missing_inputs") or []
    if len(missing) == 1:
        return RUNG_ONE_MORE
    if len(state.get("candidate_articles") or []) > 1:
        return RUNG_PATHWAYS
    if missing:
        return RUNG_ASK
    return RUNG_PATHWAYS


def shape_for(sheet: WorkSheet, state: dict[str, Any]) -> str:
    """Which response template this turn uses.

    Three shapes carry no figure and so are not rungs: an explanation of the
    law, a comparison of dates, and an answer whose question was half outside
    the Pension Code.
    """
    if state.get("scenario_set"):
        return "comparison"
    if state.get("answerable_part"):
        return "partial"
    if _has_figure(state):
        return "figure"
    if not state.get("requires_calculation"):
        return "legal"
    return "clarify"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_degradation.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/presentation.py tests/test_degradation.py
git commit -m "Give the answer somewhere to land between a figure and a refusal

The system could state a number or say it could not, and jumped straight
from one to the other. The rungs in between -- a figure resting on one
labelled assumption, the formula with the missing value shown inside it, the
law plus what is established -- are chosen here from the sheet rather than
by the model reading prose rules, and adding a fact can never move an answer
down the ladder.

The rung is also the status the member sees, so the badge cannot drift from
what actually happened: nobody writes it, it names the branch.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 11: Rendering the computed blocks

**Files:**
- Create: `src/pension_chat/agent/blocks.py`
- Test: `tests/test_presentation.py`

**Interfaces:**
- Consumes: `WorkSheet` (Task 1), `assumption_policy.plausible_range` (Task 4), `STATUS_LABELS` (Task 10).
- Produces: `MARKERS: dict[str, str]`; `render_worked_steps(result) -> str`; `render_comparison(scenarios) -> str`; `render_assumptions(sheet) -> str`; `render_basis(citations) -> str`; `render_status(rung) -> str`; `MarkerSubstituter` with `.feed(delta) -> str` and `.flush() -> str`.

**Background.** The model currently transcribes interpreter output into a markdown table, and `ANSWER_SYSTEM` spends a whole absolute rule insisting transcription is not calculation. These renderers take that job away from it. `MarkerSubstituter` is what keeps streaming working: the model emits `<<<WORKED_STEPS>>>`, and the substituter swaps in the rendered block as the stream passes it, holding back only enough characters that a marker split across two deltas is still recognised.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_presentation.py`:

```python
"""Tests for the computed answer blocks and the streaming marker substituter.

These blocks are pure functions of the working sheet, which is the point: a
table the model transcribes can be mistranscribed, and a table Python renders
cannot.
"""

from __future__ import annotations

from pension_chat.agent.blocks import (
    MarkerSubstituter,
    render_assumptions,
    render_comparison,
    render_status,
    render_worked_steps,
)
from pension_chat.agent.presentation import RUNG_ASSUMED
from pension_chat.agent.worksheet import SOURCE_ASSUMED, WorkSheet

RESULT = {
    "inputs_used": {"final_average_salary": "69956.40", "service_years": "21.5"},
    "statutory_values_used": {"annual_rate": "2.40%"},
    "steps": [
        {"label": "Before early-retirement reduction", "amount": "36097.50"},
        {"label": "Reduction (44 months x 0.25%)", "amount": "-11.00%"},
    ],
    "annual_amount": "32126.78",
    "monthly_amount": "2677.23",
}


def test_the_worked_steps_table_is_well_formed_and_right_aligned():
    table = render_worked_steps(RESULT)
    lines = [line for line in table.splitlines() if line.startswith("|")]
    assert lines[1] == "|---|---:|"
    assert all(line.count("|") == 3 for line in lines)
    assert "**Annual pension**" in table
    assert "$32,126.78" in table
    assert "$2,677.23" in table


def test_money_is_formatted_once_and_only_once():
    table = render_worked_steps(RESULT)
    assert "$$" not in table
    assert "69956.40" not in table
    assert "$69,956.40" in table


def test_the_comparison_table_carries_the_service_column():
    table = render_comparison([
        {"label": "Now, at 56", "service_years": "21.5", "annual": "32126.78", "monthly": "2677.23"},
        {"label": "At 58", "service_years": "23.2", "annual": "36584.70", "monthly": "3048.72"},
    ])
    assert "| Years of service |" in table
    assert "| 23.2 |" in table
    assert "$36,584.70" in table


def test_the_assumptions_block_names_the_basis_and_stays_out_of_the_way_when_empty():
    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": {}, "assumptions": {}})
    assert render_assumptions(sheet) == ""

    sheet.record_fact(
        "months_before_age_60", 42, SOURCE_ASSUMED,
        basis="your birthday falling mid-year, because your date of birth is not on file",
    )
    block = render_assumptions(sheet)
    assert "What I assumed" in block
    assert "birthday falling mid-year" in block


def test_the_status_line_names_the_rung():
    assert render_status(RUNG_ASSUMED) == "**Calculated, with assumptions**"


def test_the_substituter_swaps_a_marker_that_arrives_whole():
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = sub.feed("Here it is.\n\n<<<WORKED_STEPS>>>\n\nDone.") + sub.flush()
    assert "TABLE" in out
    assert "<<<" not in out


def test_the_substituter_swaps_a_marker_split_across_deltas():
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = "".join(sub.feed(part) for part in ["Here. <<<WORK", "ED_ST", "EPS>>> done."])
    out += sub.flush()
    assert "TABLE" in out
    assert "<<<" not in out


def test_a_duplicated_marker_renders_the_block_once():
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = sub.feed("<<<WORKED_STEPS>>> and again <<<WORKED_STEPS>>>") + sub.flush()
    assert out.count("TABLE") == 1
    assert "<<<" not in out


def test_an_unused_block_is_reported_so_the_caller_can_append_it():
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = sub.feed("No marker here at all.") + sub.flush()
    assert "TABLE" not in out
    assert sub.unused() == ["TABLE"]


def test_an_unknown_marker_is_removed_rather_than_shown_to_the_member():
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE"})
    out = sub.feed("Text <<<NOT_A_BLOCK>>> more.") + sub.flush()
    assert "<<<" not in out
    assert "NOT_A_BLOCK" not in out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_presentation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.agent.blocks'`

- [ ] **Step 3: Write the renderers**

Create `src/pension_chat/agent/blocks.py`:

```python
"""The parts of an answer that Python renders rather than the model writing.

The worked-steps table, the comparison table, the assumptions block and the
legal-basis footer are pure functions of what the interpreter produced and what
the sheet holds. Asking a model to transcribe them was always a small,
unnecessary risk -- and it cost a whole absolute rule in the answer prompt
insisting that transcription is not calculation.

The architecture's rule gains one clause. The LLM plans and writes, code
calculates -- and code formats what it calculated.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from pension_chat.agent.presentation import STATUS_LABELS
from pension_chat.agent.worksheet import WorkSheet

#: Placeholders the model emits where a computed block belongs.
MARKERS = {
    "WORKED_STEPS": "the worked-steps table",
    "COMPARISON": "the comparison table",
    "ASSUMPTIONS": "the assumptions block",
    "BASIS": "the legal-basis footer",
    "STATUS": "the status line",
}

_MARKER_RE = re.compile(r"<<<([A-Z_]{1,32})>>>")
#: Longest possible marker text, so a marker split across deltas is never missed.
_MAX_MARKER = len("<<<") + 32 + len(">>>")

_LABELS = {
    "final_average_salary": "Highest average salary (best 4 years)",
    "highest_average_annual_salary_4yr": "Highest average salary (best 4 years)",
    "service_years": "Years of service",
    "years_of_service": "Years of service",
    "creditable_service_years": "Years of service",
    "annual_rate": "Annual rate",
    "age": "Age",
}


def _money(value: Any) -> str:
    try:
        return f"${Decimal(str(value)):,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


def _amount(value: Any) -> str:
    """Money, a percentage or a plain quantity, formatted as it should read."""
    text = str(value).strip()
    if text.endswith("%"):
        # Already a percentage, positive or negative. Leave it alone.
        return text
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    if number == number.to_integral_value() and abs(number) < 100:
        return f"{number.normalize():f}"
    return _money(number)


def _label(name: str) -> str:
    return _LABELS.get(name, name.replace("_", " ").capitalize())


def render_worked_steps(result: Mapping[str, Any]) -> str:
    """The two-column table showing how one figure was reached."""
    rows: list[tuple[str, str]] = []
    for name, value in (result.get("inputs_used") or {}).items():
        rows.append((_label(name), _amount(value)))
    for name, value in (result.get("statutory_values_used") or {}).items():
        rows.append((_label(name), _amount(value)))
    for step in result.get("steps") or []:
        rows.append((str(step.get("label") or ""), _amount(step.get("amount"))))

    if annual := result.get("annual_amount"):
        rows.append(("**Annual pension**", f"**{_money(annual)}**"))
    if monthly := result.get("monthly_amount"):
        rows.append(("**Monthly pension**", f"**{_money(monthly)}**"))

    body = "\n".join(f"| {label} | {amount} |" for label, amount in rows)
    return f"**How that is worked out**\n\n| Step | Amount |\n|---|---:|\n{body}"


def render_comparison(options: Sequence[Mapping[str, Any]]) -> str:
    """One row per retirement date, soonest first."""
    header = (
        "| If you retire | Years of service | Annual pension | Monthly pension |\n"
        "|---|---:|---:|---:|"
    )
    rows = [
        f"| {option.get('label', '')} | {option.get('service_years', '')} | "
        f"{_money(option.get('annual'))} | {_money(option.get('monthly'))} |"
        for option in options
    ]
    return "\n".join([header, *rows])


def render_assumptions(sheet: WorkSheet) -> str:
    """What was filled in, and on whose say-so. Empty when nothing was."""
    assumed = sheet.assumed_facts()
    if not assumed:
        return ""
    lines = [f"- {fact.basis or fact.name}" for fact in assumed.values()]
    return "**What I assumed**\n\n" + "\n".join(lines)


def render_basis(citations: Sequence[str]) -> str:
    """The provisions the answer rested on, once, at the end."""
    if not citations:
        return ""
    joined = " · ".join(f"[40 ILCS 5/{section}]" for section in citations)
    return f"**Legal basis** {joined}"


def render_status(rung: int) -> str:
    return f"**{STATUS_LABELS.get(rung, '')}**"


class MarkerSubstituter:
    """Swap computed blocks into a token stream as it passes.

    Deltas arrive in arbitrary pieces, so a marker can be split across two of
    them. The substituter holds back the last few characters until it can be
    sure they are not the start of one.
    """

    def __init__(self, blocks: Mapping[str, str]) -> None:
        self._blocks = dict(blocks)
        self._used: set[str] = set()
        self._buffer = ""

    def feed(self, delta: str) -> str:
        self._buffer += delta
        out, self._buffer = self._consume(self._buffer, hold_tail=True)
        return out

    def flush(self) -> str:
        out, self._buffer = self._consume(self._buffer, hold_tail=False)
        return out

    def unused(self) -> list[str]:
        """Blocks whose marker never arrived, so the caller can append them."""
        return [
            block for name, block in self._blocks.items()
            if name not in self._used and block
        ]

    def _consume(self, text: str, *, hold_tail: bool) -> tuple[str, str]:
        out: list[str] = []
        position = 0
        for match in _MARKER_RE.finditer(text):
            out.append(text[position : match.start()])
            name = match.group(1)
            if name in self._blocks and name not in self._used:
                out.append(self._blocks[name])
                self._used.add(name)
            # A duplicated marker, or one naming no block, is dropped rather
            # than shown to the member.
            position = match.end()

        remainder = text[position:]
        if hold_tail:
            keep = min(len(remainder), _MAX_MARKER)
            cut = len(remainder) - keep
            return "".join(out) + remainder[:cut], remainder[cut:]
        return "".join(out) + remainder, ""
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_presentation.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/blocks.py tests/test_presentation.py
git commit -m "Let code format what code computed

The worked-steps table, the comparison table, the assumptions block and the
legal-basis footer are pure functions of the interpreter's output and the
sheet, and asking a model to transcribe them was a risk that bought nothing
-- it also cost a whole absolute rule in the answer prompt insisting that
transcription is not calculation. The founding split gains a clause: the LLM
plans and writes, code calculates, and code formats what it calculated.

Streaming survives on a marker the substituter swaps as the stream passes
it, holding back just enough characters that a marker split across two
deltas is still recognised. A duplicated marker renders once, an unknown one
is dropped rather than shown, and a marker that never arrives leaves its
block for the caller to append.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 12: The template registry, and `prepare_answer` using it

**Files:**
- Modify: `src/pension_chat/agent/presentation.py` (add `Template`, `TEMPLATES`, `template_for`)
- Modify: `src/pension_chat/agent/nodes.py` (`prepare_answer`, `ANSWER_SYSTEM`)
- Test: `tests/test_templates.py`

**Interfaces:**
- Consumes: `shape_for`, `select_rung` (Task 10); `blocks.MARKERS` and the renderers (Task 11).
- Produces: `Template` dataclass with fields `shape: str`, `slots: tuple[str, ...]`, `guidance: str`; `TEMPLATES: dict[str, Template]`; `template_for(shape) -> Template`. State gains `answer_blocks: dict[str, str]` and `answer_rung: int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_templates.py`:

```python
"""Tests for the response-template registry.

A template is a slot order plus the prose guidance for the slots the model
writes. The registry is data, so these tests walk it: every shape has one,
every computed slot has a renderer, and no template asks the model to write a
block that Python owns.
"""

from __future__ import annotations

from pension_chat.agent import blocks
from pension_chat.agent.presentation import (
    COMPUTED_SLOTS,
    TEMPLATES,
    Template,
    shape_for,
    template_for,
)
from pension_chat.agent.worksheet import WorkSheet

SHAPES = ("figure", "comparison", "legal", "partial", "clarify")


def test_every_shape_has_a_template():
    for shape in SHAPES:
        assert isinstance(template_for(shape), Template)


def test_every_computed_slot_names_a_real_marker():
    for template in TEMPLATES.values():
        for slot in template.slots:
            if slot in COMPUTED_SLOTS:
                assert slot in blocks.MARKERS, f"{template.shape}: {slot} has no marker"


def test_every_template_opens_with_the_lede_so_the_answer_comes_first():
    for template in TEMPLATES.values():
        assert template.slots[0] in {"STATUS", "lede"}, template.shape
        assert "lede" in template.slots, template.shape


def test_every_template_that_shows_a_figure_shows_how_it_was_reached():
    for shape in ("figure", "comparison"):
        slots = template_for(shape).slots
        assert "WORKED_STEPS" in slots or "COMPARISON" in slots


def test_guidance_never_asks_the_model_to_write_a_computed_block():
    forbidden = ("| Step | Amount |", "|---|---:|", "How that is worked out")
    for template in TEMPLATES.values():
        for phrase in forbidden:
            assert phrase not in template.guidance, f"{template.shape} still describes a table"


def test_an_unknown_shape_falls_back_to_the_legal_template():
    assert template_for("nonsense").shape == "legal"


def test_shape_and_template_agree_on_a_comparison_turn():
    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": {}, "assumptions": {}})
    shape = shape_for(sheet, {"scenario_set": {"at 60": {}}, "calculation_result": {}})
    assert "COMPARISON" in template_for(shape).slots
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_templates.py -v`
Expected: FAIL — `ImportError: cannot import name 'COMPUTED_SLOTS'`

- [ ] **Step 3: Add the registry**

Append to `src/pension_chat/agent/presentation.py`:

```python
from dataclasses import dataclass

#: Slots Python renders. Everything else is prose the model writes.
COMPUTED_SLOTS = frozenset({"STATUS", "WORKED_STEPS", "COMPARISON", "ASSUMPTIONS", "BASIS"})


@dataclass(frozen=True)
class Template:
    """One response shape: what appears, in what order, and what to say in it."""

    shape: str
    slots: tuple[str, ...]
    guidance: str


TEMPLATES: dict[str, Template] = {
    "figure": Template(
        shape="figure",
        slots=("STATUS", "lede", "WORKED_STEPS", "why", "ASSUMPTIONS", "alternatives", "BASIS"),
        guidance=(
            "lede: the amount, annual first with monthly in brackets, and the "
            "circumstances it assumes. One or two sentences.\n"
            "why: why this provision governs, in the Code's own terms. Two "
            "sentences at most.\n"
            "alternatives: which other provisions were weighed and exactly why "
            "each is not the answer. Never write a provision off as inapplicable "
            "when it applies and simply cannot be put to a number."
        ),
    ),
    "comparison": Template(
        shape="comparison",
        slots=("STATUS", "lede", "COMPARISON", "what_changes", "ASSUMPTIONS", "BASIS"),
        guidance=(
            "lede: name the range -- the soonest option and the best one -- so a "
            "member who reads no further still has the answer. Never open with "
            "the table.\n"
            "what_changes: a sentence or two on what drives the difference, then "
            "one line on what the projection holds constant."
        ),
    ),
    "legal": Template(
        shape="legal",
        slots=("lede", "detail", "BASIS"),
        guidance=(
            "lede: answer the question outright in plain English, before any "
            "subsection is named.\n"
            "detail: the conditions or consequences -- a short bullet list if "
            "there are more than two, prose otherwise. Three to six sentences."
        ),
    ),
    "partial": Template(
        shape="partial",
        slots=("lede", "detail", "outside", "BASIS"),
        guidance=(
            "lede: answer the part of the question the Pension Code covers.\n"
            "detail: the conditions that bear on it.\n"
            "outside: one line naming the part that is not in the Code and who "
            "does own it -- the fund, HR, the IRS. Never answer it yourself, and "
            "never guess at a tax or insurance rule."
        ),
    ),
    "clarify": Template(
        shape="clarify",
        slots=("STATUS", "lede", "established", "question", "BASIS"),
        guidance=(
            "lede: name what is unclear and why it matters to them. One sentence.\n"
            "established: what you already have, in one short sentence, so it is "
            "plain you read what they told you.\n"
            "question: the one or two things that would settle it, in the "
            "plainest form they take. Closed questions where the Code allows. "
            "Prose, never a table, and never more than two questions."
        ),
    ),
}


def template_for(shape: str) -> Template:
    return TEMPLATES.get(shape, TEMPLATES["legal"])
```

- [ ] **Step 4: Build the blocks in `prepare_answer`**

In `src/pension_chat/agent/nodes.py`, add the imports:

```python
from pension_chat.agent import blocks
from pension_chat.agent.presentation import (
    COMPUTED_SLOTS,
    select_rung,
    shape_for,
    template_for,
)
```

At the end of `prepare_answer`, before it returns, replace the `return` with:

```python
    sheet = WorkSheet.from_state(state)
    rung = select_rung(sheet, dict(state))
    shape = shape_for(sheet, dict(state))
    template = template_for(shape)

    computed: dict[str, str] = {}
    for slot in template.slots:
        if slot not in COMPUTED_SLOTS:
            continue
        if slot == "STATUS":
            computed[slot] = blocks.render_status(rung)
        elif slot == "WORKED_STEPS":
            for entry in (state.get("calculation_result") or {}).values():
                base = (entry or {}).get("base") or {}
                if base.get("ok"):
                    computed[slot] = blocks.render_worked_steps(base)
                    break
        elif slot == "COMPARISON":
            options = _comparison_rows(state)
            if options:
                computed[slot] = blocks.render_comparison(options)
        elif slot == "ASSUMPTIONS":
            if rendered := blocks.render_assumptions(sheet):
                computed[slot] = rendered
        elif slot == "BASIS":
            if rendered := blocks.render_basis([c["section"] for c in citations]):
                computed[slot] = rendered

    order = "\n".join(
        f"{index}. {slot}" + (" (I will insert this; emit the marker "
                              f"<<<{slot}>>> on its own line)" if slot in COMPUTED_SLOTS else "")
        for index, slot in enumerate(template.slots, 1)
        if slot not in COMPUTED_SLOTS or slot in computed
    )
    messages.append({
        "role": "system",
        "content": (
            f"Write this answer in the following parts, in this order:\n{order}\n\n"
            f"{template.guidance}"
        ),
    })

    return {
        "answer_messages": messages,
        "citations": citations,
        "answer_blocks": computed,
        "answer_rung": rung,
        "answer_shape": shape,
        "timings": {"prepare_answer": time.perf_counter() - started},
    }
```

Add the helper above `prepare_answer`:

```python
def _comparison_rows(state: PensionAgentState) -> list[dict[str, Any]]:
    """One row per scenario, soonest first, for the comparison table.

    Sorted on the retirement age rather than the label. Sorting the labels puts
    "At 58" above "Now, at 56", which is the wrong way round and reverses the
    one thing the table is for.
    """
    rows: list[dict[str, Any]] = []
    for label, entry in (state.get("scenario_set") or {}).items():
        result = (entry or {}).get("result") or {}
        if not result:
            continue
        assumptions = (entry or {}).get("assumptions") or {}
        rows.append({
            "label": label,
            "retirement_age": assumptions.get("retirement_age"),
            "service_years": assumptions.get("service_years", ""),
            "annual": result.get("annual_amount"),
            "monthly": result.get("monthly_amount"),
        })

    def _when(row: dict[str, Any]) -> float:
        age = row.get("retirement_age")
        try:
            return float(age)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # A scenario with no resolved age sorts last rather than crashing
            # the table it belongs in.
            return float("inf")

    return sorted(rows, key=_when)
```

Add `answer_blocks: dict[str, str]`, `answer_rung: int` and `answer_shape: str` to `PensionAgentState` in `state.py`, in the "Final Output" block.

- [ ] **Step 5: Trim `ANSWER_SYSTEM`**

In `src/pension_chat/agent/nodes.py`, delete from `ANSWER_SYSTEM` the whole `SHAPE` section (every per-shape description, since the template now supplies one shape per turn) and absolute rules 2 and 4's formatting half. Keep the `VOICE`, `CLOSING`, `CITING` sections and rules 1, 3, 5, 6, 7, 8, 9, 10, 11, 12 renumbered in place. Replace the deleted `SHAPE` section with:

```
SHAPE

You will be told which parts this answer has and in what order. Write the prose
parts. Where you are told to emit a marker such as <<<WORKED_STEPS>>>, put it on
its own line and write nothing in its place — that part is filled in for you,
and anything you write there will be discarded.
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/ -q`
Expected: PASS. `tests/test_answer_style.py` asserts on `ANSWER_SYSTEM`'s content; update any assertion that names a removed shape description, keeping assertions about voice and the absolute rules.

- [ ] **Step 7: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/presentation.py src/pension_chat/agent/nodes.py src/pension_chat/agent/state.py tests/test_templates.py tests/test_answer_style.py
git commit -m "Give the model one shape a turn instead of six

ANSWER_SYSTEM carried every answer shape and twelve absolute rules on every
call, of which one shape was ever needed. The registry keys a slot order on
the rung the evidence supports, prepare_answer renders the computed slots and
tells the model which prose parts to write, and the prompt loses the two
rules that existed only because the model was formatting numbers it should
never have been handling.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 13: Substituting blocks into the stream

**Files:**
- Modify: `src/pension_chat/agent/graph.py` (`run_agent_stream`)
- Test: `tests/test_presentation.py`

**Interfaces:**
- Consumes: `blocks.MarkerSubstituter` (Task 11), `state["answer_blocks"]` (Task 12).
- Produces: no new names. The token stream now carries rendered blocks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_presentation.py`:

```python
def test_a_missing_marker_leaves_its_block_to_be_appended():
    """The recovery path: the member never loses the table because the model
    forgot to ask for it."""
    sub = MarkerSubstituter({"WORKED_STEPS": "TABLE", "BASIS": "BASIS-BLOCK"})
    streamed = sub.feed("Here is your pension. <<<BASIS>>>") + sub.flush()
    assert "BASIS-BLOCK" in streamed
    assert sub.unused() == ["TABLE"]

    recovered = streamed + "\n\n" + "\n\n".join(sub.unused())
    assert "TABLE" in recovered
```

- [ ] **Step 2: Run to verify it passes already**

Run: `uv run pytest tests/test_presentation.py -k missing_marker -v`
Expected: PASS — this documents the contract Task 11 built. The wiring below is what makes the graph use it.

- [ ] **Step 3: Wire the substituter into the stream**

In `src/pension_chat/agent/graph.py`, add the import:

```python
from pension_chat.agent.blocks import MarkerSubstituter
```

Replace the generation loop in `run_agent_stream` with:

```python
    chunks: list[str] = []
    first_token_at: float | None = None
    substituter = MarkerSubstituter(state.get("answer_blocks") or {})
    try:
        async for delta in stream_llm(
            node="generate_answer",
            messages=messages,
            reasoning_effort=settings.reasoning_effort_answer,
        ):
            if first_token_at is None:
                first_token_at = time.perf_counter() - started
            text = substituter.feed(delta)
            if text:
                chunks.append(text)
                yield {"type": "token", "text": text}
    except Exception as exc:
        logger.exception("Answer generation failed")
        yield {
            "type": "error",
            "message": (
                "I found the relevant sections but could not finish writing the "
                "answer. Please try again."
            ),
            "detail": str(exc),
        }
        return

    if tail := substituter.flush():
        chunks.append(tail)
        yield {"type": "token", "text": tail}

    # A block whose marker never arrived is appended rather than lost. The
    # member must never be short a table because the model forgot to ask for it.
    for block in substituter.unused():
        addition = f"\n\n{block}"
        chunks.append(addition)
        yield {"type": "token", "text": addition}

    answer = "".join(chunks)
    state["final_answer"] = answer
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Verify end to end against the live model**

Run: `uv run python scripts/eval_agent.py --category calculation`
Expected: the calculation cases pass and the answers contain a well-formed worked-steps table. If a case fails because the model omitted a marker, confirm the appended-block recovery produced the table anyway.

- [ ] **Step 6: Lint, type-check and commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/graph.py tests/test_presentation.py
git commit -m "Splice the computed blocks into the stream as it passes

Prose still streams token by token; a table appears instantly and whole
where its marker sat. A block whose marker never arrived is appended rather
than lost, because a member being short a table is a worse outcome than a
table in a slightly different place.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 14: The frontend render fixes

**Files:**
- Modify: `frontend/src/components/chat/ChatMessage.tsx`
- Modify: `frontend/src/components/chat/ChatInterface.tsx`

**Interfaces:**
- Consumes: `guardrail_report.verified_citations` from the `done` SSE event.
- Produces: no new names.

**Background.** Three of these four are defects rather than improvements, and none of them depends on any earlier task. This task can be done at any point.

- [ ] **Step 1: Send verified citations to the client**

In `src/pension_chat/api/sessions.py`, wherever the `done` event is assembled (near line 373 and 388), the payload already carries `citations`. Add the verified list beside it:

```python
                    verified_citations=(final_state.get("guardrail_report") or {}).get(
                        "verified_citations", []
                    ),
```

Add `verified_citations: list | None = None` to the response model beside `citations` (near line 111).

- [ ] **Step 2: Give tables a horizontal escape and tune the heading scale**

In `frontend/src/components/chat/ChatMessage.tsx`, replace the `prose` wrapper and its `ReactMarkdown` with:

```tsx
            <div className="prose prose-invert prose-p:leading-relaxed prose-h3:text-sm prose-h3:font-semibold prose-h3:text-neutral-300 prose-h3:mt-4 prose-h3:mb-2 prose-pre:bg-neutral-950 prose-pre:border prose-pre:border-neutral-800 max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // A four-column comparison inside an 80%-wide bubble has
                  // nowhere to go. Give it somewhere.
                  table: ({ node, ...props }) => (
                    <div className="overflow-x-auto -mx-1 px-1">
                      <table {...props} />
                    </div>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
```

- [ ] **Step 3: Rebuild the citation chips from what was verified**

In the same file, replace the citations block with:

```tsx
        {/* Only the provisions the answer actually relied on, and that passed
            verification. Listing every retrieved section implied a reliance the
            answer did not have. */}
        {!isUser && message.verified_citations && message.verified_citations.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 px-1">
            {message.verified_citations.map((section: string) => {
              const detail = (message.citations ?? []).find(
                (c: any) => c.section === section
              );
              return (
                <div
                  key={section}
                  className="flex items-center space-x-1.5 px-3 py-1.5 bg-neutral-900 border border-neutral-800 rounded-lg text-xs text-neutral-400"
                  title={detail?.title ?? ""}
                >
                  <BookOpen size={12} className="text-blue-400" />
                  <span className="font-medium">40 ILCS 5/{section}</span>
                  {detail?.title && (
                    <span className="opacity-60 max-w-[16rem] truncate">
                      {detail.title}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
```

and add `verified_citations?: string[];` to the `ChatMessageProps` message interface. Remove `relevance_score` — it was never populated and a retrieval score is not a confidence figure.

- [ ] **Step 4: Stop rendering half-written tables**

In `frontend/src/components/chat/ChatInterface.tsx`, capture the verified list on `done`:

```tsx
                      verified_citations:
                        (data.verified_citations as string[]) ?? [],
```

and in `ChatMessage.tsx`, render the incomplete tail as plain text while a message is still streaming. Add `isStreaming?: boolean` to the props, and split the content:

```tsx
  // Markdown is re-parsed on every token, so a half-written table renders as
  // literal pipes until its row closes. Everything up to the last blank line is
  // complete enough to parse; the tail after it is shown as plain text and
  // re-renders as markdown the moment the block closes.
  const split = isStreaming ? message.content.lastIndexOf("\n\n") : -1;
  const settled = split > 0 ? message.content.slice(0, split) : message.content;
  const tail = split > 0 ? message.content.slice(split) : "";
```

Render `settled` through `ReactMarkdown` and `tail` in a `<div className="whitespace-pre-wrap">`. Pass `isStreaming` from `ChatInterface.tsx` — it is true for the assistant message currently being written.

- [ ] **Step 5: Build and lint**

```bash
cd frontend
npm run build
npm run lint
```
Expected: build succeeds; lint reports no more than 7 problems.

- [ ] **Step 6: Check it by eye**

Run the app (`uv run uvicorn pension_chat.main:app --reload` and `npm run dev`), ask "what would I get if I retire at 58, at 59 or at 60? put them side by side", and confirm: the table scrolls rather than squashing, no pipes appear mid-stream, and the chips name only the cited provisions.

- [ ] **Step 7: Commit**

```bash
cd ..
git add frontend/src/components/chat/ChatMessage.tsx frontend/src/components/chat/ChatInterface.tsx src/pension_chat/api/sessions.py
git commit -m "Give tables somewhere to go, and stop the chips overclaiming

Three defects rather than three improvements. A four-column comparison was
rendered into a max-w-[80%] bubble with no overflow anywhere. setContent
fires on every token, so a half-written table rendered as literal pipes
until its row closed. And the citation chips were built from every retrieved
section rather than the cited ones, so an answer resting on two provisions
showed six -- while the relevance score they displayed was never in the
payload at all, dead code carrying the worse idea that a retrieval score is
a confidence figure fit to show a member.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

### Task 15: Evals, then flip the flag and delete the regexes

**Files:**
- Create: `evals/routing.yaml`
- Modify: `scripts/eval_agent.py`
- Modify: `evals/gold_end_to_end.yaml`
- Modify: `src/pension_chat/config.py`
- Modify: `src/pension_chat/agent/nodes.py`, `src/pension_chat/agent/guardrails.py`, `tests/test_planning.py`

**Interfaces:**
- Consumes: everything above.
- Produces: eval expectations `in_scope`, `recompute`, `rung`.

- [ ] **Step 1: Expose the new signals to the harness**

In `scripts/eval_agent.py`, add to the dict `run_turn` returns:

```python
        "in_scope": state.get("in_scope"),
        "stale_figures": state.get("stale_figures") or [],
        "answer_rung": state.get("answer_rung"),
```

and add to `check`:

```python
    if "in_scope" in expect and result["in_scope"] != expect["in_scope"]:
        problems.append(
            f"scope was {result['in_scope']}, expected {expect['in_scope']}"
        )

    if expect.get("recompute") and not result["stale_figures"]:
        problems.append("did not treat this as a correction to an earlier figure")

    if expect.get("rung") and result["answer_rung"] != expect["rung"]:
        problems.append(
            f"answered at rung {result['answer_rung']}, expected {expect['rung']}"
        )
```

- [ ] **Step 2: Write the routing eval**

Create `evals/routing.yaml`:

```yaml
# What a message is, decided before anything else runs.
#
# Every case here was measured against the live system and got the wrong
# verdict. Three were refused outright at zero cost, which is the cheapest
# possible way to fail a member.

category: routing

cases:
  - id: R01
    name: The most natural opening sentence a member could type
    critical: true
    turns:
      - q: >-
          I'm starting to think seriously about retiring but I honestly don't
          know where to begin. What should I be thinking about?
        expect:
          # "retiring" does not contain the substring "retire".
          in_scope: true

  - id: R02
    name: A survivor question that uses none of the survivor vocabulary
    critical: true
    turns:
      - q: >-
          My wife is worried about what happens to her if something happens to
          me. What would she get?
        expect:
          in_scope: true
          cites: ["11-145.1|11-134.2"]

  - id: R03
    name: A correction to a figure already worked out
    critical: true
    turns:
      - q: how much pension will i get
        expect:
          calculation: true
      - q: actually sorry it's more like 23 years, i forgot the first two
        expect:
          recompute: true
          calculation: true
          forbids: ["I would need the pension calculation"]

  - id: R04
    name: A comparison asked for in the member's own words
    turns:
      - q: how much pension will i get
        expect:
          calculation: true
      - q: >-
          Ok. Put the retire-now, retire-at-58 and retire-at-60 numbers side by
          side for me so I can show my wife.
        expect:
          scenarios: true
          forbids: ["Not worked out"]

  - id: R05
    name: Half inside the Code and half outside it
    turns:
      - q: Does any of this get taxed, and do I still get health insurance?
        expect:
          in_scope: true
          # The tax and insurance halves are referred, never answered.
          forbids: ["Illinois does not tax", "you will be enrolled"]

  - id: R06
    name: Plainly somebody else's job
    turns:
      - q: write me a poem about my dog
        expect:
          in_scope: false

  - id: R07
    name: A figure resting on an assumption says so
    turns:
      - q: I'm 56 with 21.5 years of service and my best four years averaged $69,956.40. What would I get?
        expect:
          calculation: true
          rung: 2
```

- [ ] **Step 3: Run the eval with the flag off, to establish the baseline**

Run: `uv run python scripts/eval_agent.py --category routing`
Expected: R01, R02, R03, R04 fail. Record the output — this is the before.

- [ ] **Step 4: Run the whole suite with the flag on**

Run: `uv run env PENSION_CHAT_MODEL_TURN_UNDERSTANDING=true python scripts/eval_agent.py`
Expected: routing cases pass; no category's pass rate drops below its flag-off value; the CRITICAL block is empty. If a coverage case regresses, `understand_turn` is deciding an Article it must not — check its output for a coverage claim before changing anything else.

- [ ] **Step 5: Flip the default**

In `src/pension_chat/config.py`:

```python
    model_turn_understanding: bool = True
```

- [ ] **Step 6: Delete the dead regexes**

From `src/pension_chat/agent/nodes.py`, delete `_CALCULATION_SIGNALS`, `_RESTATED_FACT`, `_NUMBER_WORDS`, `_COUNT`, `_OFFSET_PATTERNS`, `_AGE_LIST`, `_AGE_PATTERNS`, `_RETIRE_NOW`, `_PLAUSIBLE_AGE`, `_NOW_CUE`, `_BENEFIT_CUES`, `_corroborated`, `_as_count`, `extract_scenarios`, `_merge_scenarios`, `infer_formula_types`, `_continuing_formula_types`, and the whole `plan_request` function.

From `src/pension_chat/agent/guardrails.py`, delete `_PENSION_TERMS`, `_TIMING_LANGUAGE`, `_CONTINUATION`, `_REFERS_BACK`, and reduce `classify_scope` to the `_OUT_OF_SCOPE` deny plus the empty-message check.

From `src/pension_chat/agent/graph.py`, delete the `plan_request` import, its `add_node` call, and the `planner` branch in `_in_scope` and `build_graph` — `understand_turn` is now the only path.

From `src/pension_chat/config.py`, delete `deterministic_planning` and `reasoning_effort_plan`.

Delete `tests/test_planning.py`'s `_CALCULATION_SIGNALS` worked-example guard and any test that imports a deleted name. It was guarding a fragility that no longer exists; `evals/routing.yaml` asserts the verdicts directly instead. Leave `tests/test_coverage.py` untouched.

- [ ] **Step 7: Run everything**

```bash
uv run pytest tests/ -q
uv run ruff check . && uv run mypy .
uv run python scripts/eval_agent.py
```
Expected: unit tests pass; no lint or type errors; the eval suite's CRITICAL block is empty.

- [ ] **Step 8: Add the two measured conversations as acceptance cases**

Append to `evals/gold_end_to_end.yaml`:

```yaml
  - id: E90
    name: A member planning retirement, in their own words
    critical: true
    turns:
      - q: >-
          I'm starting to think seriously about retiring but I honestly don't
          know where to begin. What should I be thinking about?
        expect:
          in_scope: true
      - q: >-
          Realistically I'd like to stop working sometime in the next couple of
          years. Roughly what would I be looking at?
        expect:
          calculation: true
      - q: >-
          My wife is worried about what happens to her if something happens to
          me. What would she get?
        expect:
          in_scope: true
      - q: >-
          Ok. Put the retire-now, retire-at-58 and retire-at-60 numbers side by
          side for me so I can show my wife.
        expect:
          scenarios: true
          forbids: ["Not worked out"]

  - id: E91
    name: A member being imprecise, and correcting themselves
    critical: true
    turns:
      - q: how much pension will i get
        expect:
          calculation: true
      - q: i'm 56, been there about 21 years, make around 72k
        expect:
          in_scope: true
      - q: actually sorry it's more like 23 years, i forgot the first two
        expect:
          recompute: true
          calculation: true
          forbids: ["I would need the pension calculation"]
```

- [ ] **Step 9: Run the acceptance cases**

Run: `uv run python scripts/eval_agent.py --category end_to_end`
Expected: E90 and E91 pass.

- [ ] **Step 10: Commit**

```bash
git add evals/routing.yaml evals/gold_end_to_end.yaml scripts/eval_agent.py src/pension_chat/config.py src/pension_chat/agent/nodes.py src/pension_chat/agent/guardrails.py src/pension_chat/agent/graph.py tests/test_planning.py
git commit -m "Make the model's reading of a turn the only reading, and delete the rest

The flag comes on and roughly two hundred lines of pattern matching go with
it: the calculation signals, the benefit cues, the age and offset patterns,
the scenario extractor, the continuation and refers-back patterns, and the
vocabulary membership test that refused three of eight turns in a plausible
conversation. test_planning.py's worked-example guard goes too -- it was
guarding a fragility that no longer exists, and evals/routing.yaml asserts
the verdicts directly instead, with the two conversations that started all
of this as the acceptance test.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DGCo5wxmKbdW3XPvBJDTDy"
```

---

## Self-review notes

**Spec coverage.** §1 working sheet → Task 1. §2 turn understanding → Tasks 7, 8, 9 (node responsibilities, fail-open, regex asymmetry, deletions). §3 assumption layer → Tasks 4, 5, 6 (policy, ceiling, materiality range, disclosure guardrail). §4 recompute and convergence → Tasks 2 (never-ask filter), 5 (assumption fill), 9 (staleness routing), 10 (ladder), 12 (partial shape's referral rule). §5 answer step and repair → Task 3, plus Task 12's prompt trim. §6 presentation → Tasks 10, 11, 12, 13, 14. §7 testing → every task's tests plus Task 15. §8 rollout → task order follows the spec's table, with the spec's step 8 (frontend) as Task 14, independent as promised.

**One spec item deliberately deferred, and it should be raised before Task 12 starts.** The spec's §3 promises the materiality *spread* is computed by re-running the interpreter at the `plausible_range` endpoints and reported in the lede ("somewhere between about $31,766 and $32,757"). Task 4 builds `plausible_range` and Task 5 applies the assumption, but no task re-runs the interpreter at the endpoints. Doing it well needs a decision this plan should not make alone: whether to run the DSL twice more on every assumption-backed figure, or once and scale, and whether the spread belongs in `calculation_result` or the sheet. **Add it as a Task 16 after Task 13, once the block renderers exist and the shape of `calculation_result` is settled in practice.** Everything else in §3 — the policy, the ceiling, the disclosure guardrail, the assumptions block — is covered.
