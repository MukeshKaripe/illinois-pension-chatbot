# Views, and progress that reflects what is happening now

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Put the tables in front of the member as soon as they exist rather
than behind the whole answer, stop the model transcribing numbers into them,
and make a long wait show what is actually happening.

**Architecture:** `compose` already computes every view before generation starts.
They become state (`views`), reach the client as their own SSE event before the
first token, and the table-shaped ones are emitted by marker so the model never
writes their numbers. Progress comes from `get_stream_writer()` inside the loop,
so it reports the step running rather than the one that finished.

**Tech Stack:** Python 3.12, LangGraph 1.2.11, FastAPI SSE, Next.js 16, React 19.

**Spec:** `docs/superpowers/specs/2026-09-16-rag-first-agent-design.md` §9.

## Global Constraints

- **No second model call.** Every view and every progress step is rendered from
  state the turn already holds. §9 says this outright and it is also the whole
  latency argument.
- **The model never writes a number into a view.** Markers, then code fills.
- **A fast turn shows no progress steps.** The member should not be shown
  machinery on a turn that was quick.
- **A block whose marker never arrived is still appended.**
  `MarkerSubstituter.unused()` already does this; nothing here may bypass it.
- Offline-testable. The latency claims are not re-measured here.

## Two things found while reading, which change the shape of this work

**The calculation drawer has been empty since the cutover.** `card.py` builds
`card["blocks"]` from `state["answer_blocks"]`, and `CalculationPanel.tsx`
renders it — but the only writer of `answer_blocks` is `loop.compose`, which
sets it to `{}`. `prepare_answer`, which used to fill it, no longer exists. So
`DRAWER_BLOCKS = ("WORKED_STEPS", "COMPARISON", "ASSUMPTIONS")` resolves to
nothing, every turn. Nobody noticed because the prose still carries the content.

**`render_worked_steps` is dead code on the live path.** `loop.py` imports
`render_assumptions`, `render_branches` and `render_thresholds` and never the
worked steps. It is written and tested and has not run since the cutover.

So §9's "extending `MarkerSubstituter`" is not an extension — the machinery is
intact and unfed. This plan feeds it.

---

### Task 1: The views are state, not only narration

**Files:**
- Modify: `src/pension_chat/agent/loop.py` (`_views`, `compose`)
- Modify: `src/pension_chat/agent/state.py`
- Modify: `src/pension_chat/agent/card.py`
- Test: `tests/test_views.py` (create)

**Interfaces:**
- Produces: `state["views"]: dict[str, str]` — slot name to rendered markdown,
  computed in `compose` before generation.
- `card.py` reads `views` rather than `answer_blocks`.

- [ ] **Step 1: Write the failing test**

```python
"""Every table this turn can show, rendered before a word is written.

They were being computed and handed to the model as prose to paraphrase, and
nothing kept a copy -- so `card["blocks"]` has been empty since the cutover and
the calculation drawer has had three empty panels in it.
"""

from __future__ import annotations

from pension_chat.agent.loop import _views

_RESULT = {
    "retirement_annuity": {
        "section": "11-134",
        "base": {
            "ok": True,
            "annual_amount": "38000.00",
            "monthly_amount": "3166.67",
            "steps": [{"label": "Final average salary", "amount": "70000.00"}],
        },
    }
}


def test_a_turn_that_computed_a_figure_has_worked_steps():
    views = _views({"calculation_result": _RESULT})
    assert "WORKED_STEPS" in views
    assert "70,000" in views["WORKED_STEPS"]


def test_a_turn_with_no_figure_has_no_views():
    assert _views({}) == {}
    assert _views({"calculation_result": {}}) == {}


def test_a_failed_calculation_renders_nothing():
    assert _views({"calculation_result": {"x": {"base": {"ok": False}}}}) == {}


def test_assumptions_are_a_view_even_though_the_model_writes_them_too():
    """The drawer needs the copy; the prose is a separate obligation the
    guardrail enforces."""
    views = _views(
        {
            "calculation_result": _RESULT,
            "assumptions": {
                "withdrawal_date": {
                    "value": "2026-09-16",
                    "source": "assumed",
                    "basis": "taken as today",
                }
            },
        }
    )
    assert "taken as today" in views["ASSUMPTIONS"]


def test_an_empty_render_is_not_recorded_as_a_view():
    """A slot present with empty content is a blank panel in the drawer."""
    views = _views({"calculation_result": _RESULT})
    assert all(text.strip() for text in views.values())
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_views.py -q`
Expected: FAIL, `_views` not defined.

- [ ] **Step 3: Write `_views` and return it from `compose`**

In `loop.py`, beside the other block helpers:

```python
def _views(state: dict[str, Any]) -> dict[str, str]:
    """Every table this turn can show, keyed by slot.

    Rendered here because here is before generation: a view built from state
    that already exists can be sent to the member while the prose is still
    being written, and can be filled into the answer by marker so the model
    never transcribes a number into it.

    Only non-empty renders are recorded. A slot carrying "" is a blank panel
    in the drawer, which reads as something having gone wrong.
    """
    first = _first_figure(state)
    rendered = {
        "WORKED_STEPS": render_worked_steps(first) if first else "",
        "COMPARISON": render_branches(state.get("calculation_result") or {}),
        "ASSUMPTIONS": _assumed(state),
        "THRESHOLDS": render_thresholds((first or {}).get("thresholds")),
    }
    return {slot: text for slot, text in rendered.items() if text.strip()}
```

with `_first_figure(state)` returning the first result entry whose `base.ok`
is true, or `None`.

`compose` returns `"views": _views(state)` beside `answer_blocks`.

Add `views` to `state.py`'s TypedDict and `initial_state()`.

Point `card.py` at it: `computed = state.get("views") or {}`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_views.py tests/test_card.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Views: the tables a turn can show are state, not narration"
```

---

### Task 2: A table is emitted by marker, never transcribed

**Files:**
- Modify: `src/pension_chat/agent/loop.py` (`compose`'s system messages)
- Test: `tests/test_views.py`

**Interfaces:**
- Consumes: `_views` (Task 1).
- Produces: `answer_blocks` carries the table-shaped views; the model is told to
  emit their markers and not to write their figures.

**Why:** §9's rule. Today `_branched` hands the model a rendered table and asks
it to "give both figures" — and a model that transposes Tier 1's amount onto
Tier 2 produces two figures that both trace, so no guardrail catches it. A
marker cannot be transposed.

- [ ] **Step 1: Write the failing test**

```python
"""The model names the view; code fills it.

A block handed over as text to paraphrase is a block whose numbers can be
transposed -- and two figures swapped between two branches both trace to engine
output, so nothing downstream catches it. A marker has no numbers in it.

ASSUMPTIONS is deliberately not a marker: the guardrail requires the *answer*
to disclose an assumption in its own words, and a substituted block is not the
answer saying anything.
"""

from __future__ import annotations

from pension_chat.agent import loop as loop_mod

...


async def test_a_table_is_offered_as_a_marker():
    out = await _composed({"calculation_result": _BRANCHED})
    assert out["answer_blocks"].get("COMPARISON")
    system = _system(out)
    assert any("<<<COMPARISON>>>" in text for text in system)


async def test_the_model_is_told_not_to_write_the_figures():
    out = await _composed({"calculation_result": _BRANCHED})
    system = " ".join(_system(out))
    assert "do not write the figures" in system.lower()


async def test_assumptions_stay_prose():
    """The guardrail checks the answer's own words, and a substituted block is
    not the answer saying anything."""
    out = await _composed({"calculation_result": _FIGURE, "assumptions": _ASSUMED})
    assert "ASSUMPTIONS" not in out["answer_blocks"]
    assert out["views"].get("ASSUMPTIONS"), "the drawer still needs its copy"


async def test_a_turn_with_no_table_registers_no_blocks():
    out = await _composed({})
    assert out["answer_blocks"] == {}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_views.py -q`
Expected: FAIL — `answer_blocks` is `{}`.

- [ ] **Step 3: Register the markers and change the instruction**

In `compose`, define which views are substituted:

```python
#: Views the model emits by marker and code fills. A table's numbers are the
#: thing a model can get wrong in a way nothing catches -- two figures swapped
#: between two branches both trace to engine output -- so the model names the
#: table and never writes it.
#:
#: ASSUMPTIONS is deliberately absent. `guardrails._check_assumptions_disclosed`
#: requires the *answer* to disclose an assumption in its own words, and a
#: block substituted into the stream is not the answer saying anything.
MARKED_VIEWS = ("WORKED_STEPS", "COMPARISON", "THRESHOLDS")
```

`answer_blocks` becomes `{slot: views[slot] for slot in MARKED_VIEWS if slot in views}`.

Replace the `_branched` system message's "Give both figures with the condition
on each" with an instruction to emit `<<<COMPARISON>>>` on its own line and
**not** write the figures, keeping the "do not pick one, do not say you could
not establish the tier" half. Add equivalent instructions for `WORKED_STEPS`
and `THRESHOLDS` where those views exist.

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q`
Expected: PASS. `tests/test_tier_branching.py`'s
`test_compose_hands_the_model_both_figures_and_forbids_picking` is
**repointed**: the figures now reach the member through the marker, so the
assertion moves from "the system message contains 38,000" to "the registered
block contains 38,000 and the model is told to emit the marker". Note the
change in its docstring.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Views: the model names the table, code writes the numbers"
```

---

### Task 3: A view reaches the member before the prose does

**Files:**
- Modify: `src/pension_chat/agent/graph.py` (`run_agent_stream`)
- Modify: `src/pension_chat/sse/__init__.py`
- Modify: `src/pension_chat/api/sessions.py`
- Test: `tests/test_view_events.py` (create)

**Interfaces:**
- Produces: an SSE `view` event, `{"slot": str, "markdown": str}`, emitted after
  the graph and before the first answer token.

**Why:** §9 — "Views render from state that exists before generation, so each is
its own SSE event the moment it is ready — rather than riding on `done` behind
the full generation and verification, which is the current 5–10s lag before the
calculation card and citations appear."

- [ ] **Step 1: Write the failing test**

```python
"""A table exists before the first token, so it is sent before the first token.

Riding on `done` puts every table behind generation *and* verification. The
member waits the length of the whole answer to see a figure that was ready
before it started.
"""


async def test_a_view_is_emitted_before_any_token(monkeypatch):
    events = await _stream_events(...)
    kinds = [e["type"] for e in events]
    assert "view" in kinds
    assert kinds.index("view") < kinds.index("token")


async def test_each_view_is_its_own_event(monkeypatch):
    events = await _stream_events(...)
    slots = [e["slot"] for e in events if e["type"] == "view"]
    assert slots == ["WORKED_STEPS", "COMPARISON"]


async def test_a_turn_with_no_views_emits_none(monkeypatch):
    events = await _stream_events(...)
    assert not [e for e in events if e["type"] == "view"]
```

Drive `run_agent_stream` with the graph and the answer stream both stubbed, as
`tests/test_tier_branching.py` stubs `resolve_recipes`' collaborators.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_view_events.py -q`
Expected: FAIL, no `view` event.

- [ ] **Step 3: Emit them**

In `run_agent_stream`, immediately after the graph completes and before the
`answering` status:

```python
    # Every view was rendered from state that existed before generation began,
    # so there is nothing to wait for. On `done` they sit behind the whole
    # answer and its verification.
    for slot, markdown in (state.get("views") or {}).items():
        yield {"type": "view", "slot": slot, "markdown": markdown}
```

Add `sse_view(slot, markdown)` to `sse/__init__.py` and a branch in
`sessions.py`'s event loop.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_view_events.py tests/test_sse.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Stream: a view is sent when it is ready, not when the answer is"
```

---

### Task 4: Progress that reports the step running, not the one that finished

**Files:**
- Modify: `src/pension_chat/agent/loop.py` (emit as it works)
- Modify: `src/pension_chat/agent/graph.py` (`custom` stream mode)
- Modify: `src/pension_chat/sse/__init__.py`, `src/pension_chat/api/sessions.py`
- Test: `tests/test_progress.py` (create)

**Interfaces:**
- Produces: SSE `progress`, `{"text": str, "at": float}` where `at` is seconds
  since the turn began.

**Why:** §9 — `graph.py`'s stage labels come from LangGraph's `updates` stream,
which fires on node *completion*, "so the longest wait displays the step that
already finished". And the member's own requirement: an answer taking more than
four seconds has to show what it is doing.

`at` is carried so the *client* decides. A fast turn is not one the server can
recognise in advance — it only knows in hindsight — so the server reports
honestly and the client shows nothing until four seconds have passed.

- [ ] **Step 1: Write the failing test**

```python
"""What it is doing, while it is doing it.

The old labels came from LangGraph's `updates` stream, which fires when a node
finishes -- so a turn that spent nine seconds investigating displayed "reading
the Pension Code" at the moment it stopped reading the Pension Code.
"""

from __future__ import annotations

from pension_chat.agent import loop as loop_mod


async def test_a_search_reports_what_it_searched_for(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(loop_mod, "get_stream_writer", lambda: written.append)
    ...
    assert any("annuity reduction" in text for text in written)


async def test_reading_a_section_reports_its_heading(monkeypatch):
    ...
    assert any("11-134" in text for text in written)


async def test_progress_is_never_a_model_call(monkeypatch):
    """The whole point. A second call to describe the first is the thing §9
    forbids and the thing that would make a slow turn slower."""
    ...


def test_a_missing_writer_is_not_an_error(monkeypatch):
    """`get_stream_writer` raises outside a graph run, and `investigate` is
    called directly by every loop test and by the eval harness."""
    monkeypatch.setattr(loop_mod, "get_stream_writer", _raises)
    ...  # investigate completes normally
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_progress.py -q`
Expected: FAIL.

- [ ] **Step 3: Emit from where the work happens**

In `loop.py`:

```python
def _progress(text: str) -> None:
    """Report a step as it starts, best effort.

    `get_stream_writer` raises outside a graph run, and `investigate` is called
    directly by the eval harness and by most of this module's tests. Progress
    is a courtesy; it must never be the reason a turn fails.
    """
    try:
        get_stream_writer()({"progress": text})
    except Exception:  # noqa: BLE001 - a missing writer is not this turn's problem
        pass
```

Call it with the real detail before each tool runs: the query being searched,
the section id being read, the Article being outlined, the benefit being
computed.

In `graph.py`, add `"custom"` to `stream_mode`, handle the mode, and yield
`{"type": "progress", "text": ..., "at": time.perf_counter() - started}`.
Keep the existing `updates` labels as the coarse fallback for nodes that emit
nothing of their own.

Add `sse_progress` and the `sessions.py` branch.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_progress.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Stream: say what is happening, while it is happening"
```

---

### Task 5: The client shows the tables early and the steps late

**Files:**
- Modify: `frontend/src/lib/sse.ts` (if event typing needs it)
- Modify: `frontend/src/components/chat/ChatInterface.tsx`
- Modify: `frontend/src/components/chat/ChatMessage.tsx`
- Create: `frontend/src/components/chat/ProgressSteps.tsx`
- Test: `frontend/src/lib/progress.test.ts` + `frontend/src/lib/progress.ts`

**Interfaces:**
- Consumes: the `view` and `progress` events.
- Produces: `lib/progress.ts::visibleSteps(steps, {after})` — the pure decision
  about what a member sees, so it can be tested without a browser.

- [ ] **Step 1: Write the failing test**

```ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { visibleSteps } from "./progress.ts";

test("a fast turn shows no steps", () => {
  assert.deepEqual(visibleSteps([{ text: "searching", at: 1.2 }]), []);
});

test("a slow turn shows what it is doing", () => {
  const steps = [
    { text: "searching for annuity reduction", at: 1.2 },
    { text: "reading 11-134", at: 5.4 },
  ];
  assert.deepEqual(
    visibleSteps(steps).map((s) => s.text),
    ["searching for annuity reduction", "reading 11-134"],
  );
});

test("crossing four seconds reveals what came before it", () => {
  // The member should see the whole story once they are made to wait for it,
  // not just the part that happened after the threshold.
  const steps = [
    { text: "one", at: 0.5 },
    { text: "two", at: 4.5 },
  ];
  assert.equal(visibleSteps(steps).length, 2);
});

test("the threshold is configurable for tests", () => {
  assert.equal(visibleSteps([{ text: "x", at: 2 }], { after: 1 }).length, 1);
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npm test`
Expected: FAIL, module not found.

- [ ] **Step 3: Write it and wire the UI**

```ts
/** Below this a turn was quick, and showing the machinery is noise. */
export const PROGRESS_AFTER_SECONDS = 4;

export type ProgressStep = { text: string; at: number };

/**
 * What the member sees. Nothing at all unless the turn ran long enough to
 * make them wait -- and then the whole sequence, because the point is to
 * explain the wait rather than to narrate the last four seconds of it.
 */
export const visibleSteps = (
  steps: ProgressStep[],
  { after = PROGRESS_AFTER_SECONDS }: { after?: number } = {},
): ProgressStep[] =>
  steps.some((step) => step.at >= after) ? steps : [];
```

In `ChatInterface.tsx`: collect `progress` events into state, collect `view`
events onto the message, clear progress on the first token. Render
`ProgressSteps` where `agentStatus` renders today, and the views above the
prose in `ChatMessage`.

Follow `frontend/AGENTS.md`: read `node_modules/next/dist/docs/` before writing
Next.js code. Semantic utilities only — a grep of `src/app` and
`src/components/{chat,profile}` for raw palette classes, gradients or blur must
come back empty.

- [ ] **Step 4: Run the tests and the build**

Run: `cd frontend && npm test && npm run build && npm run lint`
Expected: PASS; lint at its existing 2 warnings, 0 errors.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Chat: tables as they arrive, steps only when there was a wait"
```

---

## Verification

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check .` — 37, the pre-existing count, and no more
- [ ] `cd frontend && npm test && npm run build && npm run lint`
- [ ] Open a calculation turn and confirm the drawer's three panels have
      content — the regression this plan found.
- [ ] Update §9 with what shipped, and record that the drawer was empty.

**Not in this plan, deliberately:** the fire-and-forget checkpoint write (§15).
`durability="exit"` persists when the graph exits, which is on the critical
path; `"async"` persists during the following step, but with three nodes and
generation happening outside the graph there may be no following step to hide
the write behind. Which is faster is an in-region measurement, not a reading of
the API, and guessing it would put a latency claim in the spec that nobody
checked.
