# Assumptions: the provenance ladder, ranks 3 and 4

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop the agent saying "I could not establish" about things it can
derive from what the member said or cite from the Code.

**Architecture:** Two new provenance ranks between "read off the record" and
"filled in". Rank 3 (`derived`) is a value computed from facts already held —
the months before 60 from a stated age. Rank 4 (`statutory_parameter`) is a
value the Code itself states, cited to the section that states it. Both fill a
gap that today blocks a figure. A gap in a *routing* field is not filled at all:
it branches, and both branches are computed and compared.

**Tech Stack:** Python 3.12, pytest, SQLAlchemy 2.0, pydantic.

**Spec:** `docs/superpowers/specs/2026-09-16-rag-first-agent-design.md` §7.

## Global Constraints

- **The model never declares its own provenance.** Every rank is assigned in
  code, at resolution time, by the same functions the calculation card reads.
  (`nodes._input_origins`, existing rule.)
- **Never invented.** `assumption_policy.NEVER_ASSUMABLE` stays the floor: no
  rank may supply a salary, a service figure, a tier or an Article.
- **Listed, never pattern-matched.** Any new name→meaning table follows
  `thresholds._BOUNDARY_FACTS`: explicit entries with a comment saying why a
  general rule would be wrong.
- `assumption_ceiling` stays at 2 and continues to count *invented* fills only.
- Offline-testable throughout. No task needs the index, the models or the DB.

## What the spec asked for, and what is already built

§7 describes statutory parameters as a registry to be built: "discovered from
retrieved text, persisted, reusable, citable. Built on `formula_registry`'s
lifecycle."

**It already exists.** `dsl_types.ParameterSpec` is a value plus a
`source: {section, subsection}`; `derivation.py` refuses any constant without
one ("Every numeric constant carries a citation"); `validator._check_citations`
refuses any section that was not retrieved; `formula_registry.rule_specification`
persists them with the recipe's full lifecycle; `thresholds.py` already reads
them back by name (`age_60`, `minimum_age`, `minimum_service_years`) with their
sections attached.

What is missing is narrow: those parameters are usable only *inside* the
recipe's own calculation tree. They cannot fill a **missing input**. Task 3 is
therefore a resolver over evidence already on hand, not a new table, a new
migration, a new regex or a new model call.

---

### Task 1: The ladder gains a rank

**Files:**
- Modify: `src/pension_chat/agent/worksheet.py`
- Modify: `src/pension_chat/agent/ledger.py`
- Test: `tests/test_provenance_ladder.py` (create)

**Interfaces:**
- Produces: `worksheet.SOURCE_STATUTORY = "statutory_parameter"`; a five-entry
  `ledger.PRECEDENCE`.

- [ ] **Step 1: Write the failing test**

```python
"""The ladder: stated -> record -> derived -> statutory_parameter -> assumed.

Ranks 3 and 4 are where the agent used to give up. A retirement date worked out
from an age the member gave is not an assumption, and an Article's own normal
retirement age is citable rather than guessed -- so neither belongs at the
bottom of the ladder with the values nobody supplied.
"""

from __future__ import annotations

from pension_chat.agent import ledger
from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_DERIVED,
    SOURCE_RECORD,
    SOURCE_STATED,
    SOURCE_STATUTORY,
    USABLE_SOURCES,
)


def test_the_ladder_runs_from_the_member_to_the_code_to_the_gap():
    assert ledger.PRECEDENCE == (
        SOURCE_STATED,
        SOURCE_RECORD,
        SOURCE_DERIVED,
        SOURCE_STATUTORY,
        SOURCE_ASSUMED,
    )


def test_the_code_outranks_a_guess():
    """A cited age from the Article beats one we filled in, and loses to one
    worked out from what the member actually told us."""
    assert ledger.outranks(SOURCE_STATUTORY, SOURCE_ASSUMED)
    assert not ledger.outranks(SOURCE_STATUTORY, SOURCE_DERIVED)


def test_a_figure_may_rest_on_a_statutory_parameter():
    assert SOURCE_STATUTORY in USABLE_SOURCES


def test_an_unknown_source_still_sorts_last():
    assert ledger.rank("something-new") > ledger.rank(SOURCE_ASSUMED)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_provenance_ladder.py -q`
Expected: FAIL, `ImportError: cannot import name 'SOURCE_STATUTORY'`.

- [ ] **Step 3: Add the rank**

In `worksheet.py`, beside the other source constants:

```python
#: A value the Code itself states -- a rate, a threshold, a normal retirement
#: age -- carried with the section that states it. Not an assumption: nobody
#: filled it in, and the member can go and read it. It ranks below `derived`
#: because a value worked out from what this member told us is about *them*,
#: where a statutory parameter is about everyone the provision reaches.
SOURCE_STATUTORY = "statutory_parameter"
```

Add `SOURCE_STATUTORY` to `USABLE_SOURCES`. Leave `_NOT_DURABLE` alone: a
statutory parameter is as durable as the Code, which is the point.

In `ledger.py`, import it and place it in `PRECEDENCE` between `SOURCE_DERIVED`
and `SOURCE_ASSUMED`.

- [ ] **Step 4: Run the test and the ledger suite**

Run: `uv run pytest tests/test_provenance_ladder.py tests/test_ledger.py tests/test_ledger_writes.py tests/test_available_facts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Ledger: the Code is a source, ranked between derivation and a guess"
```

---

### Task 2: A fill worked out from known facts is derivation, not assumption

**Files:**
- Modify: `src/pension_chat/calculation/assumption_policy.py`
- Modify: `src/pension_chat/agent/nodes.py` (`apply_assumptions`, its one caller)
- Test: `tests/test_assumption_ranks.py` (create)

**Interfaces:**
- Consumes: `SOURCE_STATUTORY` from Task 1.
- Produces: `AssumptionRule.derives: bool`; `apply_assumptions` returns
  `(filled, origins, still_missing)` where `origins: dict[str, str]` maps each
  filled name to its rank.

**Why:** `months_before_age_60` is computed from an age the member stated. It is
recorded as `assumed` and it spends one of the two ceiling slots, so a member
who states their age can be refused a figure for want of a second slot — having
supplied the very fact the value comes from.

- [ ] **Step 1: Write the failing test**

```python
"""What is worked out is not what is guessed.

The ceiling exists to stop the agent inventing a member. A value computed from
facts that member supplied invents nothing, so it does not spend the ceiling and
it is not labelled as an assumption on the calculation card.
"""

from __future__ import annotations

from pension_chat.agent.nodes import apply_assumptions
from pension_chat.agent.worksheet import SOURCE_ASSUMED, SOURCE_DERIVED, WorkSheet


def _sheet() -> WorkSheet:
    return WorkSheet(facts={}, figures={}, turn=1)


def _ask(name: str) -> dict:
    return {"name": name, "type": "integer", "description": name, "ask_for": name}


def test_a_value_computed_from_a_stated_age_is_derived():
    sheet = _sheet()
    filled, origins, missing = apply_assumptions(
        sheet, [_ask("months_before_age_60")], {"age": 56}, 2
    )
    assert filled["months_before_age_60"] == 42
    assert origins["months_before_age_60"] == SOURCE_DERIVED
    assert missing == []


def test_taking_retire_now_as_today_is_still_an_assumption():
    """Nothing the member said picks today. It is a framing choice, and the
    answer has to say so."""
    sheet = _sheet()
    _, origins, _ = apply_assumptions(
        sheet, [_ask("withdrawal_date")], {"as_of_date": "2026-09-16"}, 2
    )
    assert origins["withdrawal_date"] == SOURCE_ASSUMED


def test_a_derivation_does_not_spend_the_ceiling():
    """Three fills under a ceiling of two: the derivation is free, so both
    assumptions still land."""
    sheet = _sheet()
    filled, origins, missing = apply_assumptions(
        sheet,
        [_ask("months_before_age_60"), _ask("withdrawal_date"), _ask("annuity_start_date")],
        {"age": 56, "as_of_date": "2026-09-16"},
        2,
    )
    assert set(filled) == {"months_before_age_60", "withdrawal_date", "annuity_start_date"}
    assert missing == []
    assert sum(1 for s in origins.values() if s == SOURCE_ASSUMED) == 2


def test_the_ceiling_still_bites_invented_values():
    sheet = _sheet()
    filled, _, missing = apply_assumptions(
        sheet,
        [_ask("withdrawal_date"), _ask("annuity_start_date")],
        {"as_of_date": "2026-09-16"},
        1,
    )
    assert len(filled) == 1
    assert [m["name"] for m in missing] == ["annuity_start_date"]


def test_a_derivation_is_not_reported_as_an_assumption_to_the_member():
    sheet = _sheet()
    apply_assumptions(sheet, [_ask("months_before_age_60")], {"age": 56}, 2)
    assert "months_before_age_60" not in sheet.assumed_facts()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_assumption_ranks.py -q`
Expected: FAIL — `apply_assumptions` returns a 2-tuple.

- [ ] **Step 3: Mark the deriving rules, and let them through free**

In `assumption_policy.py`, add to `AssumptionRule`:

```python
    #: True when `default` computes the value from facts already held rather
    #: than choosing one. A derivation invents nothing, so it does not spend
    #: the ceiling and it is not disclosed as an assumption -- disclosing it
    #: tells a member we guessed at something they told us themselves.
    derives: bool = False
```

Set `derives=True` on `months_before_age_60` only. `withdrawal_date` and
`annuity_start_date` choose today; they stay assumptions.

In `nodes.apply_assumptions`, count the ceiling against invented fills only and
return the origin of each:

```python
    filled: dict[str, Any] = {}
    origins: dict[str, str] = {}
    still_missing: list[dict[str, Any]] = []
    invented = 0

    for item in missing:
        name = str(item.get("name") or "")
        rule = rule_for(name)
        derives = bool(rule and rule.derives)
        if not is_assumable(name) or (not derives and invented >= ceiling):
            still_missing.append(item)
            continue
        value = default_for(name, known)
        if value is None:
            still_missing.append(item)
            continue
        source = SOURCE_DERIVED if derives else SOURCE_ASSUMED
        basis = rule.basis if rule else ""
        sheet.record_fact(name, value, source, basis=basis)
        filled[name] = value
        origins[name] = source
        if not derives:
            invented += 1

    return filled, origins, still_missing
```

Update the caller in `resolve_inputs` to unpack three values, and pass the new
`origins` into `_input_origins` as a new keyword `filled_origins`, replacing the
`assumed` lookup:

```python
    assumed, assumed_origins, still_missing = apply_assumptions(
        sheet, filtered, facts, settings_ceiling
    )
    resolved.update(assumed)
```

In `_input_origins`, change the `assumed` parameter to `filled_origins:
dict[str, str]` and the first branch to:

```python
        if name in filled_origins:
            origins[name] = filled_origins[name]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_assumption_ranks.py -q && uv run pytest -q`
Expected: PASS. Tests that assert `assumed` for `months_before_age_60` are
**repointed, not deleted** — the value is derived and saying so is the fix, not
a regression. Note the change in each docstring.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Assumptions: what is worked out from a stated fact is not a guess"
```

---

### Task 3: A parameter the Code states can fill a gap, with its section

**Files:**
- Create: `src/pension_chat/calculation/statutory.py`
- Modify: `src/pension_chat/agent/nodes.py` (`resolve_inputs`)
- Modify: `src/pension_chat/agent/state.py`
- Test: `tests/test_statutory_parameters.py` (create)

**Interfaces:**
- Consumes: `SOURCE_STATUTORY` (Task 1); `apply_assumptions`'s origins (Task 2).
- Produces:
  ```python
  @dataclass(frozen=True)
  class StatutoryFill:
      name: str          # the member fact this supplies
      value: Any
      parameter: str     # the recipe parameter it came from
      section: str | None
      subsection: str | None
      # `.basis` is a property: prose for the answer block

  def fills_from(recipes: dict[str, dict]) -> dict[str, StatutoryFill]
  ```

- [ ] **Step 1: Write the failing test**

```python
"""An Article's own thresholds are citable, not guessable.

A recipe's `parameters` already carry `{value, source: {section, subsection}}`,
because derivation refuses a constant without a citation and the validator
refuses a section that was never retrieved. So a normal retirement age of 60 is
sitting in the recipe with 11-134 attached to it -- while the member is told the
agent could not establish a retirement age.
"""

from __future__ import annotations

from pension_chat.calculation.statutory import FILLS, fills_from

RECIPE = {
    "spec": {
        "parameters": {
            "age_60": {"value": 60, "source": {"section": "11-134", "subsection": "(a)"}},
            "reduction_rate": {"value": 0.0025, "source": {"section": "11-134"}},
        }
    }
}


def test_a_retirement_age_the_code_states_can_fill_a_missing_age():
    fills = fills_from({"retirement_annuity": RECIPE})
    assert fills["age_at_annuity_start"].value == 60
    assert fills["age_at_annuity_start"].section == "11-134"
    assert fills["age_at_annuity_start"].subsection == "(a)"


def test_a_rate_is_not_a_fact_about_the_member():
    """`reduction_rate` is arithmetic the recipe does for itself. Offering it as
    a member fact is how "maximum_salary_percentage" would become a salary."""
    fills = fills_from({"retirement_annuity": RECIPE})
    assert "reduction_rate" not in {f.parameter for f in fills.values()}


def test_a_parameter_with_no_citation_is_refused():
    """The whole point is that the member can go and read it."""
    fills = fills_from({"x": {"spec": {"parameters": {"age_60": {"value": 60}}}}})
    assert fills == {}


def test_the_code_never_supplies_a_salary_or_a_service_figure():
    """The Code states rates and periods. It does not contain this member."""
    from pension_chat.calculation.assumption_policy import NEVER_ASSUMABLE

    assert not set(FILLS.values()) & NEVER_ASSUMABLE


def test_no_recipes_means_no_fills():
    assert fills_from({}) == {}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_statutory_parameters.py -q`
Expected: FAIL, module not found.

- [ ] **Step 3: Write the resolver**

```python
"""Values the Code states, offered as answers to questions about the member.

A recipe's `parameters` are already the Code's own numbers with the section that
states each one: `derivation.py` refuses a constant with no citation, and
`validator._check_citations` refuses a citation to a section nobody retrieved.
This module does one thing with them -- decides which of them are also *facts
about a member*, so a missing input can be filled from the statute instead of
blocking a figure or being guessed at.

Listed rather than pattern-matched, for the reason `thresholds._BOUNDARY_FACTS`
gives: "maximum_salary_percentage" looks exactly like a member fact to any rule
general enough to catch the real ones. A rate, a cap or a percentage is the
recipe's own arithmetic and is never offered here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: recipe parameter name -> the member fact it may supply.
FILLS: dict[str, str] = {
    "age_60": "age_at_annuity_start",
    "age_62": "age_at_annuity_start",
    "age_65": "age_at_annuity_start",
    "age_67": "age_at_annuity_start",
    "minimum_age": "age_at_annuity_start",
    "minimum_retirement_age": "age_at_annuity_start",
    "normal_retirement_age": "age_at_annuity_start",
}


@dataclass(frozen=True)
class StatutoryFill:
    """One value the Code states, and where the member can read it."""

    name: str
    value: Any
    parameter: str
    section: str | None
    subsection: str | None

    @property
    def basis(self) -> str:
        where = f"Section {self.section}" if self.section else "the Code"
        if self.subsection:
            where += f"{self.subsection}"
        return f"the age this Article sets for a full annuity, stated in {where}"


def fills_from(recipes: dict[str, dict]) -> dict[str, StatutoryFill]:
    """Member facts the resolved recipes' own cited parameters can supply.

    First one wins, so a recipe that names a minimum age is not overwritten by
    a later one for a different formula type.
    """
    found: dict[str, StatutoryFill] = {}
    for recipe in (recipes or {}).values():
        parameters = ((recipe or {}).get("spec") or {}).get("parameters") or {}
        for parameter, spec in parameters.items():
            name = FILLS.get(parameter)
            if name is None or name in found:
                continue
            value = (spec or {}).get("value")
            source = (spec or {}).get("source") or {}
            section = source.get("section")
            if value is None or not section:
                # Uncited, so the member cannot check it. That is exactly the
                # class of value this rank exists to be better than.
                continue
            found[name] = StatutoryFill(
                name=name,
                value=value,
                parameter=parameter,
                section=section,
                subsection=source.get("subsection"),
            )
    return found
```

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/test_statutory_parameters.py -q`
Expected: PASS.

- [ ] **Step 5: Wire it into resolution, ahead of assumption**

In `nodes.resolve_inputs`, between `sheet.filter_asks(missing)` and
`apply_assumptions`:

```python
    # The Code before the guess. A threshold this Article states is citable, so
    # it fills a gap at its own rank rather than spending the assumption
    # ceiling -- and the answer names the section instead of hedging.
    statutory = statutory_fills(recipes)
    cited: dict[str, Any] = {}
    cited_origins: dict[str, str] = {}
    outstanding_asks: list[dict[str, Any]] = []
    for item in filtered:
        fill = statutory.get(str(item.get("name") or ""))
        if fill is None:
            outstanding_asks.append(item)
            continue
        sheet.record_fact(fill.name, fill.value, SOURCE_STATUTORY, basis=fill.basis)
        cited[fill.name] = fill.value
        cited_origins[fill.name] = SOURCE_STATUTORY
    resolved.update(cited)

    assumed, assumed_origins, still_missing = apply_assumptions(
        sheet, outstanding_asks, {**facts, **cited}, settings_ceiling
    )
```

Then `filled_origins={**cited_origins, **assumed_origins}` into `_input_origins`,
and add to the returned dict:

```python
        "statutory_fills": [
            {
                "name": f.name,
                "value": f.value,
                "section": f.section,
                "subsection": f.subsection,
                "basis": f.basis,
            }
            for f in statutory.values()
            if f.name in cited
        ],
```

Add `statutory_fills` to `state.py`'s TypedDict, to `PER_TURN_KEYS`, and to
`initial_state()`.

- [ ] **Step 6: Test the wiring**

Add to `tests/test_statutory_parameters.py`:

```python
async def test_a_cited_threshold_fills_the_gap_instead_of_blocking():
    """The whole point, end to end: the input resolves, it is not reported
    missing, and it is not counted as an assumption."""
    from pension_chat.agent import nodes

    state = {
        "question": "what would my annuity be?",
        "messages": [],
        "requires_calculation": True,
        "recipes": {
            "retirement_annuity": {
                "spec": {
                    "parameters": RECIPE["spec"]["parameters"],
                    "inputs": [
                        {"name": "age_at_annuity_start", "type": "integer", "required": True}
                    ],
                },
            }
        },
        "turn_index": 1,
    }
    out = await nodes.resolve_inputs(state)

    assert out["resolved_inputs"]["age_at_annuity_start"] == 60
    assert out["missing_inputs"] == []
    assert out["input_sources"]["age_at_annuity_start"] == "statutory_parameter"
    assert out["statutory_fills"][0]["section"] == "11-134"
```

Run: `uv run pytest tests/test_statutory_parameters.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "Statutory parameters: the Code's own thresholds fill a gap, with the section"
```

---

### Task 4: A gap is either about a value or about which law applies

**Files:**
- Create: `src/pension_chat/agent/gaps.py`
- Test: `tests/test_gaps.py` (create)

**Interfaces:**
- Produces: `GOVERNING: frozenset[str]`; `classify(name) -> str` returning
  `"governing"` or `"value"`; `split(missing) -> tuple[list, list]`.

**Why:** the spec extends an existing rule from conflicts to absences — "a
conflict in a routing field is a question; a conflict in a value stays an
override-with-note". The same must hold when the field is simply absent.

- [ ] **Step 1: Write the failing test**

```python
"""Which kind of gap this is decides what to do about it.

A value gap changes the number: fill it, say so, carry on. A governing gap
changes *which provision applies*, so filling it silently answers a question
about a member who may not exist. Those get asked about -- or computed both
ways and compared.
"""

from __future__ import annotations

from pension_chat.agent.gaps import GOVERNING, classify, split


def test_a_salary_gap_changes_the_number():
    assert classify("final_average_salary") == "value"
    assert classify("service_years") == "value"


def test_a_tier_gap_changes_which_law_applies():
    assert classify("tier") == "governing"
    assert classify("article_number") == "governing"
    assert classify("employment_status") == "governing"


def test_an_unknown_input_is_treated_as_a_value():
    """A routing field is a short, known list. Guessing that something
    unrecognised routes would turn every new recipe input into a question."""
    assert classify("months_before_age_60") == "value"


def test_the_routing_fields_are_the_ones_the_record_is_keyed_on():
    """Kept in step with the conflict rule in `coverage.py`, which treats these
    same fields as questions rather than overrides."""
    assert GOVERNING == frozenset({"article_number", "employment_status", "tier"})


def test_every_routing_field_is_one_nobody_may_assume():
    """The two tables must not drift: a field that routes and is assumable
    would be silently filled in before anyone thought to ask."""
    from pension_chat.calculation.assumption_policy import NEVER_ASSUMABLE

    assert GOVERNING <= NEVER_ASSUMABLE


def test_split_separates_the_two_kinds():
    governing, value = split(
        [{"name": "tier"}, {"name": "final_average_salary"}, {"name": "service_years"}]
    )
    assert [g["name"] for g in governing] == ["tier"]
    assert [v["name"] for v in value] == ["final_average_salary", "service_years"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_gaps.py -q`
Expected: FAIL, module not found.

- [ ] **Step 3: Write it**

```python
"""What kind of gap this is, which decides what to do about it.

`coverage.py` already draws this line for *conflicts*: "a conflict in a routing
field (`article_number`, `employment_status`) is a question; a conflict in a
value (salary, service) stays an override-with-note". An absence is the same
distinction with nothing on one side, so it gets the same answer.

`tier` joins the routing fields here because it keys the recipe registry --
`registry.lookup_recipe(formula_type, article, tier)` -- so an unknown tier is
not a missing number, it is two different provisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: Fields that decide *which* provision applies rather than what it computes.
GOVERNING = frozenset({"article_number", "employment_status", "tier"})

VALUE = "value"
GOVERNING_GAP = "governing"


def classify(name: str) -> str:
    """Whether this gap changes the law that applies, or only the number."""
    return GOVERNING_GAP if name in GOVERNING else VALUE


def split(
    missing: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``missing`` in two lists, governing first, each keeping its order."""
    governing: list[dict[str, Any]] = []
    value: list[dict[str, Any]] = []
    for item in missing:
        target = (
            governing
            if classify(str(item.get("name") or "")) == GOVERNING_GAP
            else value
        )
        target.append(dict(item))
    return governing, value
```

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/test_gaps.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Gaps: an absent routing field is a question, an absent value is a note"
```

---

### Task 5: The gap count is a contract on the calculation, not a mood

**Files:**
- Modify: `src/pension_chat/agent/nodes.py` (`gap_verdict`, `resolve_inputs`)
- Modify: `src/pension_chat/agent/state.py`
- Modify: `src/pension_chat/agent/tools.py` (`_describe`)
- Test: `tests/test_gap_contract.py` (create)

**Interfaces:**
- Consumes: `gaps.split` (Task 4), the filled origins (Tasks 2 and 3).
- Produces: `nodes.gap_verdict(missing, *, filled) -> str`, one of
  `"compute"`, `"compute_with_assumptions"`, `"worked_example"`, `"ask"`.

**Why:** §7's table. Today `_describe` says "Could not compute: missing X. Ask
the member" for every shape of gap, which is the sentence behind a third of the
hedges.

- [ ] **Step 1: Write the failing test**

```python
"""Four gap states, four behaviours -- decided by counting, not by the model.

"Assumptions are counted, never declared. Their number and identity come from
ledger provenance, so 'what I assumed' cannot be wrong and the ceiling cannot be
evaded."
"""

from __future__ import annotations

from pension_chat.agent.nodes import gap_verdict


def test_nothing_missing_computes():
    assert gap_verdict([], filled={}) == "compute"


def test_two_filled_value_gaps_compute_and_say_so():
    assert (
        gap_verdict([], filled={"withdrawal_date": "assumed", "months_before_age_60": "derived"})
        == "compute_with_assumptions"
    )


def test_a_value_gap_nobody_can_fill_becomes_a_worked_example():
    """Not a refusal. The Code states the formula even when it does not state
    this member, so the answer shows the shape and what would make it theirs."""
    assert gap_verdict([{"name": "final_average_salary"}], filled={}) == "worked_example"


def test_a_governing_gap_is_asked_about_however_few_there_are():
    """One unknown tier outranks any number of fillable value gaps: it is not a
    missing number, it is two different provisions."""
    assert gap_verdict([{"name": "tier"}], filled={}) == "ask"
    assert gap_verdict([{"name": "tier"}], filled={"withdrawal_date": "assumed"}) == "ask"


def test_more_than_two_invented_fills_is_not_a_figure():
    """The ceiling is the contract. Three invented values is a member we made
    up, however plausible each one looked on its own."""
    assert (
        gap_verdict([], filled={"a": "assumed", "b": "assumed", "c": "assumed"})
        == "worked_example"
    )


def test_derivations_and_citations_do_not_count_against_the_ceiling():
    assert (
        gap_verdict(
            [],
            filled={
                "months_before_age_60": "derived",
                "age_at_annuity_start": "statutory_parameter",
                "withdrawal_date": "assumed",
            },
        )
        == "compute_with_assumptions"
    )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_gap_contract.py -q`
Expected: FAIL, `gap_verdict` not defined.

- [ ] **Step 3: Write the verdict**

In `nodes.py`:

```python
def gap_verdict(
    missing: Sequence[Mapping[str, Any]],
    *,
    filled: Mapping[str, str],
) -> str:
    """§7's table, decided by counting what the ledger holds.

    The count is over *invented* fills only: a value derived from what the
    member said, or cited to the section that states it, is not a gap that was
    papered over. `filled` maps each name to the rank it resolved at, which
    code assigned at resolution time -- so the ceiling cannot be evaded by a
    model that would rather answer than ask.
    """
    governing, value = gaps.split(missing)
    if governing:
        return "ask"
    invented = sum(1 for source in filled.values() if source == SOURCE_ASSUMED)
    if value or invented > get_settings().assumption_ceiling:
        return "worked_example"
    if filled:
        return "compute_with_assumptions"
    return "compute"
```

Return it from `resolve_inputs` as `"gap_verdict"`, and add the key to
`state.py` (TypedDict, `PER_TURN_KEYS`, `initial_state()`).

- [ ] **Step 4: Teach `_describe` the four verdicts**

Replace the single `if missing:` branch in `tools._describe` with one that takes
`verdict` and the two gap lists:

```python
    if verdict == "ask":
        lines.append(
            "Cannot pick the provision: "
            + "; ".join(str(m.get("ask_for") or m.get("name")) for m in governing)
            + " decides which rule applies, not what it computes. Either ask "
            "for it, or -- where there are only two possibilities -- compute "
            "both and compare them, saying which applies in which case."
        )
    elif verdict == "worked_example":
        lines.append(
            "Not enough to compute this member's own figure: missing "
            + "; ".join(str(m.get("ask_for") or m.get("name")) for m in value)
            + ". Do NOT stop at saying so. Set out how the provision computes "
            "the benefit, work it through on the figures the Code itself "
            "states, and then say exactly what you would need to make it "
            "theirs."
        )
```

Pass the verdict through from the state in `compute_benefit`.

- [ ] **Step 5: Run everything**

Run: `uv run pytest tests/test_gap_contract.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "Gaps: four states, four behaviours, counted rather than declared"
```

---

### Task 6: An unknown tier is computed both ways and compared

**Files:**
- Modify: `src/pension_chat/agent/nodes.py` (`resolve_recipes`)
- Modify: `src/pension_chat/agent/blocks.py` (a branch block)
- Test: `tests/test_tier_branching.py` (create)

**Interfaces:**
- Consumes: `gaps.GOVERNING` (Task 4).
- Produces: `recipes` may carry two entries per formula type, keyed
  `f"{formula_type}@tier{n}"`, each with `"branch": {"fact": "tier", "value": n}`.

**Why:** the spec's own evidence — "Asked to calculate with an unknown tier, the
agent correctly identified the branch — 'If you are Tier 1… If you are Tier 2…'
— and refused to compute either. The right behaviour is both branches, computed,
side by side."

- [ ] **Step 1: Write the failing test**

```python
"""Two provisions, both priced, rather than a sentence about both.

Tier is derived from a hire date and the agent usually has it. When it does
not -- an anonymous session, a hypothetical member -- the honest answer is not
"I cannot establish your tier". It is both figures with the condition on each.
"""

from __future__ import annotations

from pension_chat.agent import nodes


async def _no_sections(sections):
    return {}


async def test_an_unknown_tier_resolves_both_branches(monkeypatch):
    looked_up: list[int | None] = []

    async def fake_lookup(formula_type, article, tier):
        looked_up.append(tier)
        return None

    monkeypatch.setattr(nodes, "lookup_recipe", fake_lookup)
    monkeypatch.setattr(nodes, "canonical_section_texts", _no_sections)
    await nodes.resolve_recipes(
        {
            "question": "what would my annuity be?",
            "requires_calculation": True,
            "formula_types_needed": ["retirement_annuity"],
            "employee_record": None,
            "retrieved_sections": [],
        }
    )
    assert sorted(t for t in looked_up if t is not None) == [1, 2]


async def test_a_known_tier_resolves_one_branch(monkeypatch):
    looked_up: list[int | None] = []

    async def fake_lookup(formula_type, article, tier):
        looked_up.append(tier)
        return None

    monkeypatch.setattr(nodes, "lookup_recipe", fake_lookup)
    monkeypatch.setattr(nodes, "canonical_section_texts", _no_sections)
    await nodes.resolve_recipes(
        {
            "question": "what would my annuity be?",
            "requires_calculation": True,
            "formula_types_needed": ["retirement_annuity"],
            "employee_record": {"tier": 2, "article_number": 11},
            "retrieved_sections": [],
        }
    )
    assert looked_up == [2]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_tier_branching.py -q`
Expected: FAIL — one lookup with `tier=None`.

- [ ] **Step 3: Branch the resolution**

In `resolve_recipes`, replace the single `tier` with the branches to try:

```python
    tier = employee["tier"] if employee else None
    # Tier keys the registry, so an unknown tier is not a missing number -- it
    # is two provisions. Price both and let the answer say which applies when,
    # rather than naming the branch and refusing to compute either.
    branches: list[int | None] = [tier] if tier is not None else [1, 2]
```

Make `resolve_one(formula_type, branch_tier)` take the tier, key its result
`formula_type if tier is not None else f"{formula_type}@tier{branch_tier}"`, and
stamp `recipe["branch"] = {"fact": "tier", "value": branch_tier}` when branching.
Gather over the product of formula types and branches.

- [ ] **Step 4: Render the comparison**

In `blocks.py`, a block that fires only when results carry a `branch`:

```python
def branch_comparison(results: dict[str, Any]) -> str:
    """Both provisions, priced, with the condition on each.

    Code-rendered from the engine's own output for the same reason every other
    block is: a model asked to present two conditional figures reaches for the
    one that looks more like an answer.
    """
```

Render a two-row table: the condition, the annual figure, the section. Register
it in `answer_blocks` under a `<<<COMPARISON>>>` marker.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tier_branching.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "Tier: an unknown branch is computed both ways, not named and refused"
```

---

### Task 7: The answer separates what was cited from what was assumed

**Files:**
- Modify: `src/pension_chat/agent/worksheet.py` (`facts_at`)
- Modify: `src/pension_chat/agent/nodes.py` (`_assumed` block)
- Modify: `src/pension_chat/agent/card.py`
- Test: `tests/test_assumption_block.py` (create)

**Why:** "What I assumed" currently lists derivations and would list citations.
A member reading that the agent assumed something it read in the Code learns the
opposite of the truth.

- [ ] **Step 1: Write the failing test**

```python
"""Three ranks, three sentences -- and only one of them is an apology.

A value the Code states gets its section. A value worked out from what the
member said gets the fact it came from. Only a value nobody supplied is an
assumption, and only that one is capped.
"""

from __future__ import annotations

from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_DERIVED,
    SOURCE_STATUTORY,
    WorkSheet,
)


def _sheet_with(**facts):
    sheet = WorkSheet(facts={}, figures={}, turn=1)
    for name, (value, source, basis) in facts.items():
        sheet.record_fact(name, value, source, basis=basis)
    return sheet


def test_a_statutory_value_is_cited_not_apologised_for():
    sheet = _sheet_with(
        age_at_annuity_start=(60, SOURCE_STATUTORY, "stated in Section 11-134(a)")
    )
    assert set(sheet.facts_at(SOURCE_STATUTORY)) == {"age_at_annuity_start"}
    assert sheet.assumed_facts() == {}


def test_a_derived_value_is_not_an_assumption():
    sheet = _sheet_with(
        months_before_age_60=(42, SOURCE_DERIVED, "worked out from the age you gave")
    )
    assert set(sheet.facts_at(SOURCE_DERIVED)) == {"months_before_age_60"}
    assert sheet.assumed_facts() == {}


def test_only_an_invented_value_is_an_assumption():
    sheet = _sheet_with(withdrawal_date=("2026-09-16", SOURCE_ASSUMED, "taken as today"))
    assert set(sheet.assumed_facts()) == {"withdrawal_date"}
```

Plus block-level tests calling `nodes._assumed` with each rank, asserting the
section appears for a statutory fill and that "What I assumed" appears only for
an invented one.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_assumption_block.py -q`
Expected: FAIL, `facts_at` not defined.

- [ ] **Step 3: Split the block by rank**

Add `WorkSheet.facts_at(source)` returning `dict[str, Fact]`, and rewrite
`_assumed` to render up to three labelled groups — "What the Code sets" with the
section, "What I worked out" with its basis, "What I assumed" with its basis —
skipping any group that is empty. Keep the existing signature and marker so
`MarkerSubstituter` needs no change.

Update `card.py` so the calculation card's input origins carry the same three
labels rather than collapsing them to "assumed".

- [ ] **Step 4: Run everything**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy .`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "Answer: what the Code sets, what was worked out, and what was assumed"
```

---

## Verification

After Task 7:

- [ ] `uv run pytest -q` — the whole suite
- [ ] `uv run ruff check . && uv run mypy .`
- [ ] Re-read every repointed test's docstring: each must say what the behaviour
      is *now*, not carry the old claim with a new assertion under it.
- [ ] Record in the spec's §7 what was already built (`ParameterSpec`) versus
      what this added, so the next reader does not rebuild the registry.

**Not in this plan, deliberately:** the eval suites (§12) that would measure the
hedging rate moving. They need the index and the models, so they are their own
piece of work; this plan is the behaviour, offline and unit-tested.
