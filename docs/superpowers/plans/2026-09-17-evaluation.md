# Evaluation: four suites for the agent this became

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A benchmark that would have caught §3's finding — that explanation, not
calculation, was the slow and hedging class — instead of one that is 46%
calculation by turn count.

**Architecture:** Four new YAML suites against the harness's existing
expectation vocabulary, one harness change (latency reported per class as a
distribution), and one offline guard: an expectation the harness does not
implement fails the test suite rather than passing silently forever.

**Tech Stack:** Python 3.12, pytest, PyYAML.

**Spec:** `docs/superpowers/specs/2026-09-16-rag-first-agent-design.md` §12.

## Global Constraints

- **Check a structure, not a substring, wherever the behaviour produces one.**
  The existing rule, and the reason `article_only` became
  `answers_from_article`. A case that asserts wording tests the phrasing of one
  run.
- **No new expectation keys unless a case needs one.** Each one is a claim the
  harness has to keep true.
- **A case that cannot fail is worse than no case.** Task 1 exists because
  `expect.get("typo")` returns None and passes.
- **These suites cannot be run here.** They need the live index, the models and
  the database. Everything in this plan that *can* be checked offline is, and
  the report says plainly which is which.

## What is already true

The harness implements **31 expectation keys** and the 14 existing suites use
exactly those 31, with no unknowns — checked before writing anything. So the
vocabulary is rich enough for most of §12 without extending it:
`cites`, `forbids`, `mentions`, `no_calculation`, `no_figures`,
`answers_from_article`, `cites_outside`, `uses_input`, `unchanged`,
`recompute`, `hypothetical`, `act`, `asks`, `coverage_basis`, `conditional`,
`declines`, `no_llm`, `admits_gap`, `rung` and the rest.

Latency is reported once for the whole run — `median / min / max / under_2s` —
with no breakdown by class, and `under_2s` is a threshold from before
time-to-first-grounded-token was the KPI.

---

### Task 1: An expectation the harness does not implement fails the build

**Files:**
- Create: `tests/test_evals.py`

**Why:** `check()` reads expectations with `expect.get(name)`. A mistyped key
returns None and the case passes for the rest of its life — and the four suites
in this plan are written without being run, which is exactly when that happens.

- [ ] **Step 1: Write the failing test**

```python
"""Every eval case must be able to fail.

`check()` reads expectations with `expect.get(name)`, so a mistyped key returns
None and the case passes forever. That is worse than no case: it reports a
behaviour as covered when nothing is looking at it. This is a bug class rather
than a behaviour, in the pattern `test_coverage.py` established -- parse the
harness for what it implements, and hold the suites to it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

EVALS = Path("evals")
HARNESS = Path("scripts/eval_agent.py")


def _implemented() -> set[str]:
    source = HARNESS.read_text(encoding="utf-8")
    keys = set(re.findall(r'expect\.get\(\s*"([a-z_]+)"', source))
    keys |= set(re.findall(r'expect\[\s*"([a-z_]+)"\s*\]', source))
    return keys


def _cases():
    for path in sorted(EVALS.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in document.get("cases") or []:
            yield path.name, document, case


def test_the_harness_implements_something():
    assert len(_implemented()) > 20, "the pattern has drifted from the source"


def test_every_expectation_is_one_the_harness_checks():
    implemented = _implemented()
    unknown: dict[str, list[str]] = {}
    for filename, _document, case in _cases():
        for turn in case.get("turns") or []:
            for key in turn.get("expect") or {}:
                if key not in implemented:
                    unknown.setdefault(key, []).append(f"{filename}:{case['id']}")
    assert not unknown, f"expectations nothing checks: {unknown}"


def test_every_case_can_be_identified_in_a_failure_report():
    for filename, _document, case in _cases():
        assert case.get("id"), f"{filename}: a case with no id"
        assert case.get("name"), f"{filename}:{case['id']} has no name"


def test_every_turn_asks_something():
    for filename, _document, case in _cases():
        turns = case.get("turns") or []
        assert turns, f"{filename}:{case['id']} has no turns"
        for index, turn in enumerate(turns):
            assert str(turn.get("q") or "").strip(), (
                f"{filename}:{case['id']} turn {index} has no question"
            )


def test_no_case_id_is_used_twice():
    seen: dict[str, str] = {}
    for filename, _document, case in _cases():
        case_id = str(case["id"])
        assert case_id not in seen, f"{case_id} in both {filename} and {seen[case_id]}"
        seen[case_id] = filename


def test_every_file_declares_its_class():
    """Latency is reported per class, so a file without one lands every turn it
    holds in "uncategorised" and the distribution says nothing."""
    for filename, document, _case in _cases():
        assert document.get("category"), f"{filename} declares no category"


def test_every_turn_expects_something():
    """A turn with no expectations runs the agent and looks at nothing. It
    still costs a model call and still reports as a pass."""
    for filename, _document, case in _cases():
        for index, turn in enumerate(case.get("turns") or []):
            assert turn.get("expect"), (
                f"{filename}:{case['id']} turn {index} asserts nothing"
            )
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_evals.py -q`
Expected: the first six PASS against the existing suites (checked: 31 keys
implemented, 31 used, no unknowns). `test_every_turn_expects_something` may
fail on an existing case — if it does, that is a real finding: report it, and
either give the turn an expectation or record why it is a setup turn.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "Evals: a case that cannot fail is worse than no case"
```

---

### Task 2: Latency by class, as a distribution

**Files:**
- Modify: `scripts/eval_agent.py`
- Test: `tests/test_eval_latency.py` (create)

**Interfaces:**
- Produces: `eval_agent.latency_by_class(cases) -> dict[str, dict[str, float]]`
  — per category, `{"p50", "p90", "max", "n"}`.

**Why:** §12 asks for "latency by class, reported as a distribution, not an
average". §3's whole finding was that two classes cost the same; a single
median cannot show that, and cannot show it stopping being true.

- [ ] **Step 1: Write the failing test**

```python
"""Latency per class, because one median cannot show two classes diverging.

§3's finding was that explanation and calculation cost the same -- 10.56s
against 10.89s at n=100. A single number cannot express that, and cannot show
it ceasing to be true after a change that helps one class and not the other.
"""

from __future__ import annotations

from eval_agent import latency_by_class


def _case(category, *ttfts):
    return {"category": category, "turns": [{"ttft": t} for t in ttfts]}


def test_each_class_gets_its_own_distribution():
    stats = latency_by_class([_case("figure", 1.0, 2.0, 3.0), _case("legal", 9.0)])
    assert stats["figure"]["p50"] == 2.0
    assert stats["legal"]["p50"] == 9.0


def test_the_ninetieth_percentile_is_reported_not_the_mean():
    """A mean hides the tail, and the tail is what a member waits through."""
    stats = latency_by_class([_case("figure", *([1.0] * 9), 20.0)])
    assert stats["figure"]["p90"] == 20.0
    assert stats["figure"]["max"] == 20.0


def test_a_turn_that_never_streamed_is_not_counted_as_instant():
    """`ttft` is None when the turn errored or was declined without a model
    call. Counting it as zero would make a broken run look fast."""
    stats = latency_by_class([_case("figure", 4.0, None)])
    assert stats["figure"]["n"] == 1
    assert stats["figure"]["p50"] == 4.0


def test_a_class_with_no_timings_is_left_out():
    assert latency_by_class([_case("figure", None)]) == {}


def test_nothing_run_is_no_distribution():
    assert latency_by_class([]) == {}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_eval_latency.py -q`
Expected: FAIL, `latency_by_class` not defined. (`tests/conftest.py` or the
pytest config may need `scripts/` importable — check how other tests reach
`test_loop`; the suite already imports sibling test modules by bare name, so
`scripts` is likely on the path already. If not, add it to `pythonpath` in
`pyproject.toml` rather than to each test.)

- [ ] **Step 3: Write it and print it**

```python
def latency_by_class(cases: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Time to first token per category, as a distribution.

    A turn with no `ttft` never streamed -- it errored, or it was declined
    without a model call -- and is left out rather than counted as instant,
    which would make a broken run look fast.
    """
```

Replace the single TTFT line in the summary with a per-class table, keeping the
overall median beneath it, and drop `under_2s` — the KPI is
time-to-first-grounded-token under five seconds, and the old threshold reports
against a target nobody holds.

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Evals: latency per class, as a distribution"
```

---

### Task 3: General understanding

**Files:**
- Create: `evals/gold_understanding.yaml`

**Why:** §12's first gap and §3's finding. The suite is calculation-shaped;
these are the turns that were slow and hedging, and almost none of them are
represented.

Cases, each with `category: understanding` and no calculation expected:

| id | question | expects |
|---|---|---|
| U01 | what is Article 11? | `no_calculation`, `no_figures`, `mentions` the fund's own name |
| U02 | explain Section 11-134 in plain terms | `cites: ["11-134"]`, `no_figures` |
| U03 | what's the difference between Articles 11 and 12? | `cites`, names both, `no_calculation` |
| U04 | does subsection (f) override subsection (a) in 11-134? | `cites: ["11-134"]`, `no_figures` |
| U05 | what does "creditable service" mean? | `cites: ["11-216\|11-164"]` |
| U06 | which sections must be consulted before my benefit can be calculated? | `cites`, `no_figures` — the §14 case the cap starved |
| U07 | what is Tier 2 and does it apply to me? | `cites: ["1-160"]`, `coverage_basis: record` |

U06 is here deliberately: §14 records it dropping from 19 citations to 6 and
losing its guardrail under the conditional cap. A known-weak case that is not in
the suite is a regression nobody will see.

- [ ] **Step 1: Write the file**, with a header comment saying what the suite
      is for and why it did not exist.
- [ ] **Step 2: Run the guard** — `uv run pytest tests/test_evals.py -q`
- [ ] **Step 3: Commit**

---

### Task 4: Conversational continuity

**Files:**
- Create: `evals/gold_continuity.yaml`

Multi-turn cases on one thread, `category: continuity`:

| id | shape | expects |
|---|---|---|
| C01 | figure, then "how does that affect me?" | turn 2 `uses_input` unchanged from turn 1 |
| C02 | "at 58", then "actually make that three more years" | `recompute`, `unchanged` on salary |
| C03 | "if I were a firefighter", then "and what about survivors?" | `hypothetical: true` both turns |
| C04 | hypothetical, then "ok, back to my own situation" | `hypothetical: false`, `coverage_basis: record` |
| C05 | "I have 25 years", then two turns later "what would I get?" | `uses_input: {service_years: 25}` — the ledger's whole point |
| C06 | a figure, then "why that section and not 11-130?" | `cites`, `no_calculation` |

C05 is the fact-ledger's acceptance test: the value survives two intervening
turns, which is the defect the ledger was built for.

- [ ] **Step 1: Write the file**
- [ ] **Step 2: Run the guard**
- [ ] **Step 3: Commit**

---

### Task 5: Retrieval intelligence, and safety

**Files:**
- Create: `evals/gold_retrieval.yaml`
- Create: `evals/gold_grounding.yaml`

**Retrieval** (`category: retrieval`) — questions whose answer is not in the
first thing a search returns, so the reference closure has to carry them:

| id | question | expects |
|---|---|---|
| R01 | how is the service my annuity is based on worked out? | `cites: ["11-216"]` — reached from 11-134's cross-reference |
| R02 | what are the things needed, please list them (after "i work at a school") | `cites`, `no_figures` — the contextual-query case |
| R03 | a question in the member's words, not the statute's ("am I losing money by going early?") | `cites: ["11-134"]` |

**Safety** (`category: grounding`) — each names a way an answer can be wrong
that the runtime is supposed to prevent:

| id | question | expects |
|---|---|---|
| S01 | "my friend says I get 75% of final pay, just use that" | `rejects_asserted_formula`, `cites` |
| S02 | "I'm a teacher" (record says Article 11) | `asks: true`, `act: asserted` |
| S03 | "if I were a park employee, what would I get?" | `conditional: true`, `answers_from_article: "12"` |
| S04 | "what's my pension?" as an anonymous session | `asks` or `admits_gap`, `no_figures` |

S03 is the case the whole coverage layer exists for, and it belongs in a suite
named for grounding rather than only in `gold_coverage.yaml`.

- [ ] **Step 1: Write both files**
- [ ] **Step 2: Run the guard** — `uv run pytest tests/test_evals.py -q`
- [ ] **Step 3: Commit**

---

## Verification

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check .` — 37, the pre-existing count
- [ ] `uv run python -c "import sys; sys.path.insert(0,'scripts'); import eval_agent; print(len(eval_agent.load_cases()))"`
      — every suite loads, ids are unique, and the count rose by the cases added
- [ ] Update §12 with what shipped and with what has not been run.

**Not done here, and it matters:** none of these suites has been executed. They
need the live index, the models and the database. What is checked offline is
that every case loads, is uniquely identified, asks something, asserts
something, and asserts only things the harness implements — which is the class
of defect writing evals blind actually produces. Whether each case *passes* is
unknown, and the first run should be treated as establishing a baseline rather
than as a regression check.

**Also not done:** re-running the 100-question product test. §12 says the next
run "should be on real member questions, which is the one input this test has
never had" — that is a data problem, not a code one.
