# Consequences — Implementation Plan (Phase 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the coverage determination have the same consequences on the loop that it has on the pipeline, then delete the CONTEXT blocks that were substituting for those consequences — each deletion gated on a case that still passes without it.

**Architecture:** No new determination. `check_coverage` already returns the right verdict on every measured case; what is missing is what the pipeline's `assess_coverage` *does* with one — write a clarification, store the open question, and starve the record while the question is live. Binding the member's reply stays deterministic, as it is in the pipeline.

**Tech Stack:** Python 3.12, `uv`, pytest (`asyncio_mode = "auto"`), Azure OpenAI `gpt-5.6-luna-1`, ruff.

**Spec:** `docs/superpowers/specs/2026-09-12-intelligent-agent-design.md` (§5 the deletion rule; §4 stand-ins)

## Global Constraints

- Backend commands under `uv`. Unit tests offline.
- `uv run ruff check .` reports **37 pre-existing errors**. Add none.
- **The pipeline's `coverage` suite must stay 8/8**, G31/G32/G35 being CRITICAL. A change that harms it is reverted, not argued with.
- **No CONTEXT block is deleted until the behaviour it enforces passes without it**, measured, on both agents.
- A figure still comes only from the DSL interpreter under the validator.

---

## What the measurement changed

Run on 2026-09-13, `coverage` category, both agents:

| | pipeline | loop |
|---|---|---|
| G31 *a description pointing away from the record* | PASS (CRITICAL) | **FAIL** |
| G32 *the member answers and it sticks* | PASS (CRITICAL) | **FAIL** |
| G33 *asking about an Article is not answering about one* | PASS | **FAIL** |
| G34 *quoting the assistant's own words back* | PASS | **FAIL** |
| G35 *a description that agrees with the record* | PASS (CRITICAL) | PASS |
| G36 *the member says they are unsure* | PASS | **FAIL** |
| G37, S4 | PASS | PASS |

**`check_coverage` is not the problem.** It ran on every failing case and
returned the correct verdict each time — `conflict: True`, `why: elsewhere` or
`why: doubted`. The loop then computed a figure and answered anyway.

Three things the pipeline does with that verdict and the loop does not:

1. **Writes a `clarification`.** The eval's `asks` check reads exactly that key,
   and every loop failure includes *"answered where it should have asked"*.
2. **Stores an `open_question`**, so the next turn can bind the member's reply.
   G32 turn 2 is the case this exists for: the member restated their coverage in
   the statute's own words and the loop had nothing to bind it to, so the
   verdict stayed `record`/Article 11.
3. **Starves the record while the question is live** — and this one is the
   finding. It does *not*, quite. `_coverage_elsewhere` is false for a
   `record` basis, so nothing stops the calculation; the pipeline passes G31
   because `understand_turn` happened to classify "how is my pension
   calculated?" as wanting no figure. **That is luck, not a mechanism**, and the
   loop's `compute_benefit` walked straight through the gap and quoted
   $32,126.78 on a question about which fund the member is even in.

So the spec's verdict on the `described_as` + `BASIS_RECORD` block — "its
safety half is covered" — is wrong, and was wrong for the pipeline too. The
safety half was never covered by anything except an intent classifier's good
mood. Task 1 builds it; only then is any block safe to delete.

---

### Task 1: An unsettled Article starves the figure

**Files:**
- Modify: `src/pension_chat/agent/nodes.py` (`_coverage_unresolved`, `_record_facts_usable`)
- Test: `tests/test_coverage_node.py`

**Interfaces:**
- Produces: `nodes._coverage_unresolved(state) -> bool`, true when the coverage
  verdict carries `conflict: True`.
- Consumes: nothing new. `_record_facts_usable` already gates
  `_available_facts`, the age recompute, the sheet's `record_usable`, and the
  threshold pricing, so one predicate reaches all four.

`conflict: True` means exactly "the routing question is open": `adjudicate`
clears it when the member confirms, and `assess_coverage` clears it explicitly
when the clarification budget is spent and the turn proceeds on the record. So
the rule is the one already written in CLAUDE.md, applied where it was not:
*a figure is computable exactly when no input is borrowed from a record filed
under another Article* — and while the Article is unsettled, every input is
potentially borrowed.

- [ ] **Step 1: Write the failing test**

```python
def test_an_open_coverage_question_starves_the_record() -> None:
    state = {"member_facts": {"service_years": "21.5"},
             "coverage": {"article": "11", "record_article": "11",
                          "basis": "record", "conflict": True, "why": "elsewhere"}}
    assert _available_facts(state) == {}

def test_a_settled_record_is_usable() -> None:
    # No conflict: this is the ordinary turn and must cost nothing.
def test_a_spent_budget_settles_it() -> None:
    # `why: budget_spent` sets conflict False; the turn proceeds on the record
    # and says what it assumed. Starving here would ask a fourth time by
    # another route.
```

- [ ] **Steps 2-5:** run (fail), implement, run (pass), commit.

---

### Task 2: `check_coverage` asks, and the answer sticks

**Files:**
- Modify: `src/pension_chat/agent/tools.py`, `src/pension_chat/agent/loop.py`
- Test: `tests/test_tools_coverage.py`, `tests/test_loop.py`

**Interfaces:**
- Consumes: `coverage.open_question_for`, `coverage.question_is_live`,
  `coverage.bind_reply` — all unchanged, all pure.
- Produces: `check_coverage` additionally returns `clarification`,
  `open_question` and `clarifications_asked` on a conflict, subject to
  `settings.clarification_budget`; `loop._bind_open_question(state)` resolves a
  live question against the member's message before the investigation starts.

**Binding is deterministic and stays that way.** `bind_reply` is pure Python
that reads an Article number, a section number, or one option's distinguishing
words. Leaving it to the model would put the member's own answer at the mercy
of a re-reading; the pipeline runs it at the top of every turn and the loop
must too.

- [ ] **Steps 1-5:** as above.

---

### Task 3: Measure, then delete what is redundant

- [ ] **Step 1: `--category coverage` on both agents.**

The gate: **loop 8/8, pipeline still 8/8.** Anything less and Tasks 1-2 are
wrong, not the cases.

- [ ] **Step 2: Delete one block, re-run, keep or restore.**

In the spec's order, one commit each, `coverage` re-run on the **pipeline**
after every one — that is where the blocks live:

| Block | Spec verdict | Gate |
|---|---|---|
| `answers_other_article` | delete | L06, and `coverage` unchanged |
| `budget_spent` | delete | G31 and the budget path in `test_coverage_node.py` |
| `BASIS_STATUTORY` + `described_as` | delete | G35 (CRITICAL) |
| `frame` | delete | L08, `_CONDITIONAL_MARKER` holding conditionality |
| `CONFIRMED` elsewhere | **rewrite** | G32 (CRITICAL); "give no figures" is false under §4 stand-ins |
| `described_as` + `BASIS_RECORD` | **tool result** | G31 (CRITICAL); its invitation to correct the record is helpfulness no guardrail enforces |

**A block whose case fails without it has earned its place**, and comes back as
a *fact* `check_coverage` returns, never as a stance the answer is handed. That
distinction is the spec in one line and it is the thing to protect.

- [ ] **Step 3: Record which blocks survived and why.**

## Done when

- `coverage` is 8/8 on both agents, CRITICALs included.
- Each deleted block has a passing case measured without it; each surviving one
  has a failing case recorded.
- `uv run pytest` passes; ruff no worse than 37.

## Not in this phase

- **The learning layer** (§6) — Phase 5.
- **Cutover** — Phase 6, with `fund_smoke` and the full-suite comparison.
- **G17, L04** — open failures from Phase 3c, neither about coverage.
