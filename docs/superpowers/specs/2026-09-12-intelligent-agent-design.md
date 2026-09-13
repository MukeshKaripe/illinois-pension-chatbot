# Design: an intelligent conversational agent over the Illinois Pension Code

**Date:** 2026-09-12
**Status:** approved design, pending implementation plan
**Supersedes:** the agent-graph sections of `CLAUDE.md` (rewritten at cutover)

---

## The principle

> **Deterministic code decides what is true and what is allowed.
> Nothing writes the answer's content except the model that did the reasoning.**

The engine computes. The validator refuses. The guardrail rejects. None of them
composes a sentence.

This replaces the working rule that governed the current build — *"the LLM plans,
retrieves and writes prose; code calculates"* — which is still true and still
kept, but which was read in practice as *"code decides the conversation and the
LLM fills in the gaps."*

---

## 1. Diagnosis

The assistant reads as rigid even on turns where the pipeline gets everything
right. The rigidity is in the voice, not only in the behaviour, and it has a
single mechanical cause.

**The one model call that speaks to the member has never reasoned about the
question.** `prepare_answer` assembles a prompt — statute text, a figure, and a
stack of imperative stance instructions — and `config.py:88` sets that call's
reasoning effort to `none`, commented *"answer is grounded, not derived"*.

Because the writer cannot reason, the pipeline must reason on its behalf, in
advance, for every situation it can foresee. That is what these are, in
`prepare_answer`:

```
if answers_other_article:   "THIS QUESTION IS ABOUT A DIFFERENT PART OF THE CODE…"
elif frame:                 "THE MEMBER ASKED YOU TO SUPPOSE SOMETHING…"
elif basis == CONFIRMED…:   "THE MEMBER HAS CONFIRMED THEY ARE COVERED BY…"
elif described_as…:         "THE MEMBER DESCRIBED THEIR JOB, and what they said is NOT why…"
elif basis == STATUTORY…:   "WHAT THE MEMBER TOLD YOU ABOUT THEIR JOB MATCHES…"
if why == "budget_spent":   "YOU HAVE ALREADY ASKED THIS MEMBER AS MANY QUESTIONS AS YOU MAY…"
```

Six hand-written stances. Every new situation a member invents costs another
`elif`, and that treadmill is unbounded. It is audible in real transcripts:
*"The statute text provided here identifies who is covered by Article 12, but it
does not provide the retirement-annuity formula needed to calculate an Article 12
pension."* That is not a mind explaining a limit. It is a stenographer reporting
on its input tray.

The fixed graph is downstream of this cause, and shares a second one: **every
semantic decision is made once, early, with no evidence, and can never be
revised.** Search queries are written before any statute is seen
(`understanding.py` → `search_queries`) and never revised after results come
back. `needs_figure` is decided before it is known whether a recipe or its inputs
exist. Coverage is settled before retrieval and never reconsidered when the
retrieved text disagrees. And `nodes.py:531` sets `requires_calculation = False`
whenever the named Article is not the member's own, so a hypothetical about
another fund is *forbidden a figure* even when the member supplies every input.

### Verified as already working — not to be rebuilt

An external review recommended building several things that exist. Recorded here
so no implementation plan re-does them:

| Claimed missing | Reality |
|---|---|
| Per-turn employee fetch needs caching | `pinned_record` already does this (`nodes.py:556`); the DB is touched only when the pin is absent or for another profile |
| Recipe lookup needs semantic identity | `lookup_recipe(formula_type, article, tier)` + effective-date window never touches phrasing (`registry.py:105`) |
| Derivation should be an agent behaviour | It is: lookup → freshness → applicability → derive → validate → execute → persist |
| Hypotheticals need first-class context | `hypothetical_frame` exists and persists across turns (`nodes.py:268`) |
| Needs an assumption ledger | `WorkSheet` + `assumption_policy.POLICY`, with per-fact provenance on the card |
| Recipes blocked pending admin approval | They are not: derived recipes execute at `estimate` and are immediately reusable |

---

## 2. Architecture: one mind, one history

```
triage ─(deny / greeting)─> decline ─> END        unchanged: no model call, no DB
  └─> investigate  ⇄  run_tools                   the loop
        └─(no more tool calls)─> compose ─> END
                                    │
                            stream → guardrails.verify (unchanged)
```

`compose` is **not a second mind.** It appends the style rules to the *same
message history the loop built* and streams from there. The model that read
§12-118 and noticed the record disagreed is the model that writes the sentence
about it.

`prepare_answer` splits along the line the diagnosis draws:

- **How to write** — voice, citation discipline, marker protocol, closing line.
  Genuine rules. Survive intact as `compose`'s final instruction.
- **What to say** — the six CONTEXT blocks. Deleted, under the rule in §5.

### Tool surface

Seven tools, each wrapping code that already exists.

| Tool | Wraps | Notes |
|---|---|---|
| `search_statute(queries, articles, why)` | `retrieval.retrieve` | appends to `retrieved_sections`; **returns its own relevance scores plainly**, so weak results are visible rather than inferred |
| `get_section(id)` | `fetch_sections` | unfiltered, any Article |
| `outline(article)` | index `section_title` | §3 — the map |
| `related_sections(id, direction)` | `corpus.py` citation graph | §3 — `cites` / **`cited_by`** |
| `check_coverage(description, act)` | `retrieve_coverage` + `assess` + `adjudicate` | returns verdict + `basis` + why it was reached; `act` is supplied by the **model**, not a regex (see §2.1) |
| `lookup_recipes` / `derive_recipe` | `registry` + `derivation` + `validator` | a miss returns **why** it missed |
| `compute_benefit(recipe, inputs, label)` | `resolve_inputs` → `validate_recipes` → `execute_calculations`, internals untouched | the only path to a number |

Member facts stay in the system context rather than behind a tool — no round
trip, always relevant — carrying provenance labels, and marked as stand-ins when
a hypothetical is live.

### 2.1 The epistemic vocabulary, and why the loop makes it affordable

Statuses are properties of facts, not modes the agent enters. That model exists
already, twice over, and is kept:

```
worksheet.py:27-35   SOURCE_RECORD  STATED  HYPOTHETICAL  ASSUMED  WITHHELD  DERIVED
coverage.py:69-73    BASIS_CONFIRMED  STATUTORY  RECORD  HYPOTHETICAL  UNRESOLVED
coverage.py:58-60    ASSERTED  STIPULATED  DOUBTED
```

§4 adds `projected` and `stand_in` to the first row. Two further gaps are real
and are closed here, because neither sentence can be represented today:

- **`attributed`** — "my HR says I am under Article 11" is a third-party claim.
  It is not `ASSERTED` (the member is not vouching from their own knowledge), not
  `DOUBTED`, not `STIPULATED`, and today falls through to `ASSERTED`
  (`coverage.py:218`), treating hearsay as first-hand testimony. It should behave
  as a weaker assertion: check it, and on conflict say whose account is whose.
- **`superseded`** — "I used to be Article 11, but now Article 4" is a
  retraction. `apply_revisions` overwrites with `SOURCE_STATED`
  (`worksheet.py:290`), so a fact keeps no trace that it corrected something and
  nothing distinguishes a fresh statement from a correction. Add a `CORRECTED`
  marker carrying the superseded value and the turn it changed.

**The loop is what makes these affordable.** Today each act needs a regex
governor, and `CLAUDE.md` records that `test_coverage.py` exists precisely
because one governor was unreachable by any frame — a dead alternative invisible
on reading. Adding two acts that way costs two regexes, two worked examples and
two more chances at a dead branch. In the loop the **model** supplies `act` to
`check_coverage`, so the vocabulary extends by a line of tool documentation. The
`classify_self_reference` governors and the dead-alternative guard retire with
the pipeline in phase 6; `adjudicate()` stays pure Python and keeps deciding the
verdict.

### Execution budget

Not a fixed iteration count. A hard iteration cap is itself a semantic
constraint — "compare my options at 58, 60 and 62 with the reduction and the
survivor election" legitimately needs more thinking than "what is Article 11?",
and capping both at six punishes exactly the question the loop exists to answer.
Latency is explicitly **not** a design constraint for this work, so the budget
exists to stop runaways, not to ration reasoning.

| Resource | Budget |
|---|---|
| tool calls, total | 20 |
| `search_statute` | 6 |
| `derive_recipe` | 3 |
| `compute_benefit` | 12 |
| wall clock | 90s |

**Exhausting a sub-budget removes that tool and tells the model why; it does not
end the turn.** A loop that has spent its searches can still read a section it
already knows about, look up a recipe and compute — so the failure mode is a
narrower investigation, not a severed one. Only the total or the clock ends it.

### Evidence sufficiency

The budget running out is not by itself a licence to answer. Two situations look
identical from inside the loop and must not be treated alike: having enough
evidence but an unfinished investigation, and needing a provision that was never
established. A figure cannot be fabricated either way — `compute_benefit` has no
number without resolved inputs, and `validator.py` refuses a recipe citing a
section that is not in the corpus — but *prose* can still state a legal
conclusion on evidence that was never read.

So the gap is computed deterministically rather than judged. Every retrieved
section carries `referenced_sections`, and §3 adds the full citation graph: on
exhaustion, take the cross-references named in the evidence the loop gathered,
subtract the sections it actually read, and hand `compose` the remainder as a
fact. "You relied on §11-134, which computes on service as defined in §11-216,
and you never read §11-216" is a specific, checkable statement about what is
missing — far better than a model's impression that it has probably seen enough.
When that list is non-empty, the answer must name what it could not establish.

### Structured state, re-rendered each iteration

"One mind, one history" means one reasoning thread — not that state lives only
in raw message history. A loop forty tool-calls deep must not be rediscovering
`age = 56` by scrolling.

The loop's system context is **re-rendered from structured state on every
iteration**: member facts with provenance, the `WorkSheet`, coverage and its
basis, any live hypothetical frame, and figures already computed this
conversation. That block is authoritative over anything earlier in the history,
which also makes a mid-conversation correction take effect immediately instead of
competing with a stale message.

This is why `pinned_record`, `WorkSheet`, `hypothetical_frame` and the
calculation card all survive unchanged — they *are* the structured state, and
the tools writing the same keys is what makes the state summary cheap to build.

### Retired

`understand_turn`, `gate_scope`, and `assess_coverage` / `retrieve_policy` /
`resolve_recipes` as *nodes* — their logic survives as tools. With them go the
flags that were the rigidity: `requires_calculation`, `formula_types_needed`,
`needs_figure`, `search_queries`, `answers_other_article`,
`clarification_budget` as a gate, and `scenarios.extract_scenarios`' regex.

Two of those deserve their epitaphs. `search_queries` written before seeing
statute is the root of *"I don't have enough information"*; in the loop the model
reads results and writes a better query. And `extract_scenarios` exists, per
`CLAUDE.md`, only because `understand_turn` runs concurrently with the record
fetch and so cannot turn "wait 2 more years" into age 58 — in the loop the pinned
facts are already in context. `scenarios.corroborated` stays: scenarios still
leak forward onto turns that never asked for them.

`needs_figure` disappears as a concept, which is what makes "am I eligible at 58
and how much?" answerable as one question instead of a boolean.

---

## 3. The Code as a graph and a map

`referenced_sections` is already parsed at ingestion (`parser.py:306-339`) and
already indexed (`indexer.py:148`). `section_title` is indexed. Both halves of
this section are derivations over data that exists — **no re-ingestion.**

### The graph

Today those forward edges are used exactly once: `_follow_references`
(`retrieval.py:572`) takes the top 3 hits, walks one hop, keeps the first 2
targets, out of 20% of the character budget. That is a garnish on a search
result, not navigation.

Derive the full edge set from the index and **invert it**. Backward edges are the
new capability: *what else in the Code points at §11-134.* In statute the thing
that ruins an answer is almost always an exception living elsewhere, and nothing
today can see one.

**Built in memory, not in Postgres** — amended after Phase 1 shipped; the spec
originally called for a `section_references` table and migration 007. Three
reasons, in the order they matter:

1. **The public surface is two functions.** Nothing outside `agent/corpus.py`
   touches anything but `related_sections()` and `outline()`, and `_build()`
   already produces the whole structure in one pass. Persisting it later is a
   `to_rows()` plus a "read the table, else scan" branch inside `load_corpus()`
   — nothing else in the codebase moves. The decision is cheap to reverse,
   which is what makes deferring it correct rather than lazy.
2. **A table goes stale; the index cannot.** The edges derive from the search
   index, so a Postgres copy is wrong the moment an Article is re-ingested —
   the failure class `indexer.prune_stale_records` exists to prevent. A build
   over the live index is always consistent with what it was built from.
3. **The corpus is bounded by law.** 40 ILCS 5 is 24 Articles; the measured
   2,406 sections and 1,964 edges are close to final. The ~6s build scales with
   the statute, not with usage, and it runs at startup beside the other
   warm-ups rather than on a member's turn.

Revisit on a named trigger, not on taste: more than ~3 API replicas, where 6s
× N per deploy starts to matter; an admin UI that browses the graph; or a
startup budget under 10s. If it does move, **write it from the ingestion
pipeline** — that is the version that cannot go stale, because the graph is
then rebuilt exactly when the index is.

### The map

`outline(article)` projects every heading in an Article, in order. Two payoffs:

- **Orientation.** The agent can see that survivor provisions run §§11-145 to
  11-153 and go read them, instead of hoping the ranker surfaces one.
- **Vocabulary.** Headings are the statute's own words for its own concepts.
  This is the genuinely useful half of the "query rephraser" pattern — member
  language ("retire early without losing too much") to statute language
  ("annuity reduction", "withdrawal prior to age 60") — delivered as data rather
  than as a node. A separate rephraser node is rejected: in a one-mind loop it
  would be a second mind doing worse, with less context, what the first does
  natively between iterations.

A 24-line Article-level index also goes in the system prompt. Nearly free, and it
settles "which fund is this question even about."

Together these are the root fix for *"it searched once and gave up."* A loop that
can only re-run vector search re-fails in the same direction, more expensively.

---

## 4. Calculation intelligence

### Return the candidate set, not the winner

`lookup_recipe` (`registry.py:127-153`) fetches every candidate row and returns
`candidates[0]`, sorted by status → predicate-presence → version → recency. The
registry can already hold §11-134, §11-134(f-1) and §11-130 as separate rows —
`formula_id` carries the section. Lookup collapses them by a sort that has
nothing to do with which one pays the member more.

`lookup_recipes()` returns them all. `evaluate_predicates` (which exists, and is
already run on the single winner) filters to those the member actually reaches.
`compute_benefit` runs **each** and returns them ranked, best marked.
`derive_recipe` gains the same shape: where the statute offers several routes,
derive them all.

This also corrects an asymmetry: today a member gets the option the *sort order*
preferred. Under this they get the option the *statute* pays best, which is the
only defensible answer to "what is my pension?".

### Thresholds

New `calculation/thresholds.py`, fully deterministic. It walks the applicability
predicates and `OperationNode` tree of each applicable recipe, extracts every
boundary compared against a member fact — age ≥ 60, service ≥ 20 or ≥ 30, the
per-month reduction's zero point, the Tier 1/2 hire date — computes the member's
distance to each, and **re-runs the same recipe through the same DSL interpreter
at the boundary** to report the delta.

The cliff figure is therefore as authoritative as the main figure, computed by
the same engine under the same validation, needing no new trust story. Bounded to
a ~5-year horizon and the two or three that materially move the number. Code
finds the cliffs; the model decides whether this member wants to hear about them.

### Projection and stand-ins

Two new input origins beside `record` / `stated` / `derived` / `assumed`, both
flowing through `input_sources`, which the card and the guardrail already read:

- **`projected`** — a *known* value carried forward under a named rule. Service =
  known service + elapsed years. Salary = held flat by default, overridable in
  one sentence ("assume 3% raises").

  **This already exists** — amended after the Phase 0 baseline run.
  `facts.build_member_facts(assumed_retirement_age=…)` recomputes every fact as
  of the date the member reaches an age, and `execute_calculations` (`:1503`)
  runs each scenario against it. The L03 report carries a correct variant: at
  age 58, `as_of_date` 2028-05-15, `service_years` 23.2,
  `months_before_age_60` 24, $36,567.41, with `salary_basis` "carried forward
  at today's level; future pay rises are not projected". No
  `calculation/projection.py` is needed.

  What is missing is **reaching it and using it**, which is what Phase 2 now
  builds: a future-dated question must produce a scenario at all (L01 produced
  none for "work two more years"), the answer must lead with the variant that
  answers the question rather than the base (L03 computed $36,567.41 and quoted
  $32,126.78), and an extracted fact must not override the base while its
  dependents stay at today's values (L03's base paired
  `age_at_annuity_start` 58 with `months_before_age_60` 44). Only the growth
  rule is genuinely new — "assume 3% raises" has nowhere to live today.
- **`stand_in`** — the member's own value used under another Article's recipe,
  named as such in the answer and on the card.

`NEVER_ASSUMABLE` (`assumption_policy.py:31`) stays exactly as written, and the
line it draws becomes precise: **projection is a function of a known value;
assumption is invention from nothing.** An anonymous member who never stated a
salary is still asked, because there is nothing to project from.

### Ranges

`assumption_policy.plausible_range()` (`:165`) computes the spread an assumed
input implies and is called nowhere outside its own module. `compute_benefit`
re-runs the recipe at both ends — same engine again — and returns low/high beside
the point estimate. The card shows the range; the model quotes it when material.

### Constraint

Every number above **must be written inside `calculation_results`**, not beside
it. `guardrails._collect_allowed_numbers` (`:291`) walks that structure
recursively, so figures placed inside it are admitted with no change, and figures
placed beside it cause every enhanced answer to be replaced by the verifier — a
failure that would look like hallucination.

---

## 5. The deterministic boundary

### Unchanged, and not up for discussion

The DSL interpreter and its fixed operation set; `validator.py`'s structural and
evidence checks; `risk_assessor.py` reading risk from objective signals and never
the model's self-assessment; the `no_execute` / `estimate` / `authoritative`
execution policies; RLS and `get_db_session`; `triage`'s deny list and greeting
exit; `personas.validate`'s relational bounds.

### The guardrail's promotion

Today a coverage claim is policed twice — a CONTEXT block tells the model what
stance to take, and `_verify_coverage` checks that it did. Deleting the block
leaves `_verify_coverage` as the whole enforcement, and it is up to the job: it
already refuses any assertion that the member *is* covered by an Article unless
`coverage.basis` is `member_confirmed` or `statutory_match`, still permits
attribution ("your record places you in Article 11"), still requires visible
conditionality under a stipulated premise, and still strips quoted spans first.

The consequence is the design's keystone: **`check_coverage` becomes necessary
without being mandatory.** A model that skips it and asserts an Article anyway
has no `basis` in state, so the assertion is rejected and the answer replaced.
The tool earns its calls from the consequence of not calling it — which is how a
capability should work, and why nothing is lost by deleting the node.

### Repair

`repair_answer` becomes a continuation of the same history — *"your citation to
§11-216 isn't in anything you read; fix it"* — rather than a fresh call with a
complaint attached. The model knows what it read and why it cited it, so the
repair is a correction rather than a re-guess.

### The deletion rule

**No CONTEXT block is deleted until the failure it prevents exists as a failing
eval case.** Six blocks, six cases, written first and passing against the current
pipeline — and on a block-by-block reading they do not all deserve the same
fate. Deleting all six was a round number, not an argument.

| Block | Verdict | Why |
|---|---|---|
| `answers_other_article` | **delete** | the flag itself is the complaint-#8 bug; the block has nothing left to attach to |
| `budget_spent` | **delete** | `clarification_budget` stops being a gate, so the situation stops existing |
| `BASIS_STATUTORY` + `described_as` | **delete** | pure permission, which `_verify_coverage` already grants |
| `frame` | **delete, guardrail covers it** | `_CONDITIONAL_MARKER` already enforces conditionality on a stipulated premise |
| `CONFIRMED` elsewhere | **rewrite** | it says "give no figures", which §4 stand-ins now contradict — stale against the new design, not redundant |
| `described_as` + `BASIS_RECORD` | **convert to a tool result** | its safety half is covered, but it also requires an invitation to correct the record — helpfulness, which no guardrail enforces |

Each eval case is still written first and still gates its change. The four
deletions must pass **without being told the answer**; if one cannot, that block
earned its place and comes back the way the last row goes — as a fact
`check_coverage` returns ("coverage rests on the record alone; the member
described themselves differently and has not confirmed it"), never as a stance.
**A fact the model is given, never a stance it is handed** is this spec in one
line, and the last row is that rule applied to the spec own scaffolding.

### Named risk

With nothing mandatory, a model can be smart and lazy: skip the coverage check,
skip the computation, and write something fluent and thin. The guardrail catches
the *false* version of that, not the merely shallow one. Defence is the eval
suite plus `compose` seeing accumulated state, so a thin investigation yields a
visibly thin answer. To be watched specifically in the first runs.

---

## 6. What accumulates

Today the formula registry is the only thing that learns, covering roughly a
fifth of what the agent does.

### Verified retrieval memory

Deliberately not called learning: nothing here changes what the model believes
about the law. It remembers only which sections *verifiably answered* a kind of
question before. New `answer_evidence` table: `(concept, article, tier, sections[], verified_at,
session_id)`. A row is written **only when an answer passed verification** — the
guardrail's pass is the label, so nothing enters on the strength of the model's
own opinion. `concept` is a short canonical phrase emitted by `compose` as a structured
field alongside the answer ("early retirement reduction"), matched later by
embedding similarity using the existing embedder.

`compose` emits one further structured field in the same place: **what the
member asked for, and which parts of it this answer addressed.** It is written
*after* the evidence is in, so unlike a goal classified up front it cannot go
stale, and it is what `gold_goal_completion` asserts against. It is a
self-report, so it proves nothing on its own — its value is that a turn which
answered two of three questions says so in a field a test can read.

It surfaces as **enrichment on the `search_statute` result, never as a filter**:
*"sections that previously answered questions like this: §11-134, §11-216."* That
is the safety property — a hint cannot suppress a fresh search, so a section that
once got lucky cannot entrench itself.

Confirmed coverage determinations ride in the same table under
`concept = "coverage: <description>"`. That one generalises: "employee of a board
of park commissioners" → Article 12 is the statutory test, not a fact about a
person.

### Captured failures become evals

Every hard verification failure is a labelled example of the thing we are trying
to prevent, and today it is logged and dropped. Write each to `evals/_captured/`
— the conversation, the rejected answer, which check failed — and a script
promotes reviewed ones into the regression suite. This also serves the deletion
rule directly: if removing a CONTEXT block reintroduces its bug, the capture file
says so on the run it happens, with the transcript attached.

### The registry is trusted by default

**The gate is the validator, and it always was.** `validator.py` checks structure
and evidence completeness deterministically, in milliseconds, before anything
runs. Admin approval never had that job and is not given it. A recipe derived on
this turn is validated, executed, answered with, and immediately reusable at
`agent_derived`. No member ever waits for a human.

**"Trusted" here means trusted to run and be reused — never trusted as an
authoritative reading of the law.** Those are different claims and the spec keeps
them apart: a derived recipe executes at `estimate`, keeps the "this is an
estimate" line, and is labelled `agent_derived` wherever it is shown. Nothing
promotes it silently; see the trust-label paragraph below, and the decision to
leave `authoritative` unreachable in the pilot.

> **The registry is trusted by default. The admin's power is to correct and
> retire, not to admit.**

Positive control does not scale past a handful of Articles, makes the assistant
worst exactly when a member asks something new, and in practice nobody clicks the
button. Negative control is what is actually needed, and `no_execute` / `retired`
already exist for it with no surface.

Therefore **no promotion queue.** Instead:

- **Usage columns** — `reuse_count`, `last_reused_at`, `contested_count` (the
  last from `input_overrides`, which already detects a member disputing a figure
  and today only mentions it in prose). Repurposed from "what earned approval" to
  "what is load-bearing": a reviewer should see the recipe that has quietly
  produced 400 figures first, because that is where an error costs most.
- **Review as audit, on the admin's schedule.** A registry browser showing the
  rule in readable form, source sections with text, worked examples from real
  turns, and contest history. No queue, nothing pending.
- **Re-derivation with feedback.** `POST /admin/formulas/{id}/rederive` with a
  `feedback` note placed in the derivation context ("you missed §11-134(a)'s
  tiered alternative"). The result supersedes; the old row is `retired` with a
  pointer to its replacement. This makes admin time compound, instead of a button
  that says yes.
- **Retire and suspend, surfaced.** One action to `retired` (drops out of reuse),
  one to `no_execute` (stays visible, stops producing figures) for a recipe under
  suspicion.
- **Drift alerts.** When `verify_freshness` marks a row stale, flag the
  replacement for review with the text diff. Framed as "the statute moved under
  this recipe", which is the thing a human genuinely needs to know.
- **Bug fix.** `verify_freshness` returns early for an unfingerprinted row
  (`registry.py:173`) *before* the stale-marking code, so such a row re-derives
  every turn forever and is never marked. Fix in this phase.

`_STATUS_RANK` stops being a selector (§4 returns all candidates) and becomes a
**trust label per option** — "§11-134(a): $32,127 (approved)" beside "§11-134
tiered: $30,940 (estimate)". `authoritative` stays unreachable in the pilot: the
fund makes the binding determination regardless of who blessed the formula, so
dropping the estimate line is a promise the system cannot make.

### Explicitly not built

Member corrections rewriting records (a claim is not a correction; it stays a
report); any learning that changes a figure without a human in the path; any
mechanism that lets an unverified answer teach the system.

---

## 7. Evaluation, phasing, cutover

### Phase 0 — build the instrument first

Five suites in total, but **each lands with the phase it gates** — amended when
the Phase 0 plan was written. Only three belong here: `gold_stance` asserts
behaviour that replaces the Phase 4 CONTEXT-block deletions, and
`gold_capabilities` asserts Phase 2 features, so written now each would fail
only for "not built yet", which measures nothing. The three below need no new
feature to be meaningful — only the loop.

A further rule, learned from writing them: **a Phase 0 case that passes is
suspect.** Each case is marked honestly, `active` where the behaviour exists
today and must not regress, `expected_fail` (with the harness's required `gap:`)
where it is the loop's job. Phase 3 flipping those to `active` *is* the cutover
evidence.

Written to **fail** against the current pipeline, baseline recorded:

- `evals/gold_conversational.yaml` — multi-part questions answered whole; a first
  retrieval that misses and a second that finds; "if I work two more years"
  producing a figure; a police-officer hypothetical producing a figure; a
  question needing no tool getting none; recipe reuse on a re-asked question.
- `evals/gold_goal_completion.yaml` — the suite that measures helpfulness rather
  than mechanism, because an agent can call tools competently and still answer
  the wrong question. Cases: "can I retire at 58 and how much?" returns
  eligibility *and* figure *and* explanation, not one of the three; "actually
  make that three years" modifies the live scenario instead of starting over;
  "what about my wife?" continues into survivor benefits instead of asking what
  they would like to know; "what about Article 4?" switches question context
  without dragging Article 11 facts in uninvited.
- `evals/gold_no_ask.yaml` — the complaint-#1 suite, run as one conversation
  against a profile that already holds age, service and salary. "If I work two
  more years" must produce a figure with **no clarifying question**; "assume a 3%
  raise" must update the projection rule and recompute; "actually I'm 57" must
  revise the fact and recompute. Any clarification the profile could have
  answered is a failure, not a style preference.

`scripts/eval_agent.py` gains `--mode {pipeline,loop}`.

### Phases

| Phase | Work | Ships value alone? |
|---|---|---|
| 1 | §3 corpus: `agent/corpus.py` — citation graph and outline over the existing index, `_follow_references` rewired to it | **shipped** — improves pipeline retrieval too |
| 2a | §4 inputs: reach the projection that already exists — scenario recognition, answering from the variant, base consistency, the growth rule | yes — makes L01 and L03 pass on the pipeline |
| 2b | §4 outputs: `lookup_recipes`, `thresholds.py`, ranges into `calculation_results`, and `evals/gold_capabilities.yaml` | yes — exercised through the existing pipeline |
| 3 | §2 loop: `llm.call_with_tools`, `agent/tools.py`, `agent/loop.py`, `compose`, behind `agent_mode` | pipeline untouched |
| 4 | §5 deletions: `evals/gold_stance.yaml`, then the blocks one at a time, each gated on its case | — |
| 5 | §6 learning: `answer_evidence`, captured failures, registry usage + admin actions | yes |
| 6 | Cutover: flip the default, delete the pipeline, rewrite `CLAUDE.md` | — |

The ordering is insurance. If the loop underdelivers, phases 1, 2 and 5 have
still made the assistant materially smarter, and the flag keeps the pipeline one
config value away throughout.

### Cutover criterion

1. The CRITICAL block passes. Non-negotiable — it already fails a run whatever
   the overall score says.
2. No existing category regresses by more than one case; overall rate at least
   the pipeline's.
3. `fund_smoke` still resolves every fund to its own Article, no borrowed
   formulas.
4. The three new suites: **loop passes, pipeline fails.** If the pipeline also
   passes, the case was not testing what we thought.
5. Hard guardrail-failure rate no higher than the pipeline's.

TTFT and tokens per turn are reported every run and **gate nothing**, per the
decision to defer latency until deployment moves into the models' region.

### Tests

Unchanged (they test layers the loop keeps): `test_calculation`,
`test_calculation_trace`, `test_guardrails`, `test_facts`, `test_ingestion`,
`test_personas`, `test_funds`, `test_db_access`, `test_applicability`,
`test_coverage`'s `classify_self_reference` half.

Pipeline-only in phase 3, deleted in phase 6: `test_understanding`,
`test_coverage_node`, `test_scenarios`' extraction half.

New: `test_tools`, `test_loop` (tool dispatch, budget exhaustion, malformed tool
arguments), `test_thresholds`, `test_projection`, `test_section_graph`.

### Frontend

Unchanged through phases 1–4 — the tools write the state keys the SSE layer
already sends. Phase 2 adds calculation-card work: alternatives, threshold
deltas, ranges, and the `projected` / `stand_in` origins each need somewhere to
show. Additive, lands after phase 2.

The separate UI items — one collapsed citation block instead of two, and profile
plus sign-out moved into the left drawer — ship first as their own bounded piece
of work, independent of everything here.

---

## 8. Decisions taken during design

| Decision | Chosen | Rejected |
|---|---|---|
| Orchestration | one tool-calling loop, gates kept | action-enum planner; DAG with retry edges; diagnose-first |
| Coverage | tool + unchanged output gate | mandatory pre-loop step; fold in entirely |
| Answer generation | same history as the loop | separate `prepare_answer` call *(reversed mid-design: it preserved the two-minds split that is the diagnosis)* |
| Future-dated figures | projection of known values | assumption of unknown values; ask every time |
| Other-Article figures | compute, own facts as named stand-in | refuse; require stated inputs |
| Query rewriting | inside the loop + heading vocabulary | a `transform_query` node |
| Recipe approval | trusted by default; correct and retire | promotion queue as a gate |
| Latency | not a constraint for this work | parallelised loop; capped iterations for speed |
| Loop bounds | multi-resource budget; sub-budget removes a tool | fixed iteration count |
| Epistemic model | keep the existing per-fact statuses; add `attributed`, `superseded`, `CORRECTED`; the model supplies the act | a parallel `USER_ASSERTED`/`INFERRED` taxonomy *(it renames what `WorkSheet` and `coverage.basis` already carry)* |
| CONTEXT blocks | four delete, one rewrite, one becomes a tool result | delete all six |
| Citation graph storage | in memory, rebuilt per process from the index | a `section_references` table and migration 007 *(derived data that goes stale on re-ingestion, for a benefit nothing needs yet)* |
| Eval suites | each lands with the phase it gates | all five written in Phase 0 *(two would fail only for "not built yet")* |
| Goal tracking | completion self-report at `compose`, measured by evals | a `user_goal` / `subgoals` struct computed up front *(it is `needs_figure` in better clothes — the same decide-early-never-revise failure this spec exists to remove)* |
| Exhaustion | deterministic unresolved-cross-reference list handed to `compose` | "answer from what you found"; a model-judged sufficiency score |
