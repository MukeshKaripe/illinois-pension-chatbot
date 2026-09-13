# Reaching the Projection Implementation Plan (Phase 2a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a future-dated question produce the future-dated figure. The projection that computes it already exists and is correct; nothing reaches it reliably, and when it does the answer leads with the wrong number.

**Architecture:** No new subsystem. `facts.build_member_facts(assumed_retirement_age=…)` recomputes every fact at a future date and `execute_calculations` already runs each scenario against it. Three defects sit around that working core — a scenario that is never recognised, a template whose lede answers a different question, and a base figure whose inputs contradict each other — plus one genuinely new thing, a salary growth rule.

**Tech Stack:** Python 3.12, `uv`, pytest (`asyncio_mode = "auto"`), ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-12-intelligent-agent-design.md` (§4, "Projection and stand-ins", as amended)

## Global Constraints

- Backend commands under `uv`. Unit tests must not touch the network.
- `uv run ruff check .` reports **37 pre-existing errors**. Add none.
- **`NEVER_ASSUMABLE` is not relaxed.** Projection is a function of a *known* value; assumption is invention from nothing. An anonymous member who never stated a salary still gets asked.
- Every figure this plan produces must live **inside `calculation_results`**, because `guardrails._collect_allowed_numbers` (`:291`) walks that structure recursively. A number placed beside it is rejected by the verifier and looks like a hallucination.
- `article-11-standard` is the measurement member: age 56, 21.5 years of service, $69,956.40 highest 4-year average.

### What the baseline established, and why this plan is small

Phase 0 ran the suites and read the reports. Projection is **not** missing:

```
L03 variant:  label "at age 58"   as_of_date 2028-05-15
              service_years 23.2   months_before_age_60 24
              annual_amount 36567.41   difference_from_now_annual 4440.63
              salary_basis "carried forward at today's level; future pay
                            rises are not projected"
```

That is the correct answer, correctly disclosed, computed by code that already
ships. Three things stop a member ever seeing it:

1. **L01 — recognition.** "If I work two more years" produced `scenario_set: {}`
   and no variants. `scenarios._OFFSET_PATTERNS` governs on `wait|after|in`
   (`scenarios.py:37`), so "work" matches nothing and the offset is never found.
2. **L03 — shape.** The variant was computed, and the answer opened *"The
   soonest option shown is an annual annuity of $32,126.78"* — the retire-now
   figure — before mentioning that 58 is better. `presentation.shape_for`
   routes any turn with a scenario to the `comparison` template, whose guidance
   is *"lede: name the range — the soonest option and the best one"*. Right for
   "compare 58, 60 and 62"; wrong for "how much at 58?".
3. **L03 — base consistency.** The base ran on `age_at_annuity_start 58` with
   `months_before_age_60 44` and `service_years 21.5`. Extraction spliced the
   age out of the message while every fact derived from it stayed at today's
   value, so a figure sits in `calculation_results` whose own inputs disagree.

Only Task 4's growth rule is new code. Everything else is routing.

---

### Task 1: Let the model say how many years

**Files:**
- Modify: `src/pension_chat/agent/understanding.py` (the `UNDERSTAND_SYSTEM` schema and `TurnUnderstanding.scenarios` passthrough)
- Modify: `src/pension_chat/agent/scenarios.py` (`corroborated`)
- Test: `tests/test_scenarios.py`

**Interfaces:**
- Produces: a scenario dict may now carry `years_from_now: int` alongside `retirement_age: int | null`. `nodes._resolve_retirement_age` already reads `years_from_now` (`nodes.py:2256`) and resolves it against the member's real age at execution time — nothing there changes.

The regex keeps its job of supplying an offset it can see. What changes is that
it stops being the *only* way one can be found. `CLAUDE.md` draws this line
already: a regex may supply data the model cannot, but it may not decide what
the member meant — and a verb list deciding that "wait two more years" is a
scenario while "work two more years" is not is exactly that decision.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scenarios.py`:

```python
def test_a_model_offset_survives_corroboration():
    """The failure L01 measured: "work two more years" names an offset no
    pattern in this module governs, so the scenario died before it existed.

    `corroborated` guards against scenarios leaking forward from earlier turns.
    It has to recognise an offset the model found, or the guard eats the fix.
    """
    from pension_chat.agent.scenarios import corroborated

    scenario = {"label": "two more years", "retirement_age": None, "years_from_now": 2}
    assert corroborated(scenario, "if I work two more years, what would my pension be?")
    # And still refuses one this message does not name at all.
    assert not corroborated(scenario, "what about my wife?")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scenarios.py -k model_offset -v`
Expected: FAIL — `corroborated` looks for an age or an offset it extracted itself, and finds neither.

- [ ] **Step 3: Implement**

In `scenarios.py`, `corroborated` currently asks whether the message names the
scenario's age or offset. Extend it to read `years_from_now` from the scenario
as well as from its own extraction, so a model-supplied offset counts as named
when the digits or the number-word appear in the message. The existing
`_names_number`-style helper at `scenarios.py:96` already does the digit and
word matching; pass it `scenario.get("years_from_now")` when
`retirement_age` is absent.

In `understanding.py`, add the field to the schema in `UNDERSTAND_SYSTEM`:

```
 "scenarios": [{"label": "...", "retirement_age": <int|null>,
                "years_from_now": <int|null>, "basis": "..."}],
```

and to the rules, so the model knows which to fill:

```
- A scenario names either an absolute retirement age ("at 58") or an offset
  from today ("two more years", "if I keep working another decade"). Put an
  absolute age in retirement_age and an offset in years_from_now, and never
  guess the member's current age to convert one into the other -- you have not
  been told it, and it is resolved for you later.
```

`_as_dict_list` already passes unknown keys through, so `TurnUnderstanding.scenarios` needs no change.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_scenarios.py tests/test_understanding.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/understanding.py src/pension_chat/agent/scenarios.py tests/test_scenarios.py
git commit -m "Scenarios: an offset the model found counts as one"
```

---

### Task 2: One scenario is not a comparison

**Files:**
- Modify: `src/pension_chat/agent/presentation.py` (`shape_for`, and a new template)
- Test: `tests/test_presentation.py`

**Interfaces:**
- Consumes: `presentation.scenarios_this_turn(state)` (existing).
- Produces: `shape_for` returns `"projected"` for a turn with exactly one scenario, and `"comparison"` for two or more. A new `TEMPLATES["projected"]` entry.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_presentation.py`:

```python
def test_one_scenario_is_not_a_comparison():
    """L03's answer opened "The soonest option shown is $32,126.78" -- the
    retire-now figure -- to a member who asked what they would get at 58.

    The comparison template's lede is written to name a range, which is right
    for "compare 58, 60 and 62" and wrong for a single named age: the first
    number the member reads answers a question they did not ask.
    """
    from pension_chat.agent import presentation
    from pension_chat.agent.worksheet import WorkSheet

    sheet = WorkSheet.from_state({})
    one = {"scenarios": [{"label": "at age 58", "retirement_age": 58}]}
    two = {
        "scenarios": [
            {"label": "at age 58", "retirement_age": 58},
            {"label": "at age 60", "retirement_age": 60},
        ]
    }

    assert presentation.shape_for(sheet, one) == "projected"
    assert presentation.shape_for(sheet, two) == "comparison"
    assert "projected" in presentation.TEMPLATES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_presentation.py -k not_a_comparison -v`
Expected: FAIL — `shape_for` returns `"comparison"` for both.

- [ ] **Step 3: Implement**

In `presentation.py`, replace the first branch of `shape_for`:

```python
    scenarios = scenarios_this_turn(state)
    if len(scenarios) > 1:
        return "comparison"
    if scenarios:
        # A single named date is a question about that date, not a survey. The
        # comparison lede names "the soonest option and the best one", which
        # puts the retire-now figure in front of a member who asked about 58.
        return "projected"
```

and add the template beside the others:

```python
    "projected": Template(
        shape="projected",
        slots=("STATUS", "lede", "WORKED_STEPS", "why", "ASSUMPTIONS"),
        guidance=(
            "lede: the amount at the date they asked about, annual first with "
            "monthly in brackets, and the date it assumes. One or two "
            "sentences. Never open with today's figure -- they did not ask for "
            "it. If it is worth saying what waiting gains them, say it second, "
            "and copy difference_from_now_annual rather than subtracting two "
            "amounts yourself.\n"
            "why: why this provision governs at that date, in the Code's own "
            "terms. Two sentences at most."
        ),
    ),
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_presentation.py tests/test_templates.py tests/test_answer_style.py -v`
Expected: PASS. If `test_templates.py` enumerates shapes, add `projected` there too.

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/presentation.py tests/test_presentation.py
git commit -m "Presentation: a single named date gets its own shape"
```

---

### Task 3: A base figure whose inputs agree with each other

**Files:**
- Modify: `src/pension_chat/agent/nodes.py::resolve_inputs` (the extraction merge, around `:1078-1100`)
- Test: `tests/test_input_origins.py`

**Interfaces:**
- Consumes: `facts.build_member_facts`, `nodes._resolve_retirement_age` (existing).
- Produces: no new API. When extraction supplies a fact that other record-derived facts are computed *from*, the dependents are recomputed rather than left at today's values.

L03's base ran on `age_at_annuity_start 58` with `months_before_age_60 44` —
the reduction for a 56-year-old. Whether or not the prose quotes it, that figure
is in `calculation_results`, which means the guardrail will admit its digits and
the calculation card may show them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_input_origins.py`:

```python
async def test_an_extracted_age_moves_the_facts_derived_from_it(monkeypatch):
    """L03's base paired age 58 with months_before_age_60 44.

    44 months before 60 is a member of 56. Splicing one fact out of the message
    while every fact computed from it stays at today's value produces a figure
    whose own inputs contradict each other.
    """
    from pension_chat.agent.nodes import resolve_inputs

    state = {
        "question": "I'm thinking of retiring at 58. How much would I get?",
        "messages": [],
        "recipes": {
            "retirement_annuity": {
                "spec": {
                    "inputs": [
                        {"name": "age_at_annuity_start", "type": "number", "required": True},
                        {"name": "months_before_age_60", "type": "number", "required": True},
                        {"name": "service_years", "type": "number", "required": True},
                    ]
                }
            }
        },
        "member_facts": {
            "age": 56,
            "age_at_annuity_start": 56,
            "months_before_age_60": 44,
            "service_years": 21.5,
        },
    }

    async def fake_complete(**kwargs):
        return '{"resolved": {"age_at_annuity_start": 58}, "unresolved": []}'

    monkeypatch.setattr("pension_chat.agent.nodes.complete", fake_complete)

    out = await resolve_inputs(state)
    resolved = out["resolved_inputs"]

    assert str(resolved["age_at_annuity_start"]) == "58"
    assert str(resolved["months_before_age_60"]) != "44", (
        "months before 60 must move when the annuity start age does"
    )
    assert float(resolved["months_before_age_60"]) == 24.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_input_origins.py -k extracted_age -v`
Expected: FAIL — `months_before_age_60` is still 44.

- [ ] **Step 3: Implement**

In `resolve_inputs`, after the extraction merge loop and before `overrides` is
built: when the extracted values include an annuity start age that differs from
the record's, rebuild the record-derived facts at that age and let them replace
the today-derived ones — while values the member stated themselves still win,
which is the same precedence `execute_calculations` already uses for scenario
variants (`nodes.py:1508`).

```python
    # An age the member named is not one fact among several: months before 60,
    # service and the salary window are all computed *from* it. Splicing the age
    # in and leaving its dependents at today's values is how a base figure came
    # to pair age 58 with the reduction for a 56-year-old.
    named_age = taken.get("age_at_annuity_start")
    employee = state.get("employee_record")
    if named_age is not None and employee and not _coverage_elsewhere(state):
        try:
            at_age = build_member_facts(
                employee,
                [
                    (date.fromisoformat(h["effective_date"]), Decimal(str(h["annual_salary"])))
                    for h in (state.get("salary_history") or [])
                ],
                assumed_retirement_age=int(named_age),
            )
        except (TypeError, ValueError):
            at_age = {}
        for name in declared:
            # Only the facts the record supplied. Anything the member stated
            # stays theirs.
            if name in from_record and name not in taken and name in at_age:
                resolved[name] = at_age[name]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_input_origins.py tests/test_worksheet.py tests/test_calculation.py -v`
Expected: PASS

- [ ] **Step 5: Full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/pension_chat/agent/nodes.py tests/test_input_origins.py
git commit -m "Inputs: a named retirement age moves what is derived from it"
```

---

### Task 4: The salary growth rule

**Files:**
- Modify: `src/pension_chat/agent/facts.py` (`build_member_facts`)
- Test: `tests/test_facts.py`

**Interfaces:**
- Produces: `build_member_facts(..., salary_growth: Decimal | None = None)`. `None` keeps today's behaviour exactly — salary carried forward flat, which is what `salary_basis` already discloses. A rate compounds the projected salary over the years between today and the assumed retirement age.

This is the one genuinely new capability. L01 turn 2 measured its absence: "assume I get a 3% raise each year" returned the same figure and said outright that it "does not produce a new pension figure here."

- [ ] **Step 1: Write the failing test**

Append to `tests/test_facts.py`:

```python
def test_a_growth_rate_raises_the_projected_salary():
    """Flat is the honest default and stays the default. But a member who tells
    you their raises is supplying a known rule, not asking you to invent one --
    and "assume 3% a year" came back with the same figure and a sentence saying
    it changed nothing."""
    flat = build_member_facts(EMPLOYEE, HISTORY, today=TODAY, assumed_retirement_age=60)
    grown = build_member_facts(
        EMPLOYEE, HISTORY, today=TODAY, assumed_retirement_age=60,
        salary_growth=Decimal("0.03"),
    )

    assert Decimal(str(grown["final_average_salary"])) > Decimal(str(flat["final_average_salary"]))
    assert "3" in str(grown["salary_basis"]), "the rule used must be disclosed"


def test_no_growth_rate_is_the_flat_projection_unchanged():
    a = build_member_facts(EMPLOYEE, HISTORY, today=TODAY, assumed_retirement_age=60)
    b = build_member_facts(
        EMPLOYEE, HISTORY, today=TODAY, assumed_retirement_age=60, salary_growth=None
    )
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_facts.py -k growth -v`
Expected: FAIL — `build_member_facts() got an unexpected keyword argument 'salary_growth'`

- [ ] **Step 3: Implement**

Add the parameter, compound the salary rows forward by `(1 + rate) ** years` between `today` and `as_of` before the 4-consecutive-year average is taken, and set `salary_basis` to name the rate — *"carried forward at 3% a year, as you asked"* — so the disclosure says which rule produced the figure. Leave every other fact untouched: growth moves salary, not service or age.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_facts.py tests/test_calculation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pension_chat/agent/facts.py tests/test_facts.py
git commit -m "Facts: a growth rate the member supplied, disclosed with the figure"
```

---

### Task 5: Measure it

**Files:**
- Modify: `evals/gold_no_ask.yaml`, `evals/gold_goal_completion.yaml` (statuses and gaps)
- Create: `docs/superpowers/baselines/2026-09-13-phase2a-<category>.json`

- [ ] **Step 1: Re-run the two suites**

**Keep the machine awake** — the Phase 0 full-suite run died when the lid closed
(`docs/superpowers/baselines/README.md`).

```bash
uv run python scripts/eval_agent.py --category no_ask --json docs/superpowers/baselines/2026-09-13-phase2a-no-ask.json
uv run python scripts/eval_agent.py --category goal_completion --json docs/superpowers/baselines/2026-09-13-phase2a-goal-completion.json
```

- [ ] **Step 2: Flip what now passes**

L01 and L03 should pass. **Verify from the report, not the verdict** — L03
passed once already while computing on 21.5 years, which is why it now asserts
`uses_input: {service_years: 23.5}`. Check the base inputs are internally
consistent and the variant is what the prose leads with.

For each case that now passes, change `status: expected_fail` to `status: active`
and delete its `gap:`. For any that still fails, rewrite its `gap:` to what the
report actually shows — the rule that made Phase 0 worth running.

- [ ] **Step 3: Full suite and lint**

```bash
uv run pytest -q
uv run ruff check .
```
Expected: all pass; no more than 37 ruff errors.

- [ ] **Step 4: Commit**

```bash
git add evals/ docs/superpowers/baselines/
git commit -m "Phase 2a: measured, and the cases that now pass say so"
```

---

## Done when

- L01 and L03 pass, with `service_years` 23.5 in the base and the prose leading with the figure for the date asked about.
- "Assume I get a 3% raise each year" produces a different figure and says which rule produced it.
- `uv run pytest` passes; ruff no worse than 37.
- Every figure still lives inside `calculation_results`.

## Not in this phase

- **`lookup_recipes`, thresholds, ranges** — Phase 2b, the output side. This plan is the input side only.
- **`stand_in` origins** — they need coverage to resolve a described job inside a supposition, which L08 showed is missing (`frame.article` was `None`). That is Phase 3's, and the stand-in work follows it.
- **An `input_origin` expectation key** — `run_turn` still does not expose `input_sources`. Add it with Phase 2b, where `projected` and `stand_in` both need asserting.
