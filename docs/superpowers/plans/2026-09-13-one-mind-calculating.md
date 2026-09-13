# One Mind, Calculating — Implementation Plan (Phase 3b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the loop the deterministic calculation chain as a tool, so a figure question is answered by the same mind that read the statute — and the loop becomes comparable to the pipeline instead of strictly worse on a third of the suite.

**Architecture:** No new calculation code. `resolve_recipes`, `resolve_inputs`, `validate_recipes` and `execute_calculations` each take a state and return an update; `compute_benefit` runs that chain in order over a state slice and returns what it produced. The model chooses *when* to compute and with what premise; every number still comes out of the DSL interpreter under the validator, exactly as it does in the pipeline.

**Tech Stack:** Python 3.12, `uv`, pytest (`asyncio_mode = "auto"`), Azure OpenAI `gpt-5.6-luna-1`, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-12-intelligent-agent-design.md` (§2 tool surface; §4 stand-ins)

## Global Constraints

- Backend commands under `uv`. Unit tests offline: the chain is faked at the four node functions.
- `uv run ruff check .` reports **37 pre-existing errors**. Add none.
- **The pipeline is the default and must not change.**
- **`compute_benefit` is the only path to a number**, and it does not compute one — it runs the same four nodes in the same order. Nothing here reimplements arithmetic, applicability or validation.
- Every figure lands inside `calculation_result`, which `guardrails._collect_allowed_numbers` walks.

### Why 3a could not merge alone, and this must merge with it

Measured: the loop scores **2/7 on `routing`** against the pipeline's 7/7, and
five of those losses report *"no calculation ran"* (L08, L10, R03, R04, R07 —
R03 is CRITICAL). A loop that cannot compute is strictly worse than the
pipeline on a third of the suite, so 3a was a development checkpoint, not a
shippable increment. **3a and 3b merge together or not at all.**

### What this phase targets

| Case | Pipeline | Why it needs 3b |
|---|---|---|
| R03 (CRITICAL) | pass | "a correction to a figure already worked out" — no figure to correct |
| R04 | pass | a comparison of retirement dates |
| R07 | pass | a figure resting on an assumption, at rung 2 |
| L10 | pass | the same calculation twice, reusing the registry recipe |
| L08 | fail | a figure under a supposed premise — needs coverage *and* stand-ins |
| L01, L03 | L03 passes | projection reached through the loop rather than the pipeline |

---

### Task 1: `compute_benefit`

**Files:**
- Modify: `src/pension_chat/agent/tools.py`
- Test: `tests/test_tools_calculation.py` (create)

**Interfaces:**
- Consumes: `nodes.resolve_recipes`, `nodes.resolve_inputs`, `nodes.validate_recipes`, `nodes.execute_calculations`.
- Produces: a `compute_benefit` schema and handler taking `benefit` (one of `facts.BENEFIT_TYPES`), optional `retirement_age`, optional `years_from_now`, optional `stated` (a map of input name to value the member supplied). Returns the figure and its worked basis as text, plus the full state update.

The chain is run in order and each update merged into the slice before the next
node sees it, because that ordering is a correctness constraint: `resolve_inputs`
needs the recipe to know which inputs exist, and `execute_calculations` needs the
validator's verdict.

Three results, all of them answers:

- a figure, with the section, the inputs and their origins;
- **missing inputs**, named — which is what lets the model ask for one thing
  rather than a form, or say what it could not work out;
- **blocked**, with the reason (`execution_policy_no_execute`,
  `applicability_not_satisfied`), because "the provision does not reach you" is
  an answer a member needs.

- [ ] **Step 1: Write the failing test**

`tests/test_tools_calculation.py`, faking the four nodes:

```python
async def test_a_figure_comes_back_with_its_section_and_inputs(monkeypatch): ...
async def test_the_chain_runs_in_order_each_seeing_the_last(monkeypatch): ...
    # resolve_inputs must see the recipes resolve_recipes wrote
async def test_missing_inputs_are_named_not_swallowed(monkeypatch): ...
async def test_a_blocked_execution_says_why(monkeypatch): ...
    # applicability_not_satisfied is an answer, not a failure
async def test_a_retirement_age_becomes_a_scenario(monkeypatch): ...
async def test_an_unknown_benefit_is_reported_not_raised(): ...
async def test_the_state_update_carries_calculation_result(monkeypatch): ...
    # the key the guardrail and the card read
```

- [ ] **Steps 2-5:** run (fail), implement, run (pass), commit.

---

### Task 2: `check_coverage`

**Files:**
- Modify: `src/pension_chat/agent/tools.py`
- Test: `tests/test_tools_coverage.py` (create)

**Interfaces:**
- Consumes: `coverage.retrieve_coverage`, `coverage.assess`, `coverage.adjudicate`, `coverage.classify_self_reference` — unchanged.
- Produces: `check_coverage(description, act)` returning the verdict, its `basis`, the sections behind it, and whether it conflicts with the record. `act` is supplied by the **model** (`asserted` / `stipulated` / `doubted`), per spec §2.1 — which is what makes `attributed` and `superseded` addable later without a regex.

`adjudicate()` still decides in pure Python, and `guardrails._verify_coverage`
still polices what the answer may claim. The tool earns its calls from the
consequence of not calling it: an answer asserting an Article with no `basis` in
state is rejected.

L08 is the case: the frame is created with `article: None` today, so retrieval
stays on Article 11 and answers a question about somebody else's law with the
member's own.

- [ ] **Steps 1-5:** as above.

---

### Task 3: Stand-in inputs

**Files:**
- Modify: `src/pension_chat/agent/tools.py`, `src/pension_chat/agent/nodes.py` (`_input_origins`)
- Test: `tests/test_input_origins.py`

Spec §4: under an explicit hypothetical, the member's own salary and service may
be used as **clearly-labelled stand-ins**, so the figure is real and comparable —
`origin: stand_in`, named in the answer and on the card. Deferred from Phase 2a
because it needs coverage to resolve a described job to an Article, which Task 2
supplies.

- [ ] **Steps 1-5:** as above.

---

### Task 4: Measure, and decide about merging

- [ ] **Step 1: Run both modes over the categories 3b moves**

**Keep the lid open.** `--category routing`, `conversational`, `no_ask`,
`goal_completion`, `calculation`, `state`.

- [ ] **Step 2: Read the reports, not the verdicts**

- **R03 is CRITICAL and must pass**, and a failed critical is re-run before it
  is believed (`baselines/README.md`).
- **L10 must reuse**, not re-derive: `reuse: true` comes from the registry, and
  a loop that derives every time is spending a derivation per turn.
- **Token cost** is recorded. The loop ran 92k–106k on a two-turn case against
  the pipeline's ~12.9k per turn; 3b adds derivation calls on top. This gates
  nothing but informs the cutover.

- [ ] **Step 3: Decide**

If routing and conversational reach the pipeline's rates, merge 3a and 3b
together. If they do not, record what failed and why, and do not merge — the
pipeline stays the default until the criteria in `baselines/README.md` are met.

---

## Done when

- `routing` and `conversational` hold the pipeline's rates, R03 included.
- A figure from the loop carries its section, inputs and origins, and lands inside `calculation_result`.
- `uv run pytest` passes; ruff no worse than 37.
- The merge decision is recorded either way.

## Not in this phase

- **Deleting the CONTEXT blocks** — Phase 4, each gated on its eval.
- **Cutover** — Phase 6.
- **R02 / L05 survivor retrieval** — fails on both architectures; a retrieval-quality problem neither phase addresses.
