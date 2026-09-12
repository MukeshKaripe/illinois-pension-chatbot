# Measurement Instrument Implementation Plan (Phase 0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the instrument that decides whether the agent loop is an improvement — eval cases written against the complaints, with the current pipeline's failures recorded as named gaps rather than discovered later as surprises.

**Architecture:** Two new expectation keys in `scripts/eval_agent.py::check()`, a `--mode` flag, and three new suites in `evals/`. The harness already has everything else: `status: expected_fail` with a required `gap:` is exactly "this case is written, it fails today, here is why", and `test_eval_suite.py` reads the implemented expectation keys out of `check()`'s AST, so a new key self-registers.

**Tech Stack:** Python 3.12, `uv`, pytest, YAML eval suites, live Azure OpenAI + Azure AI Search (the baseline run only).

**Spec:** `docs/superpowers/specs/2026-09-12-intelligent-agent-design.md` (§7, "Evaluation, phasing, cutover")

## Global Constraints

- Backend commands run under `uv`. Unit tests must not touch the network; **only Task 7's baseline run does**, and it costs real Azure calls.
- `uv run ruff check .` reports **37 pre-existing errors**. Add none.
- Case ids must be unique across `evals/*.yaml`. Existing prefixes are `E`, `F`, `G`, `R` — **this plan uses `L`** (for loop), `L01`–`L12`.
- Every expectation key used in YAML must be one `check()` actually reads, or `test_every_expectation_is_one_the_checker_reads` fails the build. Keys are discovered from the AST, so implementing a key registers it — there is no list to maintain.
- A case with `status: expected_fail` **must** carry a non-empty `gap:`, and a `blocked` case a `blocked_by:` (`test_eval_suite.py:137,147`).
- Cases name a persona key from `personas.py`, never a raw uuid (`test_eval_suite.py:112`). `article-11-standard` is 21.5 years of service on $72,000.

### What "done" means here, and why it is unusual

**A Phase 0 case that passes is suspect.** The suites exist to measure a shortfall the loop is supposed to close, so most of these cases should fail today, and fail *for the reason written in their `gap:`*. A case that passes against the pipeline was either testing something already working — fine, keep it as a regression guard, marked `active` — or testing nothing, which is the failure this phase must not ship.

So each case is marked honestly: `active` where the behaviour exists today and must not regress, `expected_fail` where it is the loop's job. Phase 3 flips the `expected_fail` cases to `active`, and that flip is the cutover evidence.

### Scope: three suites, not five

The spec's §7 lists five new suites. Two of them land with the phase they gate, not here:

- **`gold_stance.yaml`** gates the Phase 4 CONTEXT-block deletions. Writing it now means writing assertions about blocks whose replacement does not exist; it belongs in the Phase 4 plan, where each case gates one deletion.
- **`gold_capabilities.yaml`** asserts thresholds, ranked alternatives, ranges and projection origins — all Phase 2 features. A case there would fail for "not built yet", which measures nothing.

What belongs here is the three suites that measure the **current** system against the complaints: they need no new features to be meaningful, only the loop.

---

### Task 1: The `mentions` expectation

**Files:**
- Modify: `scripts/eval_agent.py` (inside `check()`)
- Test: `tests/test_eval_checker.py` (create)

**Interfaces:**
- Produces: expectation key `mentions: [phrase, ...]` — passes when the answer contains **at least one** of the phrases, case-insensitively. Used by Tasks 4-6 to assert an answer disclosed an assumption or covered a part of the question.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_checker.py`:

```python
"""The benchmark's checker, tested the way the agent's own layers are.

`test_eval_suite.py` proves an expectation key is *read*. It cannot prove the
key means what the case author thought, and a key that reads the wrong field
passes every case silently -- which is the same failure as a misspelled key,
with better camouflage.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "eval_agent.py"


def _harness():
    spec = importlib.util.spec_from_file_location("eval_agent", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_agent"] = module
    spec.loader.exec_module(module)
    return module


def result(answer: str, **overrides) -> dict:
    """A turn result with every field `check()` reads, defaulted to harmless."""
    base = {
        "question": "q",
        "answer": answer,
        "error": None,
        "guardrail_passed": True,
        "guardrail_report": {},
        "requires_calculation": False,
        "sections": [],
        "articles": [],
        "recipe_provenance": [],
        "derivation_failures": [],
        "validation_errors": [],
        "missing_inputs": [],
        "member_facts": {},
        "coverage": {},
        "hypothetical_frame": {},
        "self_reference": {},
        "clarification": {},
        "clarifications_asked": 0,
        "candidate_articles": [],
        "can_execute": True,
        "execution_blocked": None,
        "calculation": {},
        "selected_provision": {},
        "scenario_set": {},
        "tokens": {"calls": 1},
        "timings": {},
        "in_scope": True,
        "stale_figures": [],
        "answer_rung": None,
    }
    base.update(overrides)
    return base


def test_mentions_passes_when_any_phrase_appears():
    harness = _harness()
    turn = result("I assumed your salary stays where it is.")
    turn["expect"] = {"mentions": ["assum", "held flat"]}
    assert harness.check(turn, []) == []


def test_mentions_fails_when_no_phrase_appears():
    harness = _harness()
    turn = result("Your pension is about $32,000 a year.")
    turn["expect"] = {"mentions": ["assum", "held flat"]}
    problems = harness.check(turn, [])
    assert problems, "an answer disclosing nothing must not pass a disclosure check"
    assert "assum" in problems[0]


def test_mentions_ignores_case():
    harness = _harness()
    turn = result("ASSUMING you stop working today...")
    turn["expect"] = {"mentions": ["assuming"]}
    assert harness.check(turn, []) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_checker.py -v`
Expected: FAIL — `test_mentions_fails_when_no_phrase_appears` fails with `assert problems` (the key is unread, so nothing is checked). Read the failure carefully: the first and third tests *pass* for the wrong reason, which is exactly the camouflage this file exists to strip.

- [ ] **Step 3: Write minimal implementation**

In `scripts/eval_agent.py`, inside `check()`, beside the other answer-text checks (next to the `expect.get("forbids", [])` loop):

```python
    # At least one, not all: an answer may disclose an assumption in any of
    # several natural phrasings, and demanding a particular one tests the
    # wording rather than the behaviour.
    if wanted := expect.get("mentions"):
        lowered = answer.lower()
        if not any(str(phrase).lower() in lowered for phrase in wanted):
            problems.append(f"said none of {list(wanted)}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_checker.py tests/test_eval_suite.py -v`
Expected: PASS — and `test_eval_suite.py` still green, because the AST reader now sees `mentions` as implemented.

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_agent.py tests/test_eval_checker.py
git commit -m "Eval: a mentions expectation, and a test for the checker itself"
```

---

### Task 2: The `conditional` expectation

**Files:**
- Modify: `scripts/eval_agent.py` (inside `check()`, and its imports)
- Test: `tests/test_eval_checker.py`

**Interfaces:**
- Consumes: `pension_chat.agent.guardrails._CONDITIONAL_MARKER` (existing).
- Produces: expectation key `conditional: true` — the answer keeps visibly conditional voice. Reuses the guardrail's own pattern so the benchmark and the runtime cannot disagree about what "conditional" means.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eval_checker.py`:

```python
def test_conditional_wants_the_answer_to_stay_in_the_subjunctive():
    """Answering a supposition in the plain indicative tells a member they are
    in a fund they only asked about."""
    harness = _harness()

    hedged = result("If you were covered by Article 4, your annuity would be 50% of salary.")
    hedged["expect"] = {"conditional": True}
    assert harness.check(hedged, []) == []

    flat = result("Your annuity is 50% of salary attached to rank.")
    flat["expect"] = {"conditional": True}
    assert harness.check(flat, []), "a flat indicative must fail a conditional check"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_checker.py -k conditional -v`
Expected: FAIL — `assert harness.check(flat, [])` fails, because the key is unread.

- [ ] **Step 3: Write minimal implementation**

At the top of `scripts/eval_agent.py`, beside the other `pension_chat` imports:

```python
from pension_chat.agent.guardrails import _CONDITIONAL_MARKER
```

In `check()`, beside the `mentions` block:

```python
    # The runtime's own definition, not a second one. A benchmark that decides
    # separately what counts as conditional will eventually disagree with the
    # guardrail, and then one of them is wrong with no way to tell which.
    if expect.get("conditional") and not _CONDITIONAL_MARKER.search(answer):
        problems.append("answered a supposition in the plain indicative")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_checker.py tests/test_eval_suite.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_agent.py tests/test_eval_checker.py
git commit -m "Eval: a conditional expectation, borrowed from the guardrail"
```

---

### Task 3: The `--mode` flag

**Files:**
- Modify: `scripts/eval_agent.py` (`main()`)
- Test: manual

**Interfaces:**
- Produces: `--mode {pipeline,loop}`, default `pipeline`. `loop` exits 2 with a clear message until Phase 3 lands. The flag exists now so the baseline report records which mode produced it — a baseline with no mode recorded is not a baseline.

- [ ] **Step 1: Add the argument**

In `main()`, beside the other `add_argument` calls:

```python
    parser.add_argument(
        "--mode",
        choices=("pipeline", "loop"),
        default="pipeline",
        help="which agent to run the suite against (loop lands in Phase 3)",
    )
```

- [ ] **Step 2: Refuse the mode that does not exist yet**

Immediately after the args are parsed, before any case runs:

```python
    if args.mode == "loop":
        print(
            "--mode loop is not available yet: the agent loop lands in Phase 3. "
            "Run without --mode to measure the pipeline."
        )
        return 2
```

- [ ] **Step 3: Record the mode in the report**

`report` is a **list** of cases (`scripts/eval_agent.py:631-634`), so there is no
top-level object to add a key to. Wrap it, which changes the file's shape:

```python
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(
                {"mode": args.mode, "cases": report}, indent=2, default=str
            ),
            encoding="utf-8",
        )
```

Nothing reads these files today — they are output artifacts — so the shape change
costs nothing now, and a report that cannot say which agent produced it is not a
baseline. Any future reader takes `["cases"]`.

- [ ] **Step 4: Verify both paths**

```bash
uv run python scripts/eval_agent.py --mode loop
```
Expected: prints the message, exits 2, runs no cases.

```bash
uv run python scripts/eval_agent.py --help
```
Expected: `--mode {pipeline,loop}` appears in the help.

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_agent.py
git commit -m "Eval: --mode, so a baseline records which agent produced it"
```

---

### Task 4: `gold_no_ask.yaml` — the complaint-#1 suite

**Files:**
- Create: `evals/gold_no_ask.yaml`

This is the sharpest suite in the plan, because it turns "it feels like a form" into something falsifiable. The member's profile holds age, service and salary. Any clarification the profile could have answered is a **failure**, not a style preference.

- [ ] **Step 1: Write the suite**

```yaml
# Asking for what is already known, which is complaint #1 made falsifiable.
#
# `article-11-standard` is 21.5 years of service on $72,000, with a date of
# birth and a ten-year salary history on file. A turn that asks this member for
# their salary, their service or their age is not being careful. It is being a
# form, and the member has to answer a question the system could have answered
# itself.
#
# These fail today for a reason worth stating precisely: a future-dated figure
# needs a salary and a service figure *at that date*, and `NEVER_ASSUMABLE`
# forbids inventing either. That ban is right -- assuming an unknown salary
# invents a member -- but it catches projection too, and projecting a known
# salary forward under a stated rule is what every pension estimator does.
# Phase 2 adds the projection class; Phase 3 lets the agent reach for it.

category: no_ask

cases:
  - id: L01
    name: Two more years, answered rather than interrogated
    employee: article-11-standard
    critical: true
    status: expected_fail
    gap: >
      Needs the projection input class (Phase 2) and an agent that reaches for
      it (Phase 3). Today final_average_salary at a future date is
      NEVER_ASSUMABLE, so the turn asks for a salary the record already holds.
    turns:
      - q: if I work two more years, what would my pension be?
        expect:
          # Everything needed is on file or derivable from it.
          asks: false
          asks_for_nothing_known: true
          calculation: true
          # Having projected, say so. A figure resting on an unstated
          # assumption is worse than no figure.
          mentions: ["assum", "held flat", "as it stands"]

      # A correction to the projection rule, not a new question.
      - q: assume I get a 3% raise each year
        expect:
          asks: false
          recompute: true
          calculation: true

      # A correction to a fact. The figure must move; nothing else should.
      - q: actually I'm 57
        expect:
          asks: false
          recompute: true
          calculation: true

  - id: L02
    name: A figure the record alone can support, asked for plainly
    employee: article-11-standard
    status: active
    turns:
      - q: what would my pension be if I retired now?
        expect:
          asks_for_nothing_known: true
          calculation: true
          cites: ["11-134"]
```

- [ ] **Step 2: Verify the suite parses and its keys are real**

Run: `uv run pytest tests/test_eval_suite.py -v -k "L01 or L02 or unique or shape"`
Expected: PASS — ids unique, every expectation implemented, `expected_fail` carries its `gap`, `article-11-standard` resolves.

- [ ] **Step 3: Commit**

```bash
git add evals/gold_no_ask.yaml
git commit -m "Evals: asking for what is already on file"
```

---

### Task 5: `gold_goal_completion.yaml`

**Files:**
- Create: `evals/gold_goal_completion.yaml`

- [ ] **Step 1: Write the suite**

```yaml
# Whether the answer served the question, which calling tools cannot show.
#
# An agent can search competently, compute correctly, cite honestly, and still
# answer two thirds of what was asked. `needs_figure` is a boolean, so a turn
# containing an eligibility question and a calculation question collapses to
# one of them -- and the member has to ask the other half again.

category: goal_completion

cases:
  - id: L03
    name: Eligibility and amount are one question, not two
    employee: article-11-standard
    critical: true
    status: expected_fail
    gap: >
      Needs goal decomposition (Phase 3). `needs_figure` flattens a two-part
      question into one, so the eligibility half arrives only if the prose
      happens to cover it.
    turns:
      - q: I'm thinking of retiring at 58. Am I eligible, and how much would I get?
        expect:
          calculation: true
          # The eligibility half, in the Code's own terms.
          mentions: ["eligib", "qualif", "20 years", "age 55"]
          cites: ["11-134|11-130"]

  - id: L04
    name: A correction modifies the scenario rather than starting over
    employee: article-11-standard
    status: expected_fail
    gap: >
      Needs conversational state the agent reasons over rather than a scenario
      list a regex corroborates (Phase 3).
    turns:
      - q: what would I get if I worked two more years?
        expect: { calculation: true }
      - q: actually make that three years
        expect:
          asks: false
          recompute: true
          calculation: true

  - id: L05
    name: A follow-up about a spouse continues the conversation
    employee: article-11-standard
    critical: true
    status: expected_fail
    gap: >
      Needs the turn read as a continuation (Phase 3). Today "what about my
      wife?" carries no benefit vocabulary, so the survivor provisions are not
      what gets retrieved.
    turns:
      - q: what would my pension be if I retired at the end of this year?
        expect: { calculation: true }
      # Names no benefit, no section and no age. It is a survivor question
      # only because of what came before it.
      - q: what about my wife?
        expect:
          asks: false
          mentions: ["survivor", "widow", "spouse"]
          cites: ["11-145|11-145.1|11-153"]

  - id: L06
    name: A question about another Article is answered as law, not as this member
    employee: article-11-standard
    status: active
    turns:
      - q: what does Article 4 provide for police officers?
        expect:
          article_only: "4"
          no_figures: true
```

- [ ] **Step 2: Verify the suite**

Run: `uv run pytest tests/test_eval_suite.py -v -k "L0 or unique"`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add evals/gold_goal_completion.yaml
git commit -m "Evals: whether the answer served the question"
```

---

### Task 6: `gold_conversational.yaml`

**Files:**
- Create: `evals/gold_conversational.yaml`

- [ ] **Step 1: Write the suite**

```yaml
# The behaviours the loop exists for: searching again after a bad search,
# reaching for a tool only when it is needed, computing under a premise the
# member supposed, and reusing knowledge already derived.

category: conversational

cases:
  - id: L07
    name: A question in the member's words, answered from the Code's
    employee: article-11-standard
    critical: true
    status: expected_fail
    gap: >
      Needs iterative retrieval (Phase 3) and the heading vocabulary (Phase 1,
      landed). "Losing too much" appears nowhere in the statute; the Code says
      "annuity reduction". One query written before any statute is seen cannot
      recover from missing on the first attempt.
    turns:
      - q: can I retire early without losing too much?
        expect:
          cites: ["11-134"]
          mentions: ["reduc", "0.25", "age 60"]

  - id: L08
    name: A figure under a premise the member supposed
    employee: article-11-standard
    critical: true
    status: expected_fail
    gap: >
      Needs the stand-in input origin (Phase 2) and the deletion of the
      answers_other_article calculation ban (Phase 3). Today naming another
      fund's Article sets requires_calculation = False, so no figure is
      reachable however much the member supplies.
    turns:
      - q: if I were a Chicago police officer with my salary and service, what would I get?
        expect:
          calculation: true
          # It is a supposition throughout, and must read as one.
          conditional: true
          # Article 4's own law, never Article 11's formula wearing its name.
          article_only: "4"

  - id: L09
    name: A question that needs no member and no calculation
    employee: article-11-standard
    status: active
    turns:
      - q: what is Article 11?
        expect:
          no_calculation: true
          no_figures: true
          cites: ["11-101|11-102|11-103"]

  - id: L10
    name: The same calculation asked twice reuses what was derived
    employee: article-11-standard
    status: active
    turns:
      - q: what would my pension be if I retired now?
        expect: { calculation: true }
      - q: remind me what my annuity would come to?
        expect:
          calculation: true
          # Derived once, then read from the registry -- the point of the
          # registry, and the thing complaint #7 doubted was working.
          reuse: true
```

- [ ] **Step 2: Verify the suite**

Run: `uv run pytest tests/test_eval_suite.py -v`
Expected: PASS — the whole meta-suite, including the critical-case census.

- [ ] **Step 3: Commit**

```bash
git add evals/gold_conversational.yaml
git commit -m "Evals: search again, tool only when needed, suppose, reuse"
```

---

### Task 7: Record the baseline

**Files:**
- Create: `docs/superpowers/baselines/2026-09-13-pipeline.json`
- Create: `docs/superpowers/baselines/README.md`

This is the task the phase exists for. It costs real Azure calls and takes several minutes.

- [ ] **Step 1: Run the three new suites against the pipeline**

```bash
uv run python scripts/eval_agent.py --category no_ask --json docs/superpowers/baselines/2026-09-13-no-ask.json
uv run python scripts/eval_agent.py --category goal_completion --json docs/superpowers/baselines/2026-09-13-goal-completion.json
uv run python scripts/eval_agent.py --category conversational --json docs/superpowers/baselines/2026-09-13-conversational.json
```

- [ ] **Step 2: Read each failure and check it against its `gap:`**

For every `expected_fail` case, the reported problem must **match the reason its `gap:` gives**. A case failing for a different reason than the one written down is the most valuable finding this phase can produce: it means the diagnosis in the spec is wrong somewhere, and it is far cheaper to learn that now than in Phase 3.

Write what actually happened beside each case. If a case fails for an unwritten reason, **update its `gap:`** to say the true reason, and note it for the spec.

- [ ] **Step 3: Run the existing suite too, so the loop has something to regress against**

```bash
uv run python scripts/eval_agent.py --json docs/superpowers/baselines/2026-09-13-pipeline.json
```

Record the per-category pass rates, the CRITICAL block's verdict, time-to-first-token and tokens per turn. These are the numbers cutover criteria 1, 2 and 5 compare against.

- [ ] **Step 4: Write the baseline README**

Create `docs/superpowers/baselines/README.md`:

```markdown
# Baselines

What the pipeline scored before the agent loop existed, so "the loop is better"
is a measurement rather than an impression.

Cutover (spec §7) compares the loop against `2026-09-13-pipeline.json`:

1. the CRITICAL block passes;
2. no existing category regresses by more than one case, overall rate at least
   the pipeline's;
3. `fund_smoke` still resolves every fund to its own Article;
4. the three new suites: loop passes, pipeline fails — **if the pipeline also
   passes a case, that case was not testing what we thought**;
5. hard guardrail-failure rate no higher than the pipeline's.

TTFT and tokens per turn are recorded and gate nothing, per the decision to
defer latency until deployment moves into the models' region.

Runs are dated and never edited. A second pipeline run months from now is a new
file, not an overwrite of this one.
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/baselines/
git commit -m "Baseline: what the pipeline scores before the loop exists"
```

---

## Done when

- `uv run pytest` passes, with the new `tests/test_eval_checker.py` green and `tests/test_eval_suite.py` accepting all ten new cases.
- `uv run ruff check .` reports no more than 37 errors.
- `--mode loop` refuses cleanly; `--mode pipeline` runs.
- Every `expected_fail` case has been run, and its `gap:` matches the reason it actually failed.
- `docs/superpowers/baselines/` holds a dated pipeline report and the README explaining what it is for.

## Not in this phase

- **`gold_stance.yaml`** — lands with the Phase 4 plan, where each case gates one CONTEXT-block deletion.
- **`gold_capabilities.yaml`** — lands with the Phase 2 plan, where thresholds, ranked alternatives, ranges and projection origins are built.
- **An `input_origin` expectation key.** `run_turn` does not expose `input_sources` today, and nothing needs the key until Phase 2 introduces `projected` and `stand_in`. Add both together there.
