# Fact Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make what the member has told us survive the turn it was said in, with its provenance intact, so a conversation can build up to an answer instead of forgetting.

**Architecture:** A new focused module `agent/ledger.py` holds pure functions over a durable, per-subject, provenance-ranked store. `resolve_inputs` writes to it as values resolve — reusing the classification `_input_origins` already computes in code rather than asking the model. `_available_facts`, the single place that decides which facts a turn may use, reads it back with precedence. No node ordering changes, no new model calls, no network in any test.

**Tech Stack:** Python 3.12, LangGraph 1.2.11 (`merge_dicts` reducer over a `TypedDict` blackboard), pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-16-rag-first-agent-design.md` — §4 (Conversation state) and §13 step 1.

## Global Constraints

- Run everything through `uv`: `uv run pytest`, `uv run ruff check .`, `uv run mypy .`.
- Every test in this plan is offline. No test may touch Azure OpenAI, Azure AI Search or Postgres.
- Precedence is **member_stated > record > derived > assumed**, expressed with the existing constants in `agent/worksheet.py` (`SOURCE_STATED`, `SOURCE_RECORD`, `SOURCE_DERIVED`, `SOURCE_ASSUMED`). Do not invent new source names.
- Provenance is **computed, never declared**. Nothing in this plan may add a model-facing field asking the model where a value came from.
- A new state key must be added to `initial_state()` in `agent/state.py`, which is also how `_BLANK_STATE` gets it.
- `uv run ruff check .` and `uv run mypy .` must pass before each commit.

---

### Task 1: The ledger module

**Files:**
- Create: `src/pension_chat/agent/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `SOURCE_STATED`, `SOURCE_RECORD`, `SOURCE_DERIVED`, `SOURCE_ASSUMED` from `pension_chat.agent.worksheet`.
- Produces:
  - `SELF: str` — the subject name for the member themselves, `"me"`.
  - `PRECEDENCE: tuple[str, ...]`
  - `rank(source: str) -> int`
  - `outranks(new: str, old: str) -> bool`
  - `remember(ledger: dict, subject: str, values: dict[str, Any], source: str, turn: int) -> dict` — returns a **ledger-shaped update** (`{subject: {...whole subject dict...}}`) suitable for merging with `merge_dicts`, or `{}` when nothing was recorded.
  - `recall(ledger: dict, subject: str = SELF) -> dict[str, Any]` — flat `name -> value`.
  - `provenance(ledger: dict, subject: str = SELF) -> dict[str, str]` — flat `name -> source`.
  - `stated_only(ledger: dict, subject: str = SELF) -> dict[str, Any]` — flat `name -> value`, restricted to values the member themselves supplied.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ledger.py`:

```python
"""Tests for what the conversation remembers, and how it knows it.

The member's own words are the highest-provenance fact class in this design --
`guardrails` traces figures to "the member's own messages" as a source distinct
from their record -- and they were the only class discarded at the end of every
turn. These tests pin the precedence rules that make keeping them safe.
"""

from __future__ import annotations

from pension_chat.agent.ledger import (
    SELF,
    outranks,
    provenance,
    recall,
    remember,
    stated_only,
)
from pension_chat.agent.state import merge_dicts
from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_DERIVED,
    SOURCE_RECORD,
    SOURCE_STATED,
)


def _apply(ledger: dict, update: dict) -> dict:
    """Apply a ledger update the way the LangGraph reducer would."""
    return merge_dicts(ledger, update)


def test_what_the_member_said_outranks_their_record():
    ledger = _apply({}, remember({}, SELF, {"total_service_years": 21.5}, SOURCE_RECORD, 1))
    ledger = _apply(
        ledger, remember(ledger, SELF, {"total_service_years": 40}, SOURCE_STATED, 2)
    )
    assert recall(ledger)["total_service_years"] == 40
    assert provenance(ledger)["total_service_years"] == SOURCE_STATED


def test_the_record_does_not_overwrite_what_the_member_said():
    """Re-reading the file every turn must not quietly undo their correction."""
    ledger = _apply({}, remember({}, SELF, {"total_service_years": 40}, SOURCE_STATED, 2))
    ledger = _apply(
        ledger, remember(ledger, SELF, {"total_service_years": 21.5}, SOURCE_RECORD, 3)
    )
    assert recall(ledger)["total_service_years"] == 40


def test_a_later_statement_supersedes_an_earlier_one():
    """Equal rank replaces: the member correcting themselves is still them."""
    ledger = _apply({}, remember({}, SELF, {"final_average_salary": 75000}, SOURCE_STATED, 2))
    ledger = _apply(
        ledger, remember(ledger, SELF, {"final_average_salary": 80000}, SOURCE_STATED, 5)
    )
    assert recall(ledger)["final_average_salary"] == 80000
    assert ledger[SELF]["final_average_salary"]["turn"] == 5


def test_an_assumption_never_displaces_anything_better():
    ledger = _apply({}, remember({}, SELF, {"retirement_age": 60}, SOURCE_DERIVED, 1))
    ledger = _apply(ledger, remember(ledger, SELF, {"retirement_age": 67}, SOURCE_ASSUMED, 2))
    assert recall(ledger)["retirement_age"] == 60


def test_subjects_do_not_see_each_others_facts():
    """A comparison must not borrow one subject's salary for another."""
    ledger = _apply({}, remember({}, SELF, {"final_average_salary": 72743.62}, SOURCE_RECORD, 1))
    ledger = _apply(
        ledger,
        remember(ledger, "as_a_firefighter", {"total_service_years": 21.5}, SOURCE_STATED, 2),
    )
    assert "final_average_salary" not in recall(ledger, "as_a_firefighter")
    assert "total_service_years" not in recall(ledger, SELF)


def test_stated_only_keeps_what_is_theirs_and_drops_the_file():
    ledger = _apply({}, remember({}, SELF, {"total_service_years": 40}, SOURCE_STATED, 2))
    ledger = _apply(
        ledger, remember(ledger, SELF, {"final_average_salary": 72743.62}, SOURCE_RECORD, 1)
    )
    assert stated_only(ledger) == {"total_service_years": 40}


def test_a_none_value_is_not_recorded():
    """Extraction returns nulls for what it could not find; those are not facts."""
    assert remember({}, SELF, {"retirement_age": None}, SOURCE_STATED, 1) == {}


def test_recording_nothing_returns_an_empty_update():
    """An empty update must merge to nothing rather than clearing the key."""
    ledger = _apply({}, remember({}, SELF, {"total_service_years": 40}, SOURCE_STATED, 1))
    ledger = _apply(ledger, remember(ledger, SELF, {}, SOURCE_STATED, 2))
    assert recall(ledger)["total_service_years"] == 40


def test_outranks_is_reflexive_for_equal_sources():
    assert outranks(SOURCE_STATED, SOURCE_STATED) is True
    assert outranks(SOURCE_RECORD, SOURCE_STATED) is False
    assert outranks(SOURCE_STATED, SOURCE_RECORD) is True


def test_an_unknown_source_ranks_below_every_known_one():
    """A source this module has not heard of must not displace one it has."""
    ledger = _apply({}, remember({}, SELF, {"tier": 1}, SOURCE_ASSUMED, 1))
    ledger = _apply(ledger, remember(ledger, SELF, {"tier": 2}, "guessed_somewhere", 2))
    assert recall(ledger)["tier"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ledger.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'pension_chat.agent.ledger'`

- [ ] **Step 3: Write the module**

Create `src/pension_chat/agent/ledger.py`:

```python
"""What this conversation has established, and how it knows it.

The member's own words are the highest-provenance fact class in this design.
`guardrails` traces a figure to "the member's own messages" as a source distinct
from their record, and `coverage` treats the record and their word as two
voices precisely so that one can contradict the other. Yet it was the only fact
class thrown away at the end of every turn: `input_sources` is not in
`DURABLE_KEYS`, so a member who gave their service in turn two was asked for it
again in turn four.

What is kept here is the classification `nodes._input_origins` already makes --
in code, at resolution time, deliberately not reported by the model, because
"the calculation card shows these to the member, so a guess here is a false
claim about their own figure".

**Precedence, not overwrite.** A value the member stated outranks one read off
their record, which outranks one derived from another, which outranks one we
filled in. Re-reading the file every turn therefore cannot quietly undo a
correction. Equal rank replaces, because a member restating a value is still the
member speaking and the later statement is the current one.

**Per subject.** Facts are held under the subject they are about, and nothing
reads across. That is the same rule `_available_facts` enforces when it starves
record facts under a supposition, applied to a conversation that can hold more
than one person in view at once.
"""

from __future__ import annotations

from typing import Any

from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_DERIVED,
    SOURCE_RECORD,
    SOURCE_STATED,
)

#: The subject a conversation is about unless it says otherwise.
SELF = "me"

#: Best first. A source not in this tuple ranks below every source in it, so an
#: unrecognised label can never displace a known one.
PRECEDENCE: tuple[str, ...] = (
    SOURCE_STATED,
    SOURCE_RECORD,
    SOURCE_DERIVED,
    SOURCE_ASSUMED,
)


def rank(source: str) -> int:
    """Where ``source`` sits in the precedence order; unknown sorts last."""
    try:
        return PRECEDENCE.index(source)
    except ValueError:
        return len(PRECEDENCE)


def outranks(new: str, old: str) -> bool:
    """Does a ``new``-sourced value replace an ``old``-sourced one?

    Equal rank replaces. The member correcting themselves is still the member,
    and a record re-read this turn is no staler than the one read last turn.
    """
    return rank(new) <= rank(old)


def remember(
    ledger: dict[str, Any] | None,
    subject: str,
    values: dict[str, Any],
    source: str,
    turn: int,
) -> dict[str, Any]:
    """The ledger update recording ``values`` for ``subject``.

    Returns a whole-subject dict because the state reducer merges at the top
    level only: handing it ``{subject: {one_key: ...}}`` would drop every other
    fact that subject holds. An empty dict when nothing was worth recording,
    which merges to nothing rather than clearing the key.
    """
    held = dict((ledger or {}).get(subject) or {})
    kept: dict[str, Any] = {}
    for name, value in (values or {}).items():
        if value is None:
            # Extraction reports what it could not find as null. An absence is
            # not a fact, and recording it would outrank the record.
            continue
        entry = held.get(name)
        if entry is not None and not outranks(source, str(entry.get("source") or "")):
            continue
        kept[name] = {"value": value, "source": source, "turn": turn}
    if not kept:
        return {}
    return {subject: {**held, **kept}}


def recall(ledger: dict[str, Any] | None, subject: str = SELF) -> dict[str, Any]:
    """Flat ``name -> value`` for ``subject``."""
    held = (ledger or {}).get(subject) or {}
    return {name: entry["value"] for name, entry in held.items()}


def provenance(ledger: dict[str, Any] | None, subject: str = SELF) -> dict[str, str]:
    """Flat ``name -> source`` for ``subject``."""
    held = (ledger or {}).get(subject) or {}
    return {name: str(entry.get("source") or "") for name, entry in held.items()}


def stated_only(ledger: dict[str, Any] | None, subject: str = SELF) -> dict[str, Any]:
    """Flat ``name -> value``, restricted to what the member supplied themselves.

    The one thing that survives the record being starved. A figure is computable
    exactly when no input is borrowed from a record filed under another Article
    -- and a value the member typed is not borrowed from any record.
    """
    held = (ledger or {}).get(subject) or {}
    return {
        name: entry["value"]
        for name, entry in held.items()
        if entry.get("source") == SOURCE_STATED
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ledger.py -v`
Expected: 10 passed

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check . && uv run mypy .`
Expected: no new errors from `ledger.py` or `test_ledger.py`

- [ ] **Step 6: Commit**

```bash
git add src/pension_chat/agent/ledger.py tests/test_ledger.py
git commit -m "Ledger: what the member said, kept with how we know it"
```

---

### Task 2: Make the ledger durable

**Files:**
- Modify: `src/pension_chat/agent/state.py` — `PensionAgentState` (durable section), `DURABLE_KEYS`, `initial_state()`
- Test: `tests/test_state_keys.py`

**Interfaces:**
- Consumes: `merge_dicts` from `agent.state`; nothing from Task 1 at runtime.
- Produces: state key `fact_ledger: Annotated[dict[str, Any], merge_dicts]`, present in `DURABLE_KEYS` and defaulted to `{}` by `initial_state()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state_keys.py`:

```python
def test_the_fact_ledger_survives_a_turn():
    """The whole point. A value the member gave in turn two is still theirs in
    turn four, so the assistant does not ask again."""
    from pension_chat.agent.state import DURABLE_KEYS, PER_TURN_KEYS, turn_input

    assert "fact_ledger" in DURABLE_KEYS
    assert "fact_ledger" not in PER_TURN_KEYS
    assert "fact_ledger" not in turn_input("next question", "session-1")


def test_the_fact_ledger_starts_empty():
    from pension_chat.agent.state import initial_state

    assert initial_state("q", "session-1")["fact_ledger"] == {}


def test_the_fact_ledger_merges_rather_than_replacing():
    """Two subjects written on different turns must both survive."""
    from pension_chat.agent.state import merge_dicts

    first = {"me": {"total_service_years": {"value": 40, "source": "stated", "turn": 1}}}
    second = {"as_a_firefighter": {"tier": {"value": 1, "source": "stated", "turn": 2}}}
    merged = merge_dicts(first, second)
    assert set(merged) == {"me", "as_a_firefighter"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_state_keys.py -k fact_ledger -v`
Expected: FAIL — `assert 'fact_ledger' in DURABLE_KEYS`, and `KeyError: 'fact_ledger'`

- [ ] **Step 3: Add the state key**

In `src/pension_chat/agent/state.py`, in the durable block of `PensionAgentState` — immediately after the `cited_sections` declaration — add:

```python
    # What this conversation has established about each subject, and how it
    # knows it: {subject: {name: {value, source, turn}}}. Durable because the
    # member's own words are the best-provenance fact class in this design and
    # were the only one discarded every turn -- so a member who gave their
    # service in turn two was asked for it again in turn four. See
    # `agent/ledger.py` for the precedence rules.
    fact_ledger: Annotated[dict[str, Any], merge_dicts]
```

In the same file, add `"fact_ledger",` to the `DURABLE_KEYS` frozenset, immediately after `"cited_sections",`.

In `initial_state()`, add `"fact_ledger": {},` immediately after the `"cited_sections": {},` entry.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_state_keys.py -v`
Expected: all pass, including the three new ones

- [ ] **Step 5: Run the whole offline suite for regressions**

Run: `uv run pytest tests/ -q`
Expected: no new failures. `test_state_keys.py` asserts over `DURABLE_KEYS` and `PER_TURN_KEYS` wholesale in places — if an existing test enumerates the durable set exactly, add `fact_ledger` to its expected set rather than removing the assertion.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/state.py tests/test_state_keys.py
git commit -m "State: the fact ledger is durable, like the answers that rest on it"
```

---

### Task 3: Record what resolves into the ledger

**Files:**
- Modify: `src/pension_chat/agent/nodes.py` — the return block of `resolve_inputs()`
- Test: `tests/test_ledger_writes.py` (create)

**Interfaces:**
- Consumes: `remember`, `SELF` from `agent.ledger`; `_input_origins()` as it already exists.
- Produces: `resolve_inputs()` returns an additional key `"fact_ledger"` holding a ledger update for subject `SELF`, recording every resolved input under the source `_input_origins` computed for it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ledger_writes.py`:

```python
"""The ledger is written from the classification the code already makes.

`_input_origins` decides where each resolved value came from, at resolution
time, in code. This turns that per-turn dict into something the next turn can
read, without asking the model anything.
"""

from __future__ import annotations

from pension_chat.agent.ledger import SELF, provenance, recall
from pension_chat.agent.nodes import _ledger_update
from pension_chat.agent.worksheet import SOURCE_RECORD, SOURCE_STATED


def test_a_stated_value_is_recorded_as_stated():
    update = _ledger_update(
        resolved={"total_service_years": 40},
        origins={"total_service_years": SOURCE_STATED},
        turn=2,
    )
    assert recall(update)["total_service_years"] == 40
    assert provenance(update)["total_service_years"] == SOURCE_STATED


def test_a_record_value_is_recorded_as_record():
    update = _ledger_update(
        resolved={"final_average_salary": 72743.62},
        origins={"final_average_salary": SOURCE_RECORD},
        turn=1,
    )
    assert provenance(update)["final_average_salary"] == SOURCE_RECORD


def test_a_value_with_no_recorded_origin_is_not_written():
    """Every resolved input has an origin. One that does not is a bug upstream,
    and guessing a source here would put a false claim on the member's card."""
    update = _ledger_update(
        resolved={"mystery": 3}, origins={}, turn=1
    )
    assert update == {}


def test_nothing_resolved_writes_nothing():
    assert _ledger_update(resolved={}, origins={}, turn=1) == {}


def test_the_update_is_keyed_by_subject():
    update = _ledger_update(
        resolved={"tier": 1}, origins={"tier": SOURCE_RECORD}, turn=1
    )
    assert set(update) == {SELF}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ledger_writes.py -v`
Expected: `ImportError: cannot import name '_ledger_update' from 'pension_chat.agent.nodes'`

- [ ] **Step 3: Add the helper and wire it in**

In `src/pension_chat/agent/nodes.py`, add this function immediately after `_input_origins()`:

```python
def _ledger_update(
    *,
    resolved: dict[str, Any],
    origins: dict[str, str],
    turn: int,
) -> dict[str, Any]:
    """Turn this turn's resolution into something the next turn can read.

    Grouped by source so each group is recorded at its own rank: a value the
    member stated must not be written at the record's rank simply because both
    resolved on the same turn. A resolved input with no recorded origin is
    skipped rather than guessed -- the calculation card shows these, so a guess
    here is a false claim about the member's own figure.
    """
    by_source: dict[str, dict[str, Any]] = {}
    for name, value in resolved.items():
        source = origins.get(name)
        if not source:
            continue
        by_source.setdefault(source, {})[name] = value

    update: dict[str, Any] = {}
    for source, values in by_source.items():
        written = ledger.remember(update, ledger.SELF, values, source, turn)
        if written:
            update = {**update, **written}
    return update
```

Add the import near the other `pension_chat.agent` imports at the top of `nodes.py`:

```python
from pension_chat.agent import ledger
```

Then in `resolve_inputs()`, replace the return block's `"input_sources"` entry so the origins are computed once and used twice:

```python
    origins = _input_origins(
        resolved=resolved,
        taken=taken,
        assumed=assumed,
        sheet=sheet,
        question=state["question"],
        stand_in=_stand_ins_allowed(state),
    )

    return {
        "resolved_inputs": resolved,
        "input_sources": origins,
        "fact_ledger": _ledger_update(
            resolved=resolved,
            origins=origins,
            turn=int(state.get("turn_index") or 0),
        ),
        # An input the sheet holds is not missing, and an input it holds but
        # cannot use under this premise is not missing either -- it is withheld,
        # and saying so is the difference between "tell me your age" and "your
        # file has your age, but I am not using it while we talk about Article 12".
        "missing_inputs": _dedupe_missing_inputs(still_missing),
        "input_overrides": overrides,
        **sheet.to_state_update(),
        "timings": {"resolve_inputs": time.perf_counter() - started},
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ledger_writes.py tests/test_input_origins.py -v`
Expected: all pass. `test_input_origins.py` must still pass unchanged — the refactor only hoists the call.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/nodes.py tests/test_ledger_writes.py
git commit -m "Ledger: write it from the origins the code already computes"
```

---

### Task 4: Read the ledger where facts are decided

**Files:**
- Modify: `src/pension_chat/agent/nodes.py` — `_available_facts()`
- Test: `tests/test_available_facts.py` (create)

**Interfaces:**
- Consumes: `recall`, `stated_only`, `SELF` from `agent.ledger`; `_record_facts_usable()` unchanged.
- Produces: `_available_facts(state)` overlays ledger values onto `member_facts` by precedence, and under a starved record returns the member's **own stated values** rather than nothing.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_available_facts.py`:

```python
"""Which facts a turn may use, now that the conversation remembers some.

`_available_facts` is the single place this is decided -- as a calculation
input, as a fact an applicability predicate is tested against, as a figure the
answer may quote, and as a source verification will accept -- so the ledger has
to arrive here or it arrives four times.
"""

from __future__ import annotations

from pension_chat.agent.nodes import _available_facts


def _state(**over):
    base = {
        "member_facts": {"total_service_years": 21.5, "final_average_salary": 72743.62},
        "fact_ledger": {},
        "coverage": {"article": "11", "basis": "record"},
        "employee_record": {"article_number": 11},
        "hypothetical_frame": None,
    }
    base.update(over)
    return base


def test_what_the_member_said_beats_the_record():
    state = _state(
        fact_ledger={"me": {"total_service_years": {"value": 40, "source": "stated", "turn": 2}}}
    )
    facts = _available_facts(state)
    assert facts["total_service_years"] == 40
    assert facts["final_average_salary"] == 72743.62


def test_the_record_still_supplies_what_they_did_not_say():
    state = _state(fact_ledger={"me": {}})
    assert _available_facts(state)["final_average_salary"] == 72743.62


def test_a_ledger_value_from_the_record_does_not_shadow_a_fresher_record():
    """member_facts is rebuilt from the file every turn; a ledger entry at the
    same rank must not pin a stale copy over it."""
    state = _state(
        member_facts={"total_service_years": 22.5},
        fact_ledger={
            "me": {"total_service_years": {"value": 21.5, "source": "record", "turn": 1}}
        },
    )
    assert _available_facts(state)["total_service_years"] == 22.5


def test_a_starved_record_still_leaves_the_member_their_own_words():
    """Under a supposition the file is not usable -- but what they typed is
    theirs, not the file's, and is the one way to a figure."""
    state = _state(
        hypothetical_frame={"article": "12"},
        coverage={"article": "12", "basis": "hypothetical"},
        fact_ledger={
            "me": {
                "total_service_years": {"value": 25, "source": "stated", "turn": 2},
                "final_average_salary": {"value": 72743.62, "source": "record", "turn": 1},
            }
        },
    )
    facts = _available_facts(state)
    assert facts == {"total_service_years": 25}


def test_a_starved_record_with_nothing_stated_is_still_empty():
    state = _state(
        hypothetical_frame={"article": "12"},
        coverage={"article": "12", "basis": "hypothetical"},
        fact_ledger={
            "me": {"final_average_salary": {"value": 72743.62, "source": "record", "turn": 1}}
        },
    )
    assert _available_facts(state) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_available_facts.py -v`
Expected: FAIL — `test_what_the_member_said_beats_the_record` returns 21.5, and `test_a_starved_record_still_leaves_the_member_their_own_words` returns `{}`

- [ ] **Step 3: Rewrite `_available_facts`**

In `src/pension_chat/agent/nodes.py`, replace the body of `_available_facts()`:

```python
def _available_facts(state: PensionAgentState) -> dict:
    """The member facts this turn is allowed to use.

    Empty of *record* facts when the conversation is reading an Article the
    member's record is not filed under. Their salary and service are
    administered by the fund named on that record, so under another Article they
    are not merely unhelpful -- they are the wrong basis, and every use of them
    is wrong in the same way: as a calculation input, as a fact an applicability
    predicate is tested against, as a figure the answer may quote, and as a
    source verification will accept. Single-sourced here so those four cannot
    drift apart.

    What survives the starving is what the member said themselves. A figure is
    computable exactly when no input is borrowed from a record filed under
    another Article, and a value they typed is borrowed from no record at all --
    which is why "if I were a park employee with 25 years and $70,000" has
    always been answerable where the bare supposition is not.

    The ledger is overlaid rather than consulted first: `member_facts` is
    rebuilt from the file every turn, so a ledger entry at the record's own rank
    must not pin a stale copy over a fresher read.
    """
    if not _record_facts_usable(state):
        return dict(ledger.stated_only(state.get("fact_ledger") or {}, ledger.SELF))
    facts = dict(state.get("member_facts") or {})
    held = state.get("fact_ledger") or {}
    sources = ledger.provenance(held, ledger.SELF)
    for name, value in ledger.recall(held, ledger.SELF).items():
        if name in facts and not ledger.outranks(
            sources.get(name, ""), SOURCE_RECORD
        ):
            continue
        facts[name] = value
    return facts
```

Ensure `SOURCE_RECORD` is imported in `nodes.py` — it already imports from `agent.worksheet`; add it to that import list if absent.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_available_facts.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the suites that depend on this behaviour**

Run: `uv run pytest tests/test_facts.py tests/test_coverage.py tests/test_loop.py tests/test_calculation.py tests/test_assumptions.py -q`
Expected: no new failures. `test_coverage.py` holds the pair repointed at `loop._context` that assert a supposition is not handed the member's own age and service — those must still pass, because an empty ledger leaves `stated_only` empty and the behaviour unchanged.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/nodes.py tests/test_available_facts.py
git commit -m "Facts: a supposition starves the record, not the member's own words"
```

---

### Task 5: Provenance that remembers which turn they spoke in

**Files:**
- Modify: `src/pension_chat/agent/nodes.py` — `_input_origins()`
- Test: `tests/test_input_origins.py`

**Interfaces:**
- Consumes: `provenance`, `SELF` from `agent.ledger`.
- Produces: `_input_origins()` gains a keyword-only parameter `held: dict[str, str] | None = None` — the ledger's recorded provenance for this subject — consulted before falling back to the current-message test.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_input_origins.py`:

```python
def test_a_value_stated_three_turns_ago_is_still_stated():
    """`_stated_in` only ever looked at the current message, so a figure the
    member gave earlier came back labelled `derived` -- and the card told them
    the assistant had worked out a number they had typed themselves."""
    from pension_chat.agent.nodes import _input_origins
    from pension_chat.agent.worksheet import SOURCE_STATED, WorkSheet

    origins = _input_origins(
        resolved={"total_service_years": 40},
        taken={"total_service_years": 40},
        assumed={},
        sheet=WorkSheet(),
        question="so what would that come to?",
        held={"total_service_years": SOURCE_STATED},
    )
    assert origins["total_service_years"] == SOURCE_STATED


def test_the_current_message_still_wins_when_the_ledger_is_silent():
    from pension_chat.agent.nodes import _input_origins
    from pension_chat.agent.worksheet import SOURCE_STATED, WorkSheet

    origins = _input_origins(
        resolved={"final_average_salary": 75000},
        taken={"final_average_salary": 75000},
        assumed={},
        sheet=WorkSheet(),
        question="my average salary is 75000",
        held={},
    )
    assert origins["final_average_salary"] == SOURCE_STATED


def test_an_assumption_this_turn_is_not_promoted_by_an_older_statement():
    """The assumption branch runs first and must keep running first: a value we
    filled in this turn is ours, whatever they said about it before."""
    from pension_chat.agent.nodes import _input_origins
    from pension_chat.agent.worksheet import SOURCE_ASSUMED, SOURCE_STATED, WorkSheet

    origins = _input_origins(
        resolved={"retirement_age": 67},
        taken={},
        assumed={"retirement_age": 67},
        sheet=WorkSheet(),
        question="what would I get?",
        held={"retirement_age": SOURCE_STATED},
    )
    assert origins["retirement_age"] == SOURCE_ASSUMED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_input_origins.py -v`
Expected: FAIL — `_input_origins() got an unexpected keyword argument 'held'`

- [ ] **Step 3: Teach `_input_origins` to consult the ledger**

In `src/pension_chat/agent/nodes.py`, change the signature and the `taken` branch of `_input_origins()`:

```python
def _input_origins(
    *,
    resolved: dict[str, Any],
    taken: dict[str, Any],
    assumed: dict[str, Any],
    sheet: WorkSheet,
    question: str,
    stand_in: bool = False,
    held: dict[str, str] | None = None,
) -> dict[str, str]:
```

and inside the loop, replace the `elif name in taken:` branch with:

```python
        elif name in taken:
            # The ledger first: `_stated_in` only ever saw the current message,
            # so a figure the member gave three turns ago came back `derived`
            # and the card told them the assistant had worked out a number they
            # had typed themselves. A value they stated stays stated until they
            # supersede it.
            recorded = (held or {}).get(name)
            if recorded == SOURCE_STATED:
                origins[name] = SOURCE_STATED
            else:
                origins[name] = (
                    SOURCE_STATED if _stated_in(taken[name], question) else SOURCE_DERIVED
                )
```

Then, at the call site inside `resolve_inputs()`, pass the recorded provenance:

```python
    origins = _input_origins(
        resolved=resolved,
        taken=taken,
        assumed=assumed,
        sheet=sheet,
        question=state["question"],
        stand_in=_stand_ins_allowed(state),
        held=ledger.provenance(state.get("fact_ledger") or {}, ledger.SELF),
    )
```

Ensure `SOURCE_DERIVED` and `SOURCE_STATED` are imported in `nodes.py`; both are already used by this function.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_input_origins.py tests/test_ledger_writes.py tests/test_card.py -v`
Expected: all pass. `test_card.py` reads `input_sources` to build the calculation card, so it is the check that the member-facing labels did not change shape.

- [ ] **Step 5: Run the whole offline suite**

Run: `uv run pytest tests/ -q`
Expected: no new failures.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check . && uv run mypy .
git add src/pension_chat/agent/nodes.py tests/test_input_origins.py
git commit -m "Origins: a value they stated stays stated, not just this turn"
```

---

## Self-Review

**Spec coverage.** This plan implements §13 step 1 and the parts of §4 that do not require multi-subject comparison. The ledger shape is keyed by subject from the start (Task 1's `test_subjects_do_not_see_each_others_facts`), so §13 step 8 extends it rather than reshaping it. `statutory_parameter` as a fourth source is §13 step 6 and is deliberately absent — `PRECEDENCE` will gain it there, and `rank()` already sorts an unknown source last so nothing breaks in between.

**Not in this plan, and where it goes:** subject-scoped retrieval and the conversational query rewrite (§5) are step 2, and get their own plan; per-claim and incremental verification (§8) is step 3; the closure and the conditional cap (§6) are steps 4–5 and must not ship before the verification plan, per §13.

**Deferred with the seam named.** `_ledger_update` records every resolved input, including record-sourced ones. That is more than the minimum — the record is re-read every turn — but it is what makes `provenance()` able to answer "where did this come from?" for `reflect`-style questions later, and Task 4's third test pins the rule that keeps a stale record copy from shadowing a fresh read.

**One risk worth stating.** Task 4 changes behaviour under a supposition: today a starved record yields nothing, and after this it yields the member's own stated values. That is what the design intends and what `compute_benefit`'s `stand_in_facts` path already tries to achieve by a narrower route — but it widens what a hypothetical turn can compute from, so `tests/test_coverage.py`'s repointed pair and `evals/gold_hypothetical.yaml` are the things to watch. The eval suite needs network and is not part of these tasks; run it before merging.
