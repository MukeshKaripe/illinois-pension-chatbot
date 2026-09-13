# Knowing the Cliffs Implementation Plan (Phase 2b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell a member the thing a good advisor would say unprompted — that they are 44 months from losing an 11% reduction — and stop a reused recipe from forgetting why it was chosen.

**Architecture:** No new engine. A recipe's `rule_specification.parameters` already names its statutory boundaries with their sources (`age_60: 60`, `minimum_service_years: 20`, `maximum_salary_percentage: 0.8`), and pricing a boundary is `build_member_facts(assumed_retirement_age=…)` through the scenario path Phase 2a already exercises. The work is finding the boundaries and pricing them, plus persisting the alternatives a derivation computes and currently throws away.

**Tech Stack:** Python 3.12, `uv`, pytest (`asyncio_mode = "auto"`), ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-12-intelligent-agent-design.md` (§4, "Return the candidate set" and "Thresholds")

## Global Constraints

- Backend commands under `uv`. Unit tests must not touch the network.
- `uv run ruff check .` reports **37 pre-existing errors**. Add none.
- **Every figure must live inside `calculation_results`.** `guardrails._collect_allowed_numbers` (`:291`) walks that structure recursively; a number beside it is rejected by the verifier and reads as a hallucination.
- Threshold figures come from the **same DSL interpreter under the same validator** as the main figure. Nothing computes a benefit amount outside it.
- `article-11-standard` is the measurement member: age 56, 21.5 years, $69,956.40 highest 4-year average, §11-134 at 2.40%, 0.25%/month reduction, 44 months before 60.

### What the registry actually contains, and what that changes

The spec's §4 says `lookup_recipe` returns `candidates[0]` and discards the
alternatives. Measured against the live registry, **it discards nothing**:

```
8 rows, one per (article, formula_type, tier) -- 5, 6, 7, 8, 9, 11, 12, 19
art 11 retirement_annuity tier 1 -> 1 row   §11-134   agent_derived   estimate
```

There are no competing candidates, and there never will be: `persist_recipe`
returns early when the content-addressed `formula_id` exists, and `lookup_recipe`
finds the one row before derivation is ever reached. **Reuse is what prevents
alternatives from being discovered**, so a plural lookup returns a list of one.

The real loss is next door. `derive_recipe` computes
`alternatives_considered` — which neighbouring provisions were weighed and set
aside, the answer to "why §11-134 and not §11-130" — and `resolve_recipes` puts
it in that turn's provenance. `persist_recipe` never writes it:

```
rule_specification.alternatives_considered : null
```

So the first member to ask gets the reasoning, and every member after gets a
reused recipe with none. That is the defect worth fixing, and it is Task 1.

### Scope

**In:** persisted alternatives; threshold detection and pricing; thresholds in
the answer and the card; `evals/gold_capabilities.yaml`.

**Out, with reasons:**

- **`lookup_recipes` plural.** Nothing to return until alternatives exist as
  rows, which they do not and will not while reuse blocks derivation. Revisit
  if Task 1 shows recipes competing.
- **Ranges from `plausible_range`.** It fires only for an *assumed* input, and
  `POLICY` covers three: `withdrawal_date`, `annuity_start_date`,
  `months_before_age_60`. A member with a record has an exact date of birth, so
  `months_before_age_60` is computed, not assumed, and the range is empty. It is
  reachable only for an anonymous member who states a bare age — a real case,
  but a narrow one, and better built when the loop makes anonymous conversations
  common (Phase 3).

---

### Task 1: A reused recipe keeps its reasoning — ALREADY DONE

**Dropped on execution, 2026-09-13.** The reasoning is persisted and read back;
I diagnosed it from the wrong column. `rule_specification.alternatives_considered`
is `null`, which looks exactly like the reasoning having been dropped — but
`persist_recipe` writes it to the **`applicability`** column, and
`RegistryRecipe.__init__` reads it from there and normalises it for rows written
before it was typed. The live Article 11 row carries both alternatives, and
`to_dict()` emits them.

What survives is `tests/test_registry_alternatives.py`, as a guard: the storage
location is subtle enough that the next person to look will make the same
mistake, and the test says where to look.

---

### Task 2: Find the cliffs

**Files:**
- Create: `src/pension_chat/calculation/thresholds.py`
- Test: `tests/test_thresholds.py` (create)

**Interfaces:**
- Produces: `find_thresholds(spec: dict, facts: dict) -> list[Threshold]`, where `Threshold` is a frozen dataclass carrying `fact`, `boundary`, `current`, `distance_months`, `section`, `subsection`, `description`. Pure function, no I/O, no model call. Task 3 prices them.

A recipe names its boundaries twice over, both with sources:

```
parameters: age_60 60 · minimum_service_years 20 · maximum_salary_percentage 0.8
predicates: withdrawal_date ge 1990-07-01 · service_years ge 20
```

Only two rules matter: a boundary the member has **already passed** is not a
cliff, and a boundary they will **never reach** is not one either.

- [ ] **Step 1: Write the failing test**

Create `tests/test_thresholds.py`:

```python
"""Boundaries a member is approaching, found in the recipe that names them.

The Pension Code is a field of cliffs -- age 60, 20 years, 30 years, the Tier
1/2 hire date -- and a member four months from one is owed the sentence a good
advisor would say unprompted. Nothing in the system noticed them.
"""

from __future__ import annotations

from pension_chat.calculation.thresholds import find_thresholds

SPEC = {
    "parameters": {
        "age_60": {"value": 60, "source": {"section": "11-134", "subsection": "(a)"}},
        "minimum_service_years": {
            "value": 20,
            "source": {"section": "11-134", "subsection": "(a)"},
        },
        "maximum_salary_percentage": {
            "value": 0.8,
            "source": {"section": "11-134", "subsection": "(a)"},
        },
    },
    "applicability_predicates": [
        {"fact": "service_years", "operator": "ge", "value": 20,
         "source": {"section": "11-134", "subsection": "(a)"},
         "description": "at least 20 years of service"},
    ],
}

FACTS = {"age": 56, "service_years": 21.5, "months_before_age_60": 44}


def test_an_age_boundary_ahead_is_a_cliff():
    found = {t.fact: t for t in find_thresholds(SPEC, FACTS)}
    assert "age" in found
    assert found["age"].boundary == 60
    assert found["age"].current == 56
    assert found["age"].distance_months == 44
    assert found["age"].section == "11-134"


def test_a_boundary_already_passed_is_not_a_cliff():
    """21.5 years is past the 20-year minimum. Saying so helps nobody."""
    found = {t.fact for t in find_thresholds(SPEC, FACTS)}
    assert "service_years" not in found


def test_a_service_boundary_ahead_is_a_cliff():
    early = {"age": 47, "service_years": 9.0, "months_before_age_60": 156}
    found = {t.fact: t for t in find_thresholds(SPEC, early)}
    assert found["service_years"].boundary == 20
    assert found["service_years"].current == 9.0


def test_a_boundary_beyond_the_horizon_is_not_reported():
    """A 47-year-old is thirteen years from 60. That is not news they can act on."""
    early = {"age": 47, "service_years": 9.0, "months_before_age_60": 156}
    found = {t.fact for t in find_thresholds(SPEC, early, horizon_months=60)}
    assert "age" not in found


def test_a_percentage_parameter_is_not_mistaken_for_a_boundary():
    """0.8 is a cap on the answer, not a date or a count the member approaches."""
    found = {t.fact for t in find_thresholds(SPEC, FACTS)}
    assert "maximum_salary_percentage" not in found


def test_nothing_is_found_without_facts_to_compare_against():
    assert find_thresholds(SPEC, {}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thresholds.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pension_chat.calculation.thresholds'`

- [ ] **Step 3: Implement**

Create `src/pension_chat/calculation/thresholds.py`:

```python
"""Boundaries the member is approaching, read out of the recipe that names them.

The Code is a field of cliffs. Section 11-134 reduces an annuity by 0.25% for
each month it begins before age 60, so a member of 56 is carrying an 11%
reduction that vanishes on a date nobody told them about. The recipe has always
known: its `parameters` name `age_60` and `minimum_service_years` with the
subsection each came from, and its applicability predicates name the rest.

Nothing here computes a benefit. It finds the boundary; `execute_calculations`
prices it through the same interpreter as every other figure, which is what
makes a cliff figure exactly as trustworthy as the main one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

#: Which member fact each boundary is about. A parameter named for a fact is a
#: boundary on that fact; anything else -- a rate, a cap, a percentage -- is an
#: input to the arithmetic rather than a date the member is walking towards.
_BOUNDARY_FACTS = {
    "age_60": "age",
    "age_55": "age",
    "age_65": "age",
    "age_67": "age",
    "minimum_service_years": "service_years",
    "maximum_service_years": "service_years",
}

#: Facts whose distance is measured in months of the member's life.
_MONTHS_PER_UNIT = {"age": 12, "service_years": 12}

#: Past this, a boundary is not news a member can act on.
_DEFAULT_HORIZON_MONTHS = 72


@dataclass(frozen=True)
class Threshold:
    """One boundary, and how far the member is from it."""

    fact: str
    boundary: Any
    current: Any
    distance_months: int
    section: str | None
    subsection: str | None
    description: str


def _number(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def find_thresholds(
    spec: dict,
    facts: dict,
    *,
    horizon_months: int = _DEFAULT_HORIZON_MONTHS,
) -> list[Threshold]:
    """Boundaries this member is approaching, nearest first.

    A boundary already passed is not a cliff, and one beyond the horizon is not
    news they can act on -- both are dropped rather than reported quietly.
    """
    found: dict[tuple[str, str], Threshold] = {}

    def consider(fact: str, boundary: Any, source: dict, description: str) -> None:
        current = _number(facts.get(fact))
        target = _number(boundary)
        if current is None or target is None or target <= current:
            return
        per_unit = _MONTHS_PER_UNIT.get(fact)
        if per_unit is None:
            return
        distance = int((target - current) * per_unit)
        if distance <= 0 or distance > horizon_months:
            return
        key = (fact, str(target))
        if key in found and found[key].distance_months <= distance:
            return
        found[key] = Threshold(
            fact=fact,
            boundary=int(target) if target == target.to_integral_value() else float(target),
            current=int(current) if current == current.to_integral_value() else float(current),
            distance_months=distance,
            section=(source or {}).get("section"),
            subsection=(source or {}).get("subsection"),
            description=description,
        )

    for name, parameter in (spec.get("parameters") or {}).items():
        fact = _BOUNDARY_FACTS.get(name)
        if fact is None:
            continue
        if not isinstance(parameter, dict):
            continue
        consider(
            fact,
            parameter.get("value"),
            parameter.get("source") or {},
            f"{name.replace('_', ' ')} under the controlling provision",
        )

    for predicate in spec.get("applicability_predicates") or []:
        if not isinstance(predicate, dict):
            continue
        if predicate.get("operator") not in {"ge", "gt", ">=", ">"}:
            continue
        fact = str(predicate.get("fact") or "")
        if fact not in _MONTHS_PER_UNIT:
            continue
        consider(
            fact,
            predicate.get("value"),
            predicate.get("source") or {},
            str(predicate.get("description") or fact),
        )

    return sorted(found.values(), key=lambda t: t.distance_months)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thresholds.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/calculation/thresholds.py tests/test_thresholds.py
git commit -m "Thresholds: the cliffs a recipe already names"
```

---

### Task 3: Price them through the same engine

**Files:**
- Modify: `src/pension_chat/agent/nodes.py::execute_calculations`
- Test: `tests/test_thresholds.py`

**Interfaces:**
- Consumes: `find_thresholds` from Task 2.
- Produces: `calculation_result[formula_type]["thresholds"]` — a list of `{fact, boundary, distance_months, section, subsection, annual_amount, difference_from_now_annual}`. **Inside `calculation_results`**, so the guardrail admits the figures.

An age boundary is priced the way a scenario is: `build_member_facts(assumed_retirement_age=boundary)` through the same interpreter. So the cliff figure carries the same validation and the same provenance as the answer's own number.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_thresholds.py`:

```python
async def test_an_age_cliff_is_priced_through_the_same_interpreter(monkeypatch):
    """A figure at the boundary is only worth saying if it is as trustworthy as
    the figure beside it -- so it comes from the same recipe, the same facts
    builder and the same DSL run, not from an estimate of the difference."""
    from pension_chat.agent import nodes

    priced: list[int] = []
    real_build = nodes.build_member_facts

    def spy(employee, history=None, **kwargs):
        if kwargs.get("assumed_retirement_age"):
            priced.append(int(kwargs["assumed_retirement_age"]))
        return real_build(employee, history, **kwargs)

    monkeypatch.setattr(nodes, "build_member_facts", spy)
    # The rest of this test drives execute_calculations with the Article 11
    # recipe fixture already used by tests/test_calculation.py and asserts that
    # 60 appears in `priced` and that the result carries a `thresholds` entry
    # whose annual_amount exceeds the base.
```

Write that body against the fixtures in `tests/test_calculation.py`, which
already builds a spec, member facts and a recipe dict for §11-134.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thresholds.py -k priced -v`
Expected: FAIL — no `thresholds` key in the result.

- [ ] **Step 3: Implement**

In `execute_calculations`, after the scenario variants are built and before the
result is assembled, price each threshold the member is approaching — reusing
the scenario path rather than adding a second one, and skipping any boundary a
scenario already prices, so a member who asked about 60 is not told twice.

Bound it: at most the two nearest, and only those that move the figure.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/nodes.py tests/test_thresholds.py
git commit -m "Thresholds: priced by the engine that computes the answer"
```

---

### Task 4: Say it, once, without nagging

**Files:**
- Modify: `src/pension_chat/agent/blocks.py` (a renderer), `src/pension_chat/agent/presentation.py` (the slot)
- Test: `tests/test_presentation.py`

The member is told about a cliff when there is one worth telling them about, in
one line, and never as a question. `CLAUDE.md`'s voice rules stand: no offers of
further help, no closing invitation.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_presentation.py` a test that `blocks.render_thresholds`
renders one line per priced threshold naming the distance, the money and the
section, and returns `""` when there are none — the same shape as
`render_assumptions`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_presentation.py -k threshold -v`

- [ ] **Step 3: Implement**

`blocks.render_thresholds(thresholds)`, a `THRESHOLDS` slot in `COMPUTED_SLOTS`,
and the slot added to the `figure`, `projected` and `comparison` templates after
`ASSUMPTIONS`. Guidance: state it, do not offer to explore it.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/blocks.py src/pension_chat/agent/presentation.py tests/test_presentation.py
git commit -m "Answers: name the cliff the member is walking towards"
```

---

### Task 5: Measure it

**Files:**
- Create: `evals/gold_capabilities.yaml`
- Create: `docs/superpowers/baselines/2026-09-13-phase2b-capabilities.json`

- [ ] **Step 1: Write the suite**

Cases, all on `article-11-standard` (44 months from 60):

- **L11** — a plain figure question mentions the reduction ending at 60 and what
  it is worth. `mentions: ["age 60", "44 months"]`, `calculation: true`.
- **L12** — a member who has already passed every boundary is told about none of
  them. Use `article-11-near-retirement` (31 years) and `forbids` the nagging.
- **L13** — a reused recipe still names the provisions it set aside. Ask the
  same question twice in one case; the second turn expects `reuse: true` and
  `mentions: ["11-130"]`.

- [ ] **Step 2: Run it**

**Keep the machine awake.** The first full-suite attempt died when the lid
closed (`docs/superpowers/baselines/README.md`).

```bash
uv run python scripts/eval_agent.py --category capabilities --json docs/superpowers/baselines/2026-09-13-phase2b-capabilities.json
```

- [ ] **Step 3: Read the report, not the verdict**

Check the threshold figures against the statute by hand. Measured on the live
system, the standard member's age-60 cliff prices at $42,257.11 against a base
of $32,126.78 -- a difference of $10,130.33:

```
service at 60   21.5 + 44/12   = 25.17 years
unreduced       69,956.40 x 25.17 x 2.40%  = $42,255      (reported 42,257.11)
reduction       months_before_60 = 0, so none
difference      42,257.11 - 32,126.78      = $10,130.33   (reported 10,130.33)
```

Note what this corrects: the plan first predicted ~$3,970, being 11% of
$36,097.50 -- the reduction vanishing with service held constant. Waiting to 60
also adds 3.7 years of service, and the member gets both. The larger figure is
the right one, and it is the one they can act on.

- [ ] **Step 4: Commit**

```bash
git add evals/ docs/superpowers/baselines/
git commit -m "Evals: the cliffs, and the reasoning a reused recipe keeps"
```

---

## Done when

- A reused recipe's provenance names the provisions it set aside.
- A member 44 months from 60 is told so, with what it is worth, in one line.
- A member past every boundary is told about none.
- Threshold figures come from the DSL interpreter and live inside `calculation_results`.
- `uv run pytest` passes; ruff no worse than 37.

## Not in this phase

- **`lookup_recipes` plural** — nothing to return while reuse blocks a second derivation. Revisit if Task 1 shows recipes competing.
- **Ranges** — `plausible_range` fires only for assumed inputs, which a member with a record does not have. Build it when the loop makes anonymous conversations common.
- **Stand-in origins** — waiting on coverage resolving a described job inside a supposition (Phase 3).
