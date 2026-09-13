# What Accumulates — Implementation Plan (Phase 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the system keep what it learns — failures it already detects and throws away, usage the registry already produces and does not count, and evidence that verifiably answered a question before.

**Architecture:** Three independent deliverables, each shipping alone. Nothing here changes what the model believes about the law, and nothing lets an unverified answer teach the system: the guardrail's *pass* is the only label that admits a row, and its *failure* is the only thing that writes a capture.

**Tech Stack:** Python 3.12, `uv`, pytest (`asyncio_mode = "auto"`), Alembic (hand-written migrations, RLS policies included), Postgres, Azure OpenAI, ruff.

**Spec:** `docs/superpowers/specs/2026-09-12-intelligent-agent-design.md` §6

## Global Constraints

- Backend commands under `uv`. Unit tests offline.
- `uv run ruff check .` reports **37 pre-existing errors**. Add none.
- Migrations are **hand-written** and include RLS policies; they derive from
  `db/models.py`, never the reverse.
- **Nothing a member says rewrites a record.** A claim is a report, not a
  correction.
- **No learning changes a figure without a human in the path.**
- Every query path goes through `db/session.py::get_db_session()`;
  `get_admin_session()` is the deliberate escape hatch, not a default.

---

### Task 1: Captured failures become evals

**Files:**
- Create: `src/pension_chat/agent/capture.py`, `tests/test_capture.py`
- Modify: `src/pension_chat/agent/graph.py`, `src/pension_chat/config.py`
- Create: `scripts/promote_captures.py`

**Interfaces:**
- Produces: `capture.record_failure(state, report, session_id) -> Path | None`,
  writing one YAML file per hard verification failure under
  `settings.capture_dir` (default `evals/_captured`). Returns the path, or
  `None` when capture is off or the write failed.
- Consumes: `guardrails.GuardrailReport`, the turn state.

Every hard verification failure is a labelled example of the thing this system
exists to prevent, and today it is logged at WARNING and dropped. The run that
found the `content: null` 400 needed the request dumped by hand; the run that
found the loop citing Article 3 needed a report read by hand. A capture file is
that evidence, written at the moment it exists.

**It must never cost the member their turn.** The failure has already happened,
the fallback is already going out, and an exception here would replace a
degraded answer with none. Wrapped, and the failure logged.

**No names.** The file records `session_id`, the questions, the rejected answer
and the failing checks — not the member's name, account or email. The pilot's
members are synthetic; the habit is not.

- [ ] **Step 1: Write the failing test**

```python
async def test_a_hard_failure_writes_the_evidence(tmp_path): ...
async def test_a_passing_answer_writes_nothing(tmp_path): ...
async def test_the_capture_names_which_check_failed(tmp_path): ...
async def test_a_capture_that_throws_does_not_take_the_turn(tmp_path): ...
async def test_no_member_name_reaches_the_file(tmp_path): ...
```

- [ ] **Steps 2-5:** run (fail), implement, run (pass), commit.
- [ ] **Step 6:** `scripts/promote_captures.py` — print captures, and with
  `--promote <id>` append one to `evals/_captured.yaml` as a case with its
  turns and a `gap:` naming the check it broke. Review stays human.

---

### Task 2: The registry counts what is load-bearing

**Files:**
- Create: `alembic/versions/007_registry_usage.py`
- Modify: `src/pension_chat/db/models.py`, `src/pension_chat/agent/registry.py`,
  `src/pension_chat/api/admin.py`
- Test: `tests/test_registry_usage.py`, `tests/test_registry_alternatives.py`

**Interfaces:**
- Produces: `FormulaRegistry.reuse_count`, `.last_reused_at`,
  `.contested_count`; `registry.note_reuse(row)`, `registry.note_contested(row)`.
- Admin: `POST /admin/formulas/{id}/retire`, `.../suspend`,
  `.../rederive` (with a `feedback` note); `POST .../promote` **removed**.

Repurposed from "what earned approval" to **what is load-bearing**: a reviewer
should see the recipe that has quietly produced 400 figures first, because that
is where an error costs most. `contested_count` comes from `input_overrides`,
which already detects a member disputing a figure and today only mentions it in
prose.

**The freshness bug, fixed here.** `verify_freshness` (`registry.py:171-174`)
returns `{"__unfingerprinted__": "missing"}` for a row with no stored
fingerprint — *before* the stale-marking code. So such a row is never marked
and re-derives on every turn, forever, silently. It is a cache that never hits
and a status that never changes.

- [ ] **Step 1: Write the failing test**

```python
async def test_an_unfingerprinted_row_is_marked_rather_than_re_derived_forever(): ...
async def test_reuse_is_counted_and_dated(): ...
async def test_a_disputed_input_counts_against_the_recipe_that_used_it(): ...
async def test_promote_is_gone(): ...
async def test_retire_drops_a_recipe_out_of_reuse(): ...
async def test_suspend_keeps_it_visible_and_stops_figures(): ...
```

- [ ] **Steps 2-6:** run (fail), migration, implement, run (pass), commit.

---

### Task 3: Verified retrieval memory

**Files:**
- Create: `alembic/versions/008_answer_evidence.py`,
  `src/pension_chat/agent/memory.py`, `tests/test_answer_memory.py`
- Modify: `src/pension_chat/db/models.py`, `src/pension_chat/agent/loop.py`,
  `src/pension_chat/agent/tools.py`, `src/pension_chat/agent/graph.py`

**Interfaces:**
- Produces: `AnswerEvidence(concept, concept_embedding, article, tier,
  sections, verified_at, session_id)`; `memory.remember(state, report)` writing
  **only on a guardrail pass**; `memory.recall(query, article, tier) -> list[str]`.
- `compose` emits two structured fields beside the answer: `concept` (a short
  canonical phrase — "early retirement reduction") and `addressed` (what the
  member asked for and which parts this answer covered).

**It surfaces as enrichment on `search_statute`, never as a filter** —
*"sections that previously answered questions like this: §11-134, §11-216."*
That is the safety property: a hint cannot suppress a fresh search, so a
section that once got lucky cannot entrench itself.

Confirmed coverage determinations ride in the same table under
`concept = "coverage: <description>"`. That one generalises — "employee of a
board of park commissioners" → Article 12 is a statutory test, not a fact about
a person.

**`addressed` is a self-report and proves nothing on its own.** Its value is
that a turn which answered two of three questions says so in a field a test can
read, and it is written *after* the evidence is in, so unlike a goal classified
up front it cannot go stale.

- [ ] **Step 1: Write the failing test**

```python
async def test_only_a_verified_answer_is_remembered(): ...
async def test_a_replaced_answer_teaches_nothing(): ...
async def test_recall_enriches_a_search_without_narrowing_it(): ...
async def test_a_hint_cannot_suppress_a_fresh_search(): ...
async def test_coverage_determinations_ride_along(): ...
```

- [ ] **Steps 2-6:** run (fail), migration, implement, run (pass), commit.

---

### Task 4: Measure

- [ ] Run `routing`, `state`, `coverage` on the loop. Task 3 changes what
      `search_statute` returns, which is the one thing here that can move an
      answer; Tasks 1 and 2 cannot.
- [ ] Record whether recall fired, and on what.

## Done when

- A hard failure leaves a file that reproduces it; a passing answer leaves none.
- An unfingerprinted recipe is marked once rather than re-derived forever.
- `promote` is gone; retire, suspend and re-derive exist.
- A verified answer's sections are recalled as a hint and never as a filter.
- `uv run pytest` passes; ruff no worse than 37.

## Not in this phase

- **Cutover** — Phase 6, with `fund_smoke` and the full-suite comparison.
- **The registry browser UI** — the API lands here; the admin screen does not.
- **Member corrections rewriting records** — explicitly never.
