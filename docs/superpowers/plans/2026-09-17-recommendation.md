# Recommendation: an objective, priced candidates, and arithmetic that checks the answer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let the agent recommend — which age, which option, which order — without
ever asserting a preference the numbers do not support.

**Architecture:** A recommendation is a capability in the §11 sense: a tool, a
structured result with provenance, and a validation contract. The model proposes
an objective and picks a candidate; code prices nothing new (the scenarios are
already priced and durable) and checks the pick by arithmetic. A recommendation
that does not follow from its own objective is refused before it reaches prose.

**Tech Stack:** Python 3.12, pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-rag-first-agent-design.md` §11.1.

## Global Constraints

- **The recommendation is structured, never parsed out of prose.** It arrives
  through a tool, so the choice and the objective are data. `CLAUDE.md`'s rule
  about regexes that decide what a member meant applies doubly to one that would
  decide what the *assistant* meant.
- **Corpus-backed, evidence-based, and disclaimed.** The member's own
  requirement. The disclaimer is code-rendered, like every other block.
- **Excluded factors are named, not silently ignored.** Life expectancy, tax,
  investment return, health and risk appetite are not in the Code. The agent
  says so and stops there.
- **The member remains the decision-maker.** An objective the conversation has
  not established is offered conditionally, never chosen.
- Offline-testable throughout.

## What already exists, and the third dead renderer

Candidate pricing is built. `compute_benefit` takes `retirement_age` or
`years_from_now`, has a budget of 4, and files each result into `scenario_set`
— a durable key, `{label: {annual_amount, monthly_amount, section, …}}`, with
superseded copies kept and `NOW_LABEL` for the retire-today figure.
`graph.active_scenarios` already reads it for the composer chip.

So **the candidates for a recommendation are already computed, already
provenanced and already durable across turns.** Nothing here prices anything.

And `blocks.render_comparison` — the by-date options table — is the third
renderer found dead on the live path, after `render_worked_steps` (§9) and
`prepare_answer`'s blocks. Nothing calls it. A member who builds three scenarios
across three turns has never been shown them side by side.

---

### Task 1: An objective is a closed vocabulary, not a phrase

**Files:**
- Create: `src/pension_chat/agent/objectives.py`
- Test: `tests/test_objectives.py` (create)

**Interfaces:**
- Produces: `OBJECTIVES: dict[str, Objective]`; `EXCLUDED: dict[str, str]`;
  `objective_for(name) -> Objective | None`; `Objective.satisfied_by(choice,
  candidates) -> bool`.

**Why:** §11.1 defines recommendation as "selecting or highlighting an option
according to an explicit or inferable objective". An objective drawn from free
text cannot be checked against arithmetic; one drawn from a closed set can.

- [ ] **Step 1: Write the failing test**

```python
"""What the member can be helped to want, and what the Code cannot tell them.

An objective is what makes a recommendation checkable: "I recommend 58 because
it gives you the highest pension" is either true of the computed figures or it
is not. A free-text objective is a sentence; this is a predicate.
"""

from __future__ import annotations

from pension_chat.agent.objectives import EXCLUDED, OBJECTIVES, objective_for

_CANDIDATES = {
    "at age 58": {"annual_amount": "2850.00", "section": "11-134"},
    "at age 60": {"annual_amount": "3240.00", "section": "11-134"},
}


def test_the_highest_amount_is_the_larger_figure():
    highest = objective_for("highest_annual")
    assert highest.satisfied_by("at age 60", _CANDIDATES)
    assert not highest.satisfied_by("at age 58", _CANDIDATES)


def test_the_soonest_option_is_the_earliest_one_offered():
    """Order is the order the member built them in, which is the order they
    were computed and filed."""
    soonest = objective_for("soonest")
    assert soonest.satisfied_by("at age 58", _CANDIDATES)
    assert not soonest.satisfied_by("at age 60", _CANDIDATES)


def test_an_eligibility_objective_needs_a_section_not_a_figure():
    """"Which benefit option is available to me?" may be entirely legal. A
    recommendation is a reasoning capability that *may* invoke calculation."""
    legal = objective_for("eligibility")
    assert legal.satisfied_by("at age 58", {"at age 58": {"section": "11-134"}})
    assert not legal.satisfied_by("at age 58", {"at age 58": {}})


def test_a_numeric_objective_refuses_an_unpriced_candidate():
    """Silently dropping the candidate nobody could price would let the
    recommendation be the best of whatever happened to compute."""
    highest = objective_for("highest_annual")
    assert not highest.satisfied_by(
        "at age 58", {"at age 58": {"annual_amount": "2850.00"}, "at age 60": {}}
    )


def test_an_unknown_objective_is_not_an_objective():
    assert objective_for("whatever_feels_right") is None
    assert objective_for("") is None


def test_what_the_code_cannot_weigh_is_named_rather_than_missing():
    """The agent may say so and stop there -- but it has to be able to say it,
    which means the reasons are written down and not left to the model."""
    assert set(EXCLUDED) == {
        "life expectancy",
        "tax",
        "investment return",
        "health",
        "risk appetite",
    }
    assert all(reason for reason in EXCLUDED.values())


def test_no_objective_promises_something_excluded():
    """A bug class: an objective that needed life expectancy to evaluate would
    be checkable only by inventing one."""
    for objective in OBJECTIVES.values():
        assert objective.reads in {"annual_amount", "monthly_amount", "section", ""}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_objectives.py -q`
Expected: FAIL, module not found.

- [ ] **Step 3: Write it**

A frozen `Objective` dataclass carrying `name`, `reads` (the candidate field the
predicate looks at, `""` for order-based ones), `prose` (how the answer names
it), and `satisfied_by`. Four entries: `highest_annual`, `highest_monthly`,
`soonest`, `eligibility`. `EXCLUDED` maps each excluded factor to the reason it
is excluded, in the member's terms.

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/test_objectives.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Objectives: what makes a recommendation checkable"
```

---

### Task 2: The options a member built are shown side by side

**Files:**
- Modify: `src/pension_chat/agent/loop.py` (`_views`)
- Test: `tests/test_views.py`

**Interfaces:**
- Consumes: `state["scenario_set"]`, `graph.active_scenarios`' filtering rules.
- Produces: `views["COMPARISON"]` for a turn with two or more live options.

**Why:** `render_comparison` has never run. A member who built three scenarios
across three turns has been told about them one at a time and never shown them
together — which is also the table a recommendation has to point at.

- [ ] **Step 1: Write the failing test**

```python
def test_the_options_a_member_built_are_shown_together():
    views = _views(
        {
            "calculation_result": _RESULT,
            "scenario_set": {
                "if you retire now": {"annual_amount": "31000.00", "monthly_amount": "2583.33"},
                "at age 60": {"annual_amount": "38000.00", "monthly_amount": "3166.67"},
            },
        }
    )
    assert "31,000" in views["COMPARISON"]
    assert "38,000" in views["COMPARISON"]


def test_one_option_is_not_a_comparison():
    views = _views(
        {
            "calculation_result": _RESULT,
            "scenario_set": {"at age 60": {"annual_amount": "38000.00"}},
        }
    )
    assert "COMPARISON" not in views


def test_a_superseded_option_is_not_offered():
    """A figure the member corrected is history, not something in play -- the
    same rule `active_scenarios` applies to the composer chip."""
    ...


def test_a_tier_branch_still_wins_the_slot():
    """Both are comparisons and only one slot exists. Which provision applies
    outranks which date to pick: a member who does not know their tier cannot
    act on a date."""
    ...
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_views.py -q`
Expected: FAIL, no `COMPARISON`.

- [ ] **Step 3: Render it**

In `_views`, fall back to the scenario comparison when there is no branch
comparison, reusing `graph.active_scenarios`' filtering. Move `NOW_LABEL` and
`_SUPERSEDED` to where both callers can read them rather than importing `graph`
from `loop` — `loop` is imported *by* `graph`, so that import would cycle.

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Views: the options a member built, side by side"
```

---

### Task 3: A recommendation is checked by arithmetic before it is written

**Files:**
- Create: `src/pension_chat/agent/recommend.py`
- Test: `tests/test_recommend.py` (create)

**Interfaces:**
- Consumes: `objectives.objective_for`, `state["scenario_set"]`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class Verdict:
      ok: bool
      reason: str          # why it was refused, "" when it stands
      choice: str
      objective: str
      candidates: dict[str, dict]

  def check(*, choice: str, objective: str, candidates: Mapping[str, Mapping]) -> Verdict
  ```

**Why:** §11.1's own example. "I recommend 58 because it gives you the highest
pension" against computed values of 58 → $2,850 and 60 → $3,240 "is rejected by
arithmetic, not by style".

- [ ] **Step 1: Write the failing test**

```python
"""The recommended option has to exist, and has to be the one it claims to be.

Style cannot catch this. "I recommend 58 because it gives you the highest
pension" is a well-formed sentence whose numbers are both traceable to engine
output -- the guardrail passes it, and it is wrong.
"""

from __future__ import annotations

from pension_chat.agent.recommend import check

CANDIDATES = {
    "at age 58": {"annual_amount": "2850.00", "section": "11-134"},
    "at age 60": {"annual_amount": "3240.00", "section": "11-134"},
}


def test_the_option_that_meets_the_objective_stands():
    verdict = check(choice="at age 60", objective="highest_annual", candidates=CANDIDATES)
    assert verdict.ok
    assert verdict.reason == ""


def test_the_option_that_does_not_is_refused_by_arithmetic():
    verdict = check(choice="at age 58", objective="highest_annual", candidates=CANDIDATES)
    assert not verdict.ok
    assert "at age 60" in verdict.reason
    assert "3,240" in verdict.reason


def test_an_option_nobody_computed_cannot_be_recommended():
    verdict = check(choice="at age 62", objective="highest_annual", candidates=CANDIDATES)
    assert not verdict.ok
    assert "not among" in verdict.reason


def test_an_objective_outside_the_vocabulary_is_refused():
    """Not rudeness -- an objective the Code cannot evaluate cannot be checked,
    and an unchecked recommendation is the thing this exists to prevent."""
    verdict = check(choice="at age 60", objective="best_for_me", candidates=CANDIDATES)
    assert not verdict.ok


def test_nothing_to_choose_between_is_not_a_recommendation():
    assert not check(choice="x", objective="highest_annual", candidates={}).ok


def test_a_single_option_is_not_a_recommendation():
    """With one candidate the answer is the answer. Calling it a
    recommendation implies a comparison nobody made."""
    verdict = check(
        choice="at age 60",
        objective="highest_annual",
        candidates={"at age 60": {"annual_amount": "3240.00"}},
    )
    assert not verdict.ok
    assert "one option" in verdict.reason
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_recommend.py -q`
Expected: FAIL, module not found.

- [ ] **Step 3: Write `check`**

Refuse, in this order, each with a reason the model can act on: an unknown
objective; fewer than two candidates; a choice not among them; a choice the
objective's predicate rejects — naming the option that does satisfy it and its
figure, so the model can correct rather than guess.

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/test_recommend.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Recommend: the check that arithmetic makes, not style"
```

---

### Task 4: `recommend` is a tool, so the choice is data

**Files:**
- Modify: `src/pension_chat/agent/tools.py` (schema, handler, `run_tool`)
- Modify: `src/pension_chat/agent/loop.py` (`BUDGETS`)
- Modify: `src/pension_chat/agent/state.py`
- Test: `tests/test_recommend_tool.py` (create)

**Interfaces:**
- Consumes: `recommend.check`.
- Produces: state `recommendation: dict | None` —
  `{choice, objective, candidates, prose}` — set only when the check passed.

**Why:** the alternative is reading the recommendation back out of the answer
with a regex, which is the pattern this codebase has twice recorded as failing.
A tool makes the choice structured, and makes the refusal something the model
can act on while it still has the turn.

- [ ] **Step 1: Write the failing test**

```python
async def test_a_sound_recommendation_is_recorded():
    text, update = await run_tool(
        "recommend",
        {"choice": "at age 60", "objective": "highest_annual"},
        {"scenario_set": CANDIDATES},
    )
    assert update["recommendation"]["choice"] == "at age 60"
    assert "highest" in text


async def test_a_recommendation_the_figures_refuse_is_not_recorded():
    text, update = await run_tool(
        "recommend",
        {"choice": "at age 58", "objective": "highest_annual"},
        {"scenario_set": CANDIDATES},
    )
    assert "recommendation" not in update
    assert "at age 60" in text, "the refusal has to say what would be right"


async def test_the_tool_answers_rather_than_raising():
    """Every tool answers, even when it fails -- a tool that raises ends the
    member's turn over something the model could have recovered from."""
    text, update = await run_tool("recommend", {}, {})
    assert text
    assert update == {}


async def test_an_excluded_factor_is_named_and_refused():
    text, _ = await run_tool(
        "recommend",
        {"choice": "at age 60", "objective": "life expectancy"},
        {"scenario_set": CANDIDATES},
    )
    assert "life expectancy" in text
    assert "not in the Pension Code" in text
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_recommend_tool.py -q`
Expected: FAIL, unknown tool.

- [ ] **Step 3: Add the tool**

A `RECOMMENDATION_TOOLS` entry with `choice` and `objective` (the latter an
`enum` built from `OBJECTIVES`, so the vocabulary is offered rather than
guessed), a `_recommend` handler reading `scenario_set`, a `run_tool` branch,
and `"recommend": 2` in `BUDGETS` — two attempts, so a refusal can be acted on
once and not argued with.

Add `recommendation` to `state.py` (TypedDict, `initial_state`), durable so a
later turn can refer back to what was recommended.

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q`
Expected: PASS. `tests/test_tools_coverage.py` may assert the offered tool set;
repoint rather than delete.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Recommend: a capability, so the choice is data and not prose"
```

---

### Task 5: A recommendation says what it rests on and what it cannot weigh

**Files:**
- Modify: `src/pension_chat/agent/loop.py` (`_recommended`, `compose`)
- Test: `tests/test_recommend_block.py` (create)

**Why:** the member's requirement, in their words: recommendations "should be
corpus backed evidence based, and should give a disclaimer as well". A
disclaimer the model is asked to remember is a disclaimer that goes missing;
this is rendered by code from the same state the check wrote.

- [ ] **Step 1: Write the failing test**

```python
def test_a_recommendation_carries_its_disclaimer():
    text = _recommended({"recommendation": _SOUND})
    assert "at age 60" in text
    assert "not advice" in text.lower()


def test_it_names_what_the_code_cannot_weigh():
    """Excluded because they are not in the corpus -- and a member deciding
    when to retire deserves to know which of their real questions this cannot
    touch, rather than reading a confident answer that quietly omits them."""
    text = _recommended({"recommendation": _SOUND})
    for factor in ("life expectancy", "tax", "health"):
        assert factor in text.lower()


def test_it_names_the_objective_it_served():
    text = _recommended({"recommendation": _SOUND})
    assert "highest" in text.lower()


def test_no_recommendation_renders_nothing():
    assert _recommended({}) == ""
    assert _recommended({"recommendation": None}) == ""


async def test_compose_hands_it_over_with_the_decision_left_to_the_member():
    system = _system(await _composed({"recommendation": _SOUND}))
    joined = " ".join(system).lower()
    assert "the member decides" in joined
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_recommend_block.py -q`
Expected: FAIL, `_recommended` not defined.

- [ ] **Step 3: Write the block**

`_recommended(state)` renders the validated choice, the objective it served in
the member's words, the sections the candidates rest on, and a closing
disclaimer naming `objectives.EXCLUDED`. `compose` appends it with an
instruction that the member remains the decision-maker and that the assistant
must not press the recommendation past what the objective supports.

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q && uv run ruff check . && cd frontend && npm test`
Expected: PASS; ruff at its pre-existing 37.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Answer: a recommendation says what it served and what it cannot weigh"
```

---

## Verification

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check .` — 37, the pre-existing count, and no more
- [ ] Confirm `recommend` appears in the loop's offered tools only while its
      budget holds, like every other tool.
- [ ] Update §11 with what shipped and with the third dead renderer.

**Not in this plan, deliberately: subjects.** §13 step 8 pairs recommendation
with "subjects, comparison" — comparing two members, real or supposed, in the
same or different funds. The ledger is already keyed by subject and nothing else
is: `_available_facts`, the retrieval filter, `coverage` and `compute_benefit`
all assume one member, and the rule that makes the hypothetical path safe — a
figure is computable exactly when no input is borrowed from a record filed under
another Article — is written against that assumption. Making them
subject-aware is an architectural change, not a capability, and bolting a second
member onto a layer that starves the first one's record is how a park employee
gets told they are in Article 11. It needs its own design.
