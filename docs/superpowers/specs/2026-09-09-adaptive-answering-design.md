# Adaptive answering: making the assistant reason like a colleague, not a gate

Date: 2026-09-09
Status: approved design, not yet implemented
Branch it was written on: `recipe-applicability-and-answer-voice`

## The complaint

The assistant feels rigid, deterministic and unhelpful. It should understand what
a member actually wants, ask when something is genuinely unclear, make safe
assumptions and say so, and reach a satisfying answer even when it cannot reach
the exact one. Where it is blocked it should say what is blocking it and what
would unblock it, and finish the job when the member supplies that. This applies
to every kind of question, not only calculations.

## What is actually wrong

The leading hypothesis before this investigation was the hierarchical
parent-child chunking. It is not the cause, and neither is retrieval or legal
reasoning. Both rounds of external review graded retrieval, citation grounding
and cross-reference discovery green, and the review's legal-correctness items
(§11-130 dismissed as uncalculable, elections treated as `max()`, invented
44-month precision) have since been fixed: `alternatives_considered` now carries
§11-130 as `applicable_not_calculable`, elections are marked `member_election`,
and the worked-steps table is present.

Two multi-turn conversations were run against the live index and model as
`ARTICLE_11_STANDARD` (age 56, 21.5 years of service, FAS $69,956.40). They
locate the problem in the control layer.

### Conversation A - a member planning retirement (8 turns, 3 refused outright)

| turn | message | outcome |
|---|---|---|
| 1 | "I'm starting to think seriously about retiring but I honestly don't know where to begin." | refused as out of scope, zero model calls |
| 3 | "My wife is worried about what happens to her if something happens to me. What would she get?" | refused as out of scope |
| 6 | "Put the retire-now, retire-at-58 and retire-at-60 numbers side by side." | table of em-dashes and "Not worked out"; asked the member to supply the projected pension amounts |
| 7 | "Does any of this get taxed, and do I still get health insurance?" | refused wholesale |

Turn 1's cause is exact: `"retiring"` does not contain the substring `"retire"`.
`_PENSION_TERMS` holds `retire` and `retirement`; the gerund matches neither, and
`classify_scope` defaults to deny. Turn 3's cause is that the list holds `spouse`,
`widow` and `survivor` but not `wife` - and turn 4, the follow-up, passed the gate
and produced a good survivor answer citing §11-145.1 and §11-134.2. The knowledge
was present throughout.

Turn 2 produced a good, cited, traceable figure, but the member asked about
"sometime in the next couple of years" and received a retire-today number with no
acknowledgement of the mismatch.

### Conversation B - a member being imprecise (the decisive one)

> **Member:** actually sorry it's more like 23 years, i forgot the first two
>
> **Assistant:** To work out the revised amount, I would need the pension
> calculation using 23 years of service.

The system asks the member for the pension calculation. It held the cached
recipe, the corrected service figure, the FAS and the age.
`requires_calculation=False`, because `_CALCULATION_SIGNALS` does not match that
sentence, so the calculation branch never ran. Two turns later "just tell me the
monthly number" returned `$2,677.23` immediately.

### The four defects

1. **Scope is a default-deny keyword membership test.** Every gap in a
   hand-written vocabulary is a silent, free refusal. This bug class has now bitten
   three times.
2. **Calculation routing reads only the surface words of the current message.**
   There is no notion of a live computation in the conversation whose inputs can be
   corrected, extended or re-run.
3. **There is no assumption layer.** `missing` is terminal. Every "I would need..."
   list is written by the answer model with nothing behind it, which is why it asks
   for things the system already has, could derive, or could never obtain.
4. **Everything is decided by Python upstream and policed by Python downstream.**
   `prepare_answer` makes no model call; generation is single-pass at
   `reasoning_effort="none"`; the verifier can only pass or delete the whole answer.

### What the measurements say about the answer step

The answer step is not where the problem is. Eight real prompts captured from the
live pipeline, regenerated under each variant and scored by the project's own
`_verify` plus shape checks:

| variant | median TTFT | median total | guardrail | shape problems |
|---|---:|---:|---:|---:|
| effort=none (today) | 4.65s | 5.86s | 8/8 pass | 1 |
| effort=low | 5.60s | 6.82s | 8/8 pass | 1 |
| effort=medium | 10.25s | 11.52s | 8/8 pass | 2 |
| outline-then-write | 7.15s | 7.93s | 8/8 pass | 4 |

More reasoning bought nothing and `medium` was worse. Outline-then-write was the
worst variant and broke a hard shape rule, producing a pension figure with no
"How that is worked out" table because its outline overrode the shape contract in
`ANSWER_SYSTEM`.

Across 42 live generations - including 10 turns engineered to tempt the
arithmetic prohibition ("how much more per month is that?", "what percentage of
my salary is that?", "what would that be after 10 years of annual increases?",
"if my wife gets half of that, what is her monthly amount?") - there were **zero
guardrail hard failures**.

Two failures were then forced by splicing an untraceable figure and a fake
citation into a verified answer. A single repair call recovered **2/2** at
3.9-4.1s TTFT, keeping the full calculation and table and removing only the
offending sentence. Today the member instead loses the entire answer and receives
a template asking for their age, service and salary history - all three of which
are in their record.

### Model choice

Turn understanding was benchmarked on both provisioned deployments:

| deployment | reasoning_effort | median | mean | max |
|---|---|---:|---:|---:|
| gpt-5.6-luna-1 | none | 2.27s | 2.35s | 3.34s |
| gpt-5-mini-1 | none | 3.99s | 3.90s | 4.74s |
| gpt-5-mini-1 | low | 5.30s | 5.78s | 10.84s |

The current model is faster **and** more accurate. `gpt-5-mini-1` hallucinated
outside the corpus - a "50% survivor option (~$16,063/yr)", joint-and-survivor
elections that do not exist in Article 11, and "Illinois does not tax retirement
income". Keep `gpt-5.6-luna-1`; provision no second deployment.

On the eight routing cases, `gpt-5.6-luna-1` was correct 8/8, including every
case the live system gets wrong, and volunteered the unresolved items ("whether
projected service credit and final average salary should be estimated for ages 58
and 60") that the system currently has nowhere to put.

## Design

### 1. The working sheet

A new module, `agent/worksheet.py`, owning the durable conversation state behind
one interface. This is a consolidation, not an invention: `assumptions`,
`baseline`, `selected_provision`, `open_question`, `scenario_set`, `coverage` and
`clarifications_asked` are already durable keys in `state.py`. What is missing is
that nothing reads them as a whole, `source` has no value meaning "we filled this
in ourselves", and `missing_inputs` cannot reach them.

```
facts     name  -> {value, source, basis, turn, asked}
figures   label -> {result, inputs_used, provision, turn, rests_on}
open      [{what, why, asked_turn, bound_to}]
```

`source` grows from three values to five:

| source | meaning | may a figure rest on it? |
|---|---|---|
| `record` | from the member's file | yes |
| `stated` | the member said it | yes |
| `hypothetical` | a premise they asked us to suppose | yes; the answer stays conditional |
| `assumed` | we filled it in; `basis` says why | yes, but the answer must say so |
| `withheld` | on file, but not usable under this premise | no |

`withheld` fixes a measured defect: under a stipulated premise `resolve_inputs`
deliberately starves record facts, so age and service arrive as plain *missing*
and the answer asks the member for an age it knows. The sheet can now distinguish
"we do not know" from "we know and must not use it here".

`rests_on` makes recompute structural. `baseline` already records the inputs a
figure used; naming them as a dependency set means a revision to `service_years`
mechanically invalidates every figure that used it.

Two invariants, both testable without a network:

1. Every value reaching the DSL interpreter has a `source`. No anonymous inputs.
2. A figure whose `rests_on` includes an `assumed` fact carries an assumption
   note, or verification fails.

Code touched: `state.py` gains the two enum values and `rests_on`; `worksheet.py`
is new and pure; `nodes.py` stops reading durable keys directly. The DSL
interpreter, validator, risk assessor and ingestion pipeline are untouched.

### 2. Turn understanding

One node, `understand_turn`, replaces every regex that decides what a message is.
It emits, in one call:

```
in_scope, out_of_scope_reason
intent                     one short phrase - what they actually want
benefit_types              [retirement_annuity | survivor_annuity | ...]
needs_figure               including "compare these three dates"
recompute, revised_facts   {name: value} the member corrected or supplied
scenarios                  [{label, retirement_age, basis}]
unresolved                 [{what, why, assumable, suggested_default}]
answerable_part            the pension half of a two-part question
answers_open_question      binds a reply to the question asked last turn
search_queries             the retrieval queries for this turn
```

**Node responsibilities after the change**, stated explicitly because the
boundary matters to whoever implements this:

- `understand_turn` **replaces `plan_request` entirely**, including its
  `search_queries` output, and takes over `requires_calculation`,
  `formula_types_needed` and `scenarios`. `plan_request` is deleted, along with
  the `deterministic_planning` config switch, whose whole purpose was skipping a
  planner call that no longer exists separately.
- `triage` **remains, reduced**. It keeps the three things that are pure string
  work over the raw question and are still trustworthy: the high-precision
  `_OUT_OF_SCOPE` deny, `detect_instruction_override`, and
  `extract_section_references` / `extract_article_references`. It loses
  `classify_scope`'s keyword-membership accept.
- `assess_coverage`, `retrieve_policy`, `resolve_recipes`, `resolve_inputs`,
  `validate_recipes` and `execute_calculations` keep their current
  responsibilities and their current order.

**It keeps the concurrent pair.** The working sheet is durable, so from turn 2
onward the member's facts are already in the checkpoint and no record read is
required; on turn 1 nothing has been corrected yet, so `recompute` is moot.
`understand_turn` therefore runs in parallel with `fetch_employee_record` exactly
as `plan_request` does today, and the graph shape is unchanged:

```
triage ---+--> understand_turn --------+--> assess_coverage --> retrieve_policy --> ...
          +--> fetch_employee_record --+
```

Net latency: +2.3s on turns where `deterministic_planning` currently skips the
planner call, and approximately zero where it does not. Partly offset because
`resolve_inputs` can skip its own extraction call when `revised_facts` already
resolved everything, which is the common case on a correction turn.

**One regex survives, on an asymmetry.** `_OUT_OF_SCOPE` - the pattern catching
"write me a poem", "what's the weather" - is high precision: when it fires it is
right, so it stays as a free fast-path *deny* and a poem request still costs zero
model calls. The `_PENSION_TERMS` membership test is unreliable in the other
direction: when it says "no pension words here, refuse", it is wrong about
"retiring", "my wife" and "does this get taxed". The rule: **keep the regex where
it accuses, delete it where it acquits.**

Deleted from `nodes.py` and `guardrails.py`: `_PENSION_TERMS`, `_TIMING_LANGUAGE`,
`_CONTINUATION`, `_REFERS_BACK`, `_CALCULATION_SIGNALS`, `_BENEFIT_CUES`,
`_AGE_PATTERNS`, `_AGE_LIST`, `_OFFSET_PATTERNS`, `_RETIRE_NOW`, `_NOW_CUE`,
`_RESTATED_FACT`, `extract_scenarios`, `infer_formula_types`,
`_continuing_formula_types`.

That removes `test_planning.py`'s worked-example guard over
`_CALCULATION_SIGNALS`, which was guarding a fragility that will no longer exist.
Its replacement is the routing eval suite, which asserts the verdicts directly.
`test_coverage.py`'s equivalent guard over the act governors is untouched.

**Failure handling is deliberately reversed.** Today a model failure in planning
degrades toward refusal. `understand_turn` fails **open**: on timeout or
malformed JSON the fallback is `in_scope=True, needs_figure=False,
benefit_types=[retirement_annuity]`. The safety story is the post-generation
verifier and the deterministic calculation gate, neither of which is weakened by
letting a question through, and a wrongly-refused member has no recourse at all.

### 3. The assumption layer

**Not everything may be assumed, and the model does not get a vote.** A declared
table in `calculation/assumption_policy.py` maps input name to
`{assumable, default, basis, plausible_range}`. `understand_turn` may propose a
default in `unresolved[].suggested_default`, but only the policy decides. The
model states; code decides.

| class | inputs | policy |
|---|---|---|
| Assumable | `withdrawal_date` (to today), `months_before_age_60` (from stated age, mid-year), `annuity_start_date`, projection basis (salary held flat, service accruing) | fill, label, offer to correct |
| Never assumable | `article_number`, `employment_status`, `tier`, `final_average_salary`, `service_years`, salary history | ask |

The system may assume things about timing and framing, never about who the member
is or what they earned.

**Materiality is computed, not asserted.** Each assumable input carries a
`plausible_range`, and the DSL interpreter re-runs at its endpoints -
deterministic, local, sub-millisecond, no model call. The disclosure then becomes
an actionable next step:

> Your estimated pension is **$32,126.78 a year**. I've assumed your birthday
> falls mid-year, because your exact date of birth isn't on file - that puts the
> real figure somewhere between about **$31,766** and **$32,757**. Tell me your
> date of birth and I'll pin it down exactly.

This is also the correct resolution of the external review's complaint that
`age = 56 -> months_to_60 = 44` is invented precision. When it comes from the
record's birth date it is a fact; when it comes from a bare "I'm 56" it is an
assumption with roughly a $1,000 spread, and the system should say so rather than
silently pick a point in the middle.

**Three places it surfaces:**

1. In the sheet - `source: assumed`, with `basis`.
2. In the answer - a "What I assumed" block, and inline in the opening sentence
   when the spread exceeds 2% of the figure.
3. In `guardrails.py` - a new hard failure, `ASSUMPTION_NOT_DISCLOSED`: a figure
   whose `rests_on` includes an `assumed` fact fails verification unless the
   answer names that assumption. It reuses the figure-tracing machinery `verify`
   already has.

**A ceiling: at most two assumed facts behind one figure.** Beyond that the figure
stops being an estimate of anything, and the turn switches to the guided path -
ask for the one thing that unlocks the most, and say what is already established.

**Assumptions are corrigible.** When the member supplies the real value it flips
from `assumed` to `stated`, and every figure whose `rests_on` names it recomputes.

### 4. Recompute and convergence

**Recompute is dependency invalidation.** `understand_turn` emits
`revised_facts`; the sheet applies them; every figure whose `rests_on` intersects
the revised names goes stale. The branch condition becomes:

```python
needs_figure or any_stale_figure(sheet)
```

instead of `_CALCULATION_SIGNALS` matching the current sentence.

**A recompute is cheaper than the original.** The sheet holds
`selected_provision`, so a recompute reuses the registry recipe rather than
paying derivation again. But a revised value can cross a statutory threshold, so
before reuse the revised facts go back through `applicability.py`'s predicates -
already deterministic, already present, no model call. Predicates still verify:
run the interpreter. A predicate now fails: fall through to `resolve_recipes`.

**Never ask for what you already have.** Before the answer prompt is assembled,
`missing_inputs` and `unresolved` are filtered against the sheet:

- present as `record` or `stated`: removed. This alone fixes the "asks for what it
  has" defect that appeared in 3 of the 8 benchmark cases.
- present as `withheld`: rephrased honestly rather than asked - "your file has
  your service, but I'm not using it while we're talking about Article 12".
- already asked in an earlier turn: not asked again; assumed if the policy allows,
  otherwise the turn proceeds and says what it proceeded on.

What remains is ranked by how many blocked figures each would unlock, and at most
two are asked, highest leverage first. `clarification_budget` still caps the
conversation at three asking turns.

**A partial answer beats a refusal, with one hard rule.** `answerable_part`
handles the two-part question: the pension half is answered from the Code, and the
other half is acknowledged in one line naming who owns it - the fund, HR, the IRS.

**The out-of-Code portion is acknowledged and referred, never answered.** This is
the one place the redesign could regress into hallucination, and the benchmark
supplies the evidence: `gpt-5-mini-1` volunteered "Illinois does not tax
retirement income for most pensions" and invented joint-and-survivor elections.
Not filling that gap is a guardrail check, not a prompt suggestion.

**The degradation ladder.** Every request gets the same ordered ladder, and the
system takes the highest rung the sheet supports:

```
1  figure, fully sourced
2  figure, resting on <=2 labelled assumptions, with the spread
3  the formula and its inputs, shown, with the one value that is missing
4  what the law provides, what is established, what would settle it
5  what is established, and one question
```

Today the system jumps from 1 to 5 with nothing in between; that is the whole
"rigid" complaint. Rungs 2, 3 and 4 are the new behaviour.

The rung is chosen in Python from the sheet, not by the model reading prose rules,
and `prepare_answer` then puts only that rung's shape into the prompt.
`ANSWER_SYSTEM` currently carries six answer shapes and twelve absolute rules, of
which the model needs one shape per turn; cutting it to the relevant shape should
improve adherence to what remains, and is measurable against the same shape checks
used in the benchmark.

Scenario comparisons come free: `scenarios` from `understand_turn` feeds the
machinery that already exists, so conversation A turn 6 produces three real rows
instead of a table of dashes.

### 5. The answer step and repair

**Unchanged, on the evidence.** `reasoning_effort_answer` stays `"none"`. Single
pass, no outline step. The arithmetic prohibition, citation verification, coverage
checks and quoted-span stripping all stay as they are.

**What `prepare_answer` gains,** all from the sheet and all deterministic: the
selected rung's shape only, the assumption block, and the filtered ask-list. The
prompt gets shorter - and shorter again under §6, which moves the computed blocks
out of the model's hands entirely and so retires absolute rule 2 (transcribing
interpreter output into the table) and the formatting half of rule 4. Rule 1, the
arithmetic prohibition, stays exactly as it is: it governs what the model may
compute, not what it may format.

**Repair replaces withdrawal.**

```
_verify fails --> one repair call (~4.5s) --> re-verify
                        |-- passes --> SSE `replace` with the repaired answer
                        +-- fails  --> SSE `replace` with a sheet-built fallback
```

One attempt, never a loop.

**The fallback is rebuilt from the sheet.** Today `fallback_answer` is a static
template asking for "your age, your years of service and your salary history"
regardless of what is known - which means asking a member for three things sitting
in their own record. Rebuilt, it says what is established, what specifically
failed to verify in the member's own terms, and what would actually help.

**Streaming is unchanged.** Tokens go out immediately and a failure replaces them;
the SSE `replace` event already exists and the frontend already handles it.
Buffering to verify before showing would put full generation time onto TTFT on
every turn to smooth a path taken in 0 of 42.

**Two new deterministic checks in `guardrails.py`:** `ASSUMPTION_NOT_DISCLOSED`,
and a check that the out-of-Code portion of a two-part answer was referred rather
than answered. Both are string-and-state work; no model grades another model's
output.

### 6. Presentation

#### What is wrong in the render path

Four defects, found by reading `frontend/src/components/chat/ChatMessage.tsx`,
`ChatInterface.tsx` and the citation payload built in `prepare_answer`.

1. **Tables have no horizontal escape.** The answer sits in a `max-w-[80%]`
   bubble with no `overflow-x-auto` anywhere. The four-column comparison table
   this design produces most often squashes on a laptop and breaks the layout on
   a phone.
2. **Streaming re-parses partial markdown.** `setContent(aiContent)` fires on
   every token, so ReactMarkdown re-renders mid-table and the member watches
   literal `|---|---:|` pipes assemble before the row completes.
3. **The citation chips are dishonest, and partly dead.** They are built from
   *every retrieved section*, so an answer that cited two sections shows six
   chips, implying reliance the answer does not have. `ChatMessage.tsx` renders
   `cite.relevance_score` as a percentage, but `prepare_answer` never puts that
   key in the payload, so it silently never renders - dead code carrying a bad
   idea, since a retrieval score is not a confidence figure and must never be
   shown to a member as one. The chips are `cursor-pointer` with no click
   handler. The correct source already exists:
   `guardrail_report.verified_citations` holds exactly the sections the answer
   relied on and that passed verification.
4. **Pseudo-headings and one overloaded closing block.** `**How that is worked
   out**` is bold text, not a heading. The comparison sample ends with a single
   paragraph carrying projection assumptions, the election note, the §11-130
   disposition and two citations at once.

#### Python renders what Python computed

Today the model transcribes interpreter output into a markdown table, and
`ANSWER_SYSTEM` spends an entire absolute rule insisting that transcription is
not calculation. That request is withdrawn. The worked-steps table, the
comparison table, the assumptions block and the legal-basis footer are pure
functions of the working sheet: **Python renders them, the model writes prose
only.**

This extends the architecture's founding split by one step - the LLM plans and
writes, code calculates, and code formats what it calculated. Three consequences:
a table can never be malformed or mistranscribed; absolute rule 2 and half of
rule 4 leave the prompt; and the same figures can later be rendered differently
per device without touching the model.

**Streaming survives.** A template is an ordered list of slots, each `model` or
`computed`. The model emits a marker such as `<<<WORKED_STEPS>>>` where a
computed block belongs, and the streaming layer substitutes the rendered block as
it passes - so prose still streams token by token and the table appears instantly
and whole. If the marker never arrives, the block is appended after the opening
paragraph. Deterministic recovery, no lost data, and the fallback is a unit test.

#### The template registry

Not one universal format. The rungs from §4 supply the axis, and **the rung is
the status**:

| rung | status chip | slots |
|---|---|---|
| 1 | Calculated | lede, worked-steps, why-this-provision, alternatives, basis |
| 2 | Calculated, with assumptions | lede including the spread, worked-steps, what I assumed, why, basis |
| 3 | Needs one more detail | lede, the formula with the gap shown, what is needed, basis |
| 4 | More than one pathway | lede, each pathway and where the member stands against it, what would settle it, basis |
| 5 | Needs information | what is established, the one question |

Three further shapes carry no figure and so are not rungs: **legal explanation**
(plain-English answer, then what it includes and excludes, then basis),
**comparison** (lede naming the range, table, what changes, what is held
constant), and **partial** (the pension half answered, then the one-line referral
for the rest).

The registry lives in one module, one template per shape, each declaring its slot
order and which slots are optional. Adding a shape is adding an entry rather than
editing a 200-line prompt, and it is unit-testable without a network: a template
applied to a fixed sheet must produce a fixed slot list.

Real `###` headings replace the bold pseudo-headings, with the bubble's prose
heading scale tuned down so they read as section labels rather than billboards.

**The status chip is shown to the member**, at the top of the answer. The
difference between a settled figure and an assumption-backed one is exactly the
trust problem the external review raised, and it should be legible without
reading a paragraph.

#### Citations

- Inline brackets stay but thin out: one per claim that actually rests on a
  provision, never one per sentence.
- The chip row is rebuilt from `verified_citations`, so it shows what the answer
  relied on and what passed verification, and nothing else.
- The relevance percentage is removed. Each chip shows the section number and
  title, and expands to the quoted span the answer leaned on - which is what
  `cursor-pointer` has been promising and not delivering.

One constraint: `rehype-raw` is not installed, so raw HTML such as `<details>`
will not render. The collapsible "view the calculation" block therefore uses a
marker-driven component rather than widening the HTML surface.

#### Frontend work

Four contained changes, no new dependencies: wrap tables in `overflow-x-auto`;
render everything up to the last blank line as markdown and the streaming tail as
plain text so partial tables never render as pipes; rebuild the chip row from
verified citations; tune the prose heading scale. `npm run build` type-checks and
`npm run lint` currently sits at 7 pre-existing `any` problems - the bar is not to
add to that count.

Deliberately not done: no per-viewport card fallback for tables
(`overflow-x-auto` first, revisit only if it proves insufficient), and no
structured JSON answer envelope replacing markdown - the marker approach gets the
guarantees without giving up streaming or rebuilding the message renderer.

### 7. Testing

Unit tests, no network:

- `test_worksheet.py` - provenance transitions, `rests_on` invalidation, `withheld`
  versus genuinely missing, the "never ask for what you have" filter.
- `test_assumptions.py` - with the bug-class guard this repo favours: parse the
  policy table and require that every `assumable` entry has a default, a basis, a
  `plausible_range` and a worked example asserting its spread, and that every
  never-assumable input rejects a default. Same shape as the alternative-
  reachability guards in `test_planning.py` and `test_coverage.py`.
- `test_degradation.py` - rung selection is deterministic from a given sheet and
  monotone: adding a fact never selects a lower rung.
- `test_guardrails.py` additions - `ASSUMPTION_NOT_DISCLOSED` fires and does not;
  out-of-Code referral.
- `test_templates.py` - a template applied to a fixed sheet produces a fixed slot
  list; every rung and every non-rung shape has a template; every `computed` slot
  has a renderer; and the marker-recovery path places an orphaned computed block
  after the opening paragraph when the model omits its marker.
- `test_presentation.py` - the rendered worked-steps and comparison tables are
  well-formed GFM with the money columns right-aligned, and every figure in them
  comes from the sheet rather than from a string the model produced.

Frontend, via `npm run build` (type-check) and manual check of the four changes;
there is no test setup in `frontend/` today and this design does not add one.
`npm run lint` must not exceed its current 7 pre-existing problems.

Evals, against the live index:

- `evals/routing.yaml` asserting verdicts directly, seeded with the eight
  benchmarked cases. Two in the CRITICAL block: "thinking about retiring" must be
  in scope, and "actually it's more like 23 years" must recompute.
- `evals/conversations.yaml` gains conversations A and B, turn by turn, as the
  acceptance test.
- A regression guard specific to a bug this repo has already had:
  `understand_turn` must never decide the Article. It emits benefit types and
  scenarios; the Article stays `assess_coverage`'s job. `_ARTICLE_BY_NAME` nearly
  let "I am a park employee" bypass the coverage layer once already, and a
  model-driven router is a fresh opportunity for the same mistake.

### 8. Rollout

Ordered so the risky step follows the safe ones and the irreversible step follows
proof.

| # | step | risk |
|---|---|---|
| 1 | worksheet module, provenance, `rests_on` | none - pure, no behaviour change |
| 2 | "never ask for what you have" filter | tiny, immediately visible |
| 3 | repair plus sheet-built fallback | isolated to a branch taken 0/42 times |
| 4 | `understand_turn` behind a config flag, regexes still in place, A/B on the eval suite | the real risk - flag it, measure it, flip it |
| 5 | delete the dead regexes | irreversible, so only after 4 wins |
| 6 | assumption policy, materiality spread, disclosure check | moderate |
| 7 | degradation ladder, prompt slimming | measurable against the shape checks |
| 8 | frontend render fixes: table overflow, streaming tail, chips from `verified_citations`, heading scale | low, and independent of everything above |
| 9 | template registry, computed-block rendering, status chip | moderate - changes every answer's composition |

Steps 1-3 are worth shipping on their own even if step 4 never lands, and step 8
is independent of all of them: the four render fixes are worth doing whatever
happens to the rest of this design, because three of them are defects rather than
improvements. Step 9 depends on the working sheet (step 1) and the degradation
ladder (step 7), because the template registry is keyed on the rung.

### 9. Risks

- **Latency.** +2.3s on turns where the planner call is currently skipped.
  Mitigated by keeping the concurrent pair, skipping `resolve_inputs`' extraction
  when `revised_facts` covers it, and keeping the fast-path deny. The config flag
  makes it revertible.
- **Fail-open scope** lets more junk reach the pipeline. That costs money and
  latency, not correctness; the verifier is untouched.
- **Assumption creep** is the real correctness risk, held by three independent
  limits: the never-assumable list, the two-assumption ceiling, and the disclosure
  guardrail. Weakening any one leaves the other two binding.
- **Prompt slimming could regress adherence.** Measurable with the same harness;
  revert if it does.
- **Deleting the regexes gives up a free fast path.** Accepted: that fast path
  refused 3 of 8 turns in a plausible conversation.
- **The computed-block marker can go missing or land in the wrong place.** The
  model may omit `<<<WORKED_STEPS>>>`, emit it twice, or put it before the lede.
  Recovery is deterministic - first occurrence wins, duplicates are dropped, and
  an omitted marker appends the block after the opening paragraph - and each of
  those three cases is a unit test rather than a hope.
- **Rendering the streaming tail as plain text changes how an answer looks while
  it arrives.** Emphasis and headings in the final sentence will pop into place
  when the block closes. That is a smaller cost than watching table pipes
  assemble, but it is a visible change and worth checking on a real device before
  step 8 lands.

### 10. Explicitly out of scope

- No re-chunking and no re-ingestion. Retrieval is not the problem and there is no
  evidence that it is.
- No changes to the DSL interpreter, validator or risk assessor.
- No separate "legal critic" layer. The external review's proposal there is
  already served by `applicability.py` plus `alternatives_considered`, both of
  which the probes showed working correctly.
- No new frontend dependencies, no message-renderer rewrite, and no structured
  JSON answer envelope replacing markdown. The frontend work is the four
  contained changes in §6; SSE `replace` already exists and is already handled.
- No per-viewport card fallback for wide tables. `overflow-x-auto` first, and
  revisit only if it proves insufficient on a real device.
- No `rehype-raw`. The collapsible "view the calculation" block uses a
  marker-driven component rather than widening the rendered HTML surface.
