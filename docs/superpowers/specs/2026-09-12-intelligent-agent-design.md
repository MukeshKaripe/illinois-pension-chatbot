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
| `related_sections(id, direction)` | `section_references` table | §3 — `cites` / **`cited_by`** |
| `check_coverage(description, act)` | `retrieve_coverage` + `assess` + `adjudicate` | returns verdict + `basis`; all existing code |
| `lookup_recipes` / `derive_recipe` | `registry` + `derivation` + `validator` | a miss returns **why** it missed |
| `compute_benefit(recipe, inputs, label)` | `resolve_inputs` → `validate_recipes` → `execute_calculations`, internals untouched | the only path to a number |

Member facts stay in the system context rather than behind a tool — no round
trip, always relevant — carrying provenance labels, and marked as stand-ins when
a hypothetical is live.

The loop is bounded at 6 iterations with sub-budgets (≤3 searches, ≤1 derivation
per benefit). Exhaustion is not an error: the model answers from what it found
and says what is still open. Latency is explicitly **not** a design constraint
for this work; iterations may run at real reasoning effort.

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

Derive the full edge set from the index into `section_references`
(migration 007: `from_section`, `to_section`), and **invert it**. Backward edges
are the new capability: *what else in the Code points at §11-134.* In statute the
thing that ruins an answer is almost always an exception living elsewhere, and
nothing today can see one.

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
  one sentence ("assume 3% raises"). New `calculation/projection.py`,
  deterministic.
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
pipeline:

| Block | Failure it prevents | Eval asserts |
|---|---|---|
| `answers_other_article` | quoting the member's figures as another Article's answer | figures present, framed as that Article's law |
| `frame` | answering a supposition in the plain indicative | conditional markers throughout |
| `CONFIRMED` elsewhere | a figure on a record the member says is wrong | no figure; record correction named |
| `described_as` + `BASIS_RECORD` | *"As a park employee covered by Article 11"* | description and Article never joined causally |
| `BASIS_STATUTORY` + `described_as` | hedging when the Code genuinely confirms it | plain statement permitted |
| `budget_spent` | asking a fourth time | answers on the record, names the assumption |

The loop must pass all six **without being told the answer.** If it cannot, that
block earned its place and returns as a *tool result* — a fact the model is
given, never a stance it is handed. That distinction is this spec in one line.

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

### Retrieval memory

New `answer_evidence` table: `(concept, article, tier, sections[], verified_at,
session_id)`. A row is written **only when an answer passed verification** — the
guardrail's pass is the label, so nothing enters on the strength of the model's
own opinion. `concept` is a short canonical phrase emitted by `compose` as a structured
field alongside the answer ("early retirement reduction"), matched later by
embedding similarity using the existing embedder.

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

Three suites, written to **fail** against the current pipeline, baseline
recorded:

- `evals/gold_conversational.yaml` — multi-part questions answered whole; a first
  retrieval that misses and a second that finds; "if I work two more years"
  producing a figure; a police-officer hypothetical producing a figure; a
  question needing no tool getting none; recipe reuse on a re-asked question.
- `evals/gold_stance.yaml` — the six cases from §5, asserting behaviour, never a
  block's wording.
- `evals/gold_capabilities.yaml` — thresholds surfaced, alternatives ranked with
  figures, ranges quoted when material, projection and stand-in origins correct
  on the card.

`scripts/eval_agent.py` gains `--mode {pipeline,loop}`.

### Phases

| Phase | Work | Ships value alone? |
|---|---|---|
| 1 | §3 corpus: `section_references` (migration 007), build script over the existing index, `outline` / `related_sections` | yes — improves pipeline retrieval too |
| 2 | §4 calculation: `lookup_recipes`, `thresholds.py`, `projection.py`, ranges into `calculation_results` | yes — exercised through the existing pipeline |
| 3 | §2 loop: `llm.call_with_tools`, `agent/tools.py`, `agent/loop.py`, `compose`, behind `agent_mode` | pipeline untouched |
| 4 | §5 deletions: six CONTEXT blocks, one at a time, each gated on its eval | — |
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
