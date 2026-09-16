# Design: a RAG-first general agent

*2026-09-16. Supersedes `2026-09-16-general-agent-design.md`.*

## The principle

The assistant is a general conversational RAG agent over the Illinois Pension
Code. **Retrieval is its environment, not one of its tools.** Calculation is one
capability it may invoke, alongside the capabilities still to come. The model
owns understanding, investigation, tool selection and composition. Deterministic
systems own facts, evidence integrity, calculation, validation and security.

Every decision below is tested against that sentence.

## 1. What this supersedes, and why

The predecessor diagnosed the right problem — one evidentiary standard, the
binding determination, applied to every sentence — and introduced four
foundations to fix it: register, ledger, subject, view.

Three of those survive. **The register does not.** Asked what actually separates
its eight values:

| registers | what really separated them |
|---|---|
| `explain` / `reflect` / `boundary` | only which **provenance class** a claim may have |
| `estimate` / `illustrate` / `suppose` | only the nature of the **subject** |
| `requirements` / `recommend` | they are **capabilities**, not answer types |

The taxonomy was a redundant encoding of three things the design already had.
Worse, it was model-declared, and this codebase's history is a catalogue of
model-declared flags going wrong — `stand_in_facts` set when the member had not
asked, `find_provision` cited without being read. What actually stops a bad
figure is that `compute_benefit` refuses to read the record for a generic
subject, and that the figure must trace to engine output. Both are enforced
further down than a declaration. **The register was the weakest link in its own
chain.**

Two further findings, both measured today, are in §3 and §6. They changed more
than the register did.

## 2. What replaces it

Three dimensions, each independently decided, none declared by the model as a
routing choice.

### 2.1 Claim provenance — what verification checks

Every material claim in an answer is classifiable, by extraction rather than by
declaration:

```
STATUTE_CLAIM  USER_FACT  PROFILE_FACT  DERIVED_FACT
CALCULATION_RESULT  ASSUMPTION  HYPOTHETICAL  TOOL_RESULT  UNKNOWN
```

The verifier asks *"does this claim have acceptable provenance?"* rather than
*"was this sentence written under the correct register?"*. A citation is found
and tested for membership in the retrieved set; a figure is matched against
engine output. Neither asks the model what it thinks it said.

This is what makes capabilities cheap to add: a disability tool's output is
`TOOL_RESULT` with provenance, not six new guardrail mappings.

### 2.2 Subject — who a claim is about

Each subject carries its own facts, provenance, coverage and Article, is
addressable by name for the session, and its ledger is starved of every other
subject's facts exactly as `_available_facts` starves record facts under a
supposition today.

This names what three existing durable keys have in common: `hypothetical_frame`
is one derived subject, `scenario_set` is subjects differing in one fact,
`baseline` is "me, retiring today". N-way comparison falls out.

### 2.3 Capability contract — what a tool may read and must return

A capability is: a **tool**, a **structured result with provenance**, and a
**validation contract**. `compute_benefit`'s contract includes which subjects it
may read the record for — which is where G38 is prevented, at the input, rather
than by an answer-level flag.

## 3. The finding that changed the architecture

`2026-09-16-general-agent-design.md` assumed explanation turns were the cheap
majority and calculation the expensive minority. Both halves are false.

**The mix is unknowable without real traffic, and every estimate of it
disagrees.** Classifying by whether `compute_benefit` actually ran:

| sample | figure-seeking |
|---|---|
| 100 diverse questions, half seeded from section headings | **13%** — and 0% for the corpus half alone |
| the situation-seeded half of those 100 | 26% |
| 23 questions drawn from the eval suite and the reported transcripts | 52% |
| the reported transcripts alone | 60% |

Zero to sixty, decided entirely by how the questions were sampled. No number in
that column is member traffic, and the architecture must not rest on any of
them.

**Which is survivable, because the mix barely matters.** Measured directly at
n=100, the two classes cost the same: **10.56s for calculation, 10.89s for
everything else.** Resampling the smaller set across the whole mix range moves
the median only from 18.40s at 0% calculation to 15.47s at 100% (local). The
median is flat because the classes are equally expensive, so the design is
robust to a number nobody can pin down.

**Because explanation turns are the unbounded ones.** In-region, baseline, and
the round-trip gap widens as the questions get more diverse:

| | TTFT p50 | round trips p50 | worst |
|---|---|---|---|
| called `compute_benefit` | 6.84s | 4.0 | 6 trips |
| no calculation | **9.68s** | **5.0** | **9 trips, 26.3s, 35 citations** |

At n=100, **nine of the ten worst turns did no calculation at all.** The worst
was "I moved from one Illinois pension fund to another" — nine round trips and
thirty-five citations, for a question with no arithmetic in it.

**Calculation has a terminating condition and explanation does not.**
`compute_benefit` either returns a figure or names exactly what is missing;
either way the turn is over. Nothing played that role for an explanation, so the
model browsed until a budget stopped it:

| question | round trips | what it did |
|---|---|---|
| "what is Article 11?" | 5 | `outline` + five `get_section`, **never searched** |
| "i work at a school as a teacher, how is my pension calculated?" | 7–10 | `check_coverage`, `outline`, five `get_section` |

The first is the "browsed, not looked" pathology this codebase documents, on the
simplest question in the set.

**So the fast path is not "explain is cheap". It is "explanation needs a
stopping rule."** That is §6.

## 4. Conversation state

Durable, structured, and presented to the model **as authoritative state above
the transcript** rather than left to be inferred from it. "Actually I was hired
in 2015, not 2012" must supersede the earlier statement without the model having
to reason about which sentence is newer.

```
subjects:
  me:
    total_service_years   21.5        record
    final_average_salary  72743.62    record
    retirement_age        60          member_stated (turn 4)
    withdrawal_date       2026-12-31  statutory_parameter §9-134
objectives:
  maximise_monthly_benefit            user_stated (turn 6)
```

Precedence **member_stated > record > derived > statutory_parameter**.
Supersession rather than overwrite, so a correction leaves an audit trail.

This promotes structures that already exist and are discarded every turn.
`_input_origins` (`nodes.py:692`) already classifies each input as `assumed`,
`stated`, `derived` or `record`, computed by code at resolution time. Two things
are wrong with it: `_stated_in` consults only the current message, and
`input_sources` is not in `DURABLE_KEYS` (`state.py:236`). That is the whole
mechanism behind values stated two turns ago being forgotten.

**Durable is not the same as in-prompt.** A state selector decides what reaches
the model each turn. Measured first-token latency against prompt size is 1.72s
at 688 tokens rising to 4.14s at 42k, so an accumulating ledger placed wholesale
into every prompt silently buys back the round trip §6 removes.

## 5. Retrieval-first

> **Retrieval-first is the default execution path. The agent receives corpus
> evidence before its first reasoning call. It may then answer directly, or
> enter an agentic investigation loop when that evidence is insufficient.**

```
user message
   ├── conversation state (selected)      ┐
   └── prefetch: query + recent turns +   ├─► ONE model call ─► answer
       active subject → hybrid search     │         │
       → one-hop reference closure        ┘         └─► tools ─► loop ─► answer
```

The prefetch is not routing and not a mandatory tool decision. It is the
environment the agent operates inside. The model still chooses every tool,
including searching an Article the prefetch did not cover.

**Retrieval scope follows the subject, not coverage.** `_search_filter`
(`loop.py:394`) resolves one Article — named by number, then a premise about the
member, then the coverage verdict, then the record. A subject described in words
moves none of them, which is why a question about teachers searched the member's
own Article. Scope becomes the union of the Articles of all active subjects,
plus cross-cutting.

Naming a fund in words creates or updates a *subject*; it does not touch the
member's coverage. `_ARTICLE_BY_NAME` refuses to match fund Articles by name so
that "I am a park employee" cannot bypass the coverage determination — that
protection now lives in the capability contract and the coverage claim check
rather than in the retrieval filter.

**The prefetch query is built from the conversation**, not the bare last
message. "what are the things needed, please list them" retrieved
investment-adviser rules under §1-113.5 because nothing anchored it.

## 6. Termination: the evidence closure contract

The analogue, for explanation, of what `compute_benefit` provides for figures.

**An explanation terminates when its evidence is closed:** every section the
retrieved set cross-references, one hop out, has itself been fetched. This is
deterministic, needs no model call, and `_unread_references` and `_read_sections`
already exist in `loop.py`. Closure either succeeds or names the sections it
could not reach — the same shape as "here is the figure, or here is the missing
input".

After prefetch and closure, the model gets **one tool round**. It may use it for
anything, including searching another Article, and the results reach the answer
because composition continues the same history. What it may not do is browse
indefinitely.

**Measured, in-region, 23 questions:**

| strategy | TTFT p50 | round trips | citations | guardrail |
|---|---|---|---|---|
| baseline | 7.98s | 4.0 | 12.0 | 22/23 |
| prefetch, cap 1 round | 3.49s | 2.0 | **8.0** | 23/23 |
| **prefetch + closure, cap 1** | **3.84s** | **2.0** | **13.0** | 22/23 |
| prefetch, browsing withdrawn after round 1 | 6.16s | 3.0 | 11.0 | 23/23 |

Capping without closure is faster and **cites a third less** — fast because it
answers with less, which is not a win. **With closure it is 2.1× faster than
baseline while citing more than baseline**, because the closure fetches
cross-referenced sections the model never got round to reading.

It also repaired a failure it was not aimed at: *"explain about a person who is
a teacher"* went from 8.76s with a **failed** guardrail — the "I have taken that
answer back" case — to 6.05s, 16 citations, passing.

### 6.1 The cap is conditional on the prefetch — a hard dependency

Three turns collapsed to 1–3 citations under the winning strategy. All three are
exactly the turns where `classify_self_reference` fires and the prefetch is
skipped:

| question | act | citations |
|---|---|---|
| "i work at a school as a teacher…" | asserted | 1 |
| "if I were a Chicago police officer…" | stipulated | 3 |
| "what if i were a firefighter…" | stipulated | 2 |

The skip is correct — coverage may still move, and a filter built on the record
would seed another fund's law. But the cap still applied, leaving those turns
with no prefetch *and* no iterations to recover.

**So §5's subject-scoped retrieval is a prerequisite for §6's cap, not an
independent step.** Capping is only safe once the prefetch can target the right
Article. Where the prefetch is skipped, the cap lifts and the full loop runs.

### 6.2 What termination does not fix

Hedging was flat across every strategy — 7, 8, 9 and 10 hedge phrases
respectively. **Termination is a latency and evidence property, not an
epistemic one.** Refusing to assume, refusing to illustrate, and answering "how
is my pension calculated?" with paragraphs about what is missing are §7's
problem, and nothing here touches them.

How big that problem is, measured over the 100: **33 of 100 answers hedge** —
they contain "could not establish", "cannot state", "is not available" or a
close relative — and every one of those 100 passed verification. So this is not
the guardrail retracting answers. It is the agent, unretracted and unchallenged,
declining to say a third of what it was asked. That is the number §7 has to
move, and no amount of §6 will move it.

## 7. Assumptions

### 7.1 Shipped, 2026-09-16 — and what was already built

All seven pieces below are implemented, offline-tested and merged
(`docs/superpowers/plans/2026-09-16-assumptions.md`). One thing this section
asked for did not need building.

**The statutory parameter registry already existed.** `dsl_types.ParameterSpec`
is a value plus `source: {section, subsection}`; `derivation.py` refuses any
constant without a citation; `validator._check_citations` refuses a citation to
a section nobody retrieved; `formula_registry.rule_specification` persists them
with the recipe's whole lifecycle; `thresholds.py` already reads them back by
name. Discovered, persisted, reusable, citable — all four, already there.

The gap was narrower than the prose above suggests: a parameter was usable only
*inside* its own recipe's calculation tree, so an Article that states the age
for a full annuity still left the agent unable to establish a retirement age.
`calculation/statutory.py` is a resolver over evidence already on hand — no new
table, no migration, no regex, no model call. **Do not rebuild the registry.**

What shipped:

| | |
|---|---|
| `statutory_parameter` as rank 4 | `worksheet.py`, `ledger.PRECEDENCE` |
| a fill computed from known facts is `derived`, not `assumed` | `AssumptionRule.derives` |
| the ceiling counts **inventions**, not fills | `nodes.apply_assumptions` |
| cited thresholds fill a gap, with the section | `calculation/statutory.py` |
| governing gaps vs value gaps | `agent/gaps.py` |
| the four-row contract | `nodes.gap_verdict`, `tools._describe` |
| an unknown tier is priced both ways | `nodes.resolve_recipes`, `blocks.render_branches` |
| three ranks, three headings in the answer | `blocks.render_assumptions` |

**Two things the implementation settled that the design did not.**

*The ceiling was counting the wrong thing.* `months_before_age_60` is computed
from an age the member stated, and it spent one of the two slots — so a member
could be refused a figure for want of a slot they themselves had paid for.

*Branching belongs to the registry, not to derivation.* Derivation reads the
statute, not the member, so branching it would spend two model calls producing
one recipe. And two branches are only two provisions when the rows differ: one
recipe answering for either tier prints one figure, not the same figure twice.

**Not measured.** Every claim here is unit-tested behaviour. Whether it moves
the 33-in-100 hedging rate needs the index and the models, and that is §12's
work, not this one's.

**The ladder.** member_stated → record → derived → statutory_parameter → *never
invented*. Ranks 3 and 4 are where the current agent gives up: a retirement date
derivable from a stated age is not an assumption, and an Article's own normal
retirement age is citable rather than guessed.

They are **statutory parameters**, not defaults — rates, thresholds, ages,
minimums, averaging periods. Naming them "defaults" invites treating statutory
constants as generic filler. Built on `formula_registry`'s lifecycle:
discovered from retrieved text, persisted, reusable, citable.

**Value gaps and governing gaps.** A value gap — salary, service, a date —
changes the number: assume, flag, proceed. A governing gap — tier, Article,
employment status — changes which provision applies: ask, or compute both
branches and compare. This extends an existing rule from conflicts to absences:
*"a conflict in a routing field is a question; a conflict in a value stays an
override-with-note"*.

The transcripts contain the proof. Asked to calculate with an unknown tier, the
agent correctly identified the branch — "If you are Tier 1… If you are Tier 2…"
— and refused to compute either. The right behaviour is both branches, computed,
side by side.

**The gap count is a contract on the calculation capability, not a
conversational policy.** It governs what `compute_benefit` may fill in:

| ledger state for this subject | behaviour |
|---|---|
| no gaps | compute |
| 1–2 value gaps fillable at rank 3 or 4 | compute, assumptions named |
| more than 2, or fillable only by invention | worked example on statutory parameters, plus what would make it theirs |
| any governing gap | ask, or branch and compare |

**Assumptions are counted, never declared.** Their number and identity come from
ledger provenance, so "what I assumed" cannot be wrong and the ceiling cannot be
evaded. `materiality_threshold` (0.02) already decides whether an assumption
reaches the opening sentence.

**Where this stops.** The Code states rates and periods; it does not contain
typical salaries. A salary can be derived from a member's history, taken from
their words, or used as a labelled illustration figure — there is no citable
standard salary. That is the corpus's edge, not the architecture's rigidity.

## 8. Verification

Per-claim, by provenance, with a downgrade ladder. Today a hard failure replaces
the entire answer, which is how a sound explanation of teacher pensions became
"I have taken that answer back — sorry."

| failure | consequence |
|---|---|
| a citation is not in the retrieved set | drop the sentence's block |
| a coverage claim is unsupported | drop the claim, keep the rest |
| a hypothetical reads as fact | re-render that block |
| **a figure does not trace to engine output** | **retract the answer** |

An untraceable figure remains the one failure that retracts everything, because
it is the one that harms a member.

**Incremental, during the stream.** A citation is checkable the instant it
appears — set membership, no model call, microseconds. Buffer to a sentence
boundary; if a citation names a section outside the retrieved set, stop the
stream. That bounds the damage to one unsent sentence rather than a retracted
answer, and it enforces grounding at the moment it becomes checkable.

**Built and measured, 2026-09-16** (`agent/grounding.py`). The buffering cost is
below run-to-run noise: **8.69s p50 against a 9.71s baseline on the same five
questions**, in-region. And it fired on live traffic — one stream in ten was cut
at a citation that had never been retrieved, on a question that passed on its
second run. That is model variance, and it is exactly the case the gate exists
for: before this, the whole answer streamed and was retracted at `done`.

A boundary needs whitespace after the full stop, because `"2."` followed by
`"40%"` is a rate arriving in two deltas and releasing eagerly would show the
member `"2."` as a finished thought.

Still deferred: falling back to *investigation* on a cut, rather than to the
existing repair-then-fallback path. And the per-block downgrade ladder above
depends on blocks carrying registers, which §1 dissolved — it needs redesigning
against claim provenance before it can be built.

**A regression this design introduces, and must fix.** `loop.py` pushes back
once when the model declares itself done having read nothing — the guard that
exists because the loop once answered from memory that Article 11 is the
Municipal Employees' fund, and the guardrail passed it because an answer with no
citations has nothing to verify. **Prefetch populates the retrieved set before
the loop starts, so that guard can never fire again.** The invariant must change
from "sections exist" to "the answer's substantive claims participate in the
evidence available" — which the incremental check above enforces directly.

**Delete the record-attribution prose.** "I'm going by your record…" is prose in
the answer prompt (`nodes.py:1491`) with a prose condition, asking the model to
police itself. It becomes a code-rendered block firing only when coverage rests
on the record and the member has not already confirmed.

## 9. Composition and streaming

**Composition is a finalization boundary, not a second mind.** It does not plan
or reason; it assembles verified evidence, calculation results and views into
the context for the final response, which the same history continues.

On the fast path there is nothing to assemble — no figures, no blocks — so the
prefetched call writes directly. Blocks derived from tool results are already in
the history. Only blocks needing extra engine work, such as pricing a threshold
the member is walking toward, need the boundary, and those can be computed
concurrently with the tool call that triggers them.

**Views: a fixed vocabulary the model names and code fills**, extending
`MarkerSubstituter`, which already discards anything the model writes in a
marker's place. `<<<COMPARISON>>>` `<<<SENSITIVITY>>>` `<<<BREAKDOWN>>>`
`<<<TIMELINE>>>` `<<<REQUIREMENTS>>>`. The model never writes numbers into a
view. Views render from state that exists before generation, so each is its own
SSE event the moment it is ready — rather than riding on `done` behind the full
generation and verification, which is the current 5–10s lag before the
calculation card and citations appear.

**Progress past four seconds.** A quiet indicator from zero; real steps after
four seconds — the actual query, the actual section heading, the actual
derivation — emitted with `get_stream_writer()` (langgraph 1.2.11) from the same
state the answer comes from, never a second model call. Fast turns show none of
it. This also repairs `graph.py:256`, which emits stage labels from LangGraph's
`updates` stream — fired on node *completion*, so the longest wait displays the
step that already finished.

**Progress events are not tokens.** See §10.

## 10. Latency

**The KPI is time to first grounded token.** Not first token: a fused call could
stream "Let me check that provision" at 1.2s and make the dashboard look fixed
while the answer still takes seven seconds. Progress events do not count.

Today's measurements are already TFGT, because the answer stream begins only
after retrieval and the loop have run.

| | measured in-region |
|---|---|
| **median** | **3.84s** (baseline 7.98s) |
| calculation turns | 3.77s |
| explanation turns | 3.85s |
| round trips | 2.0 (baseline 4.0) |
| citations | 13.0 (baseline 12.0) |

Under five seconds on both classes, **measured rather than projected**, and no
longer dependent on the question mix.

Remaining slow paths, each honest about doing more work: a fresh premise with no
prefetch runs the full loop; a first-ever recipe derivation costs ~26s and shows
what it is deriving.

### 10.1 Shipped, and what it actually measures

Built 2026-09-16: the contextual query, the prefetch, the closure and the
conditional cap. Verified in-region on the five `bench_ttft` questions, which
have baselines at two earlier points:

| | TFGT p50 | round trips p50 | citations p50 | guardrail |
|---|---|---|---|---|
| before any of this work | 9.71s | 3–4 | — | — |
| with the streaming gate only | 8.69s | — | — | — |
| **shipped** | **5.39s** | **2.0** | **14.0** | **10/10** |

**Not the spike's 3.84s, and the difference is the metric.** That figure was
time-to-first-*delta* on a wider 23-question set that included two free
declines. This is time-to-first-*grounded*-token — the gate's sentence
buffering is inside it — on five questions with no declines among them. Against
the same metric on the same questions, 8.69s → 5.39s.

**Every prefetched turn took exactly two round trips.** The three-trip turns
were the ones where the prefetch was skipped, which is §6.1 working rather than
failing.

**A concrete next win, found by that check.** The skip is driven by
`classify_self_reference`, which reads *"what would my pension be **if I
retired now**?"* as a stipulation. It is a timing hypothetical, not a supposed
job, and nothing about it risks seeding another fund's law — but it loses the
fast path. Narrowing that classifier, or deciding the skip on whether the
supposition names an *Article* rather than on whether it is a supposition at
all, would bring the largest remaining class of turns onto the two-trip path.

**The guard.** A benchmark someone has to remember to run will not hold this.

1. A unit test with a fake LLM asserting **model round trips before the first
   grounded token**: at most two on the prefetched path. No network, runs in CI,
   fails the moment a step is added to the critical path. Round-trip count is
   what regresses silently; wall-clock is its shadow.
2. `bench_ttft.py --budget`, reported **per query class**, not as one average.

## 11. Capabilities

A capability is a tool, a structured result with provenance, and a validation
contract. The core agent does not change when one is added.

| capability | result | fits |
|---|---|---|
| survivor and disability benefits | `CalculationResult` | yes — recipes only |
| post-retirement increases | `CalculationResult` | yes |
| benefit-statement upload | `DocumentFacts` | yes — a new provenance tier |
| Reciprocal Act aggregation | `CalculationResult` | yes |
| administrative policy ("how do I apply") | `Evidence` | **needs a second corpus** |
| statute version comparison | `Evidence` | **index holds current text only** |

### 11.1 Recommendation

A first-class capability, because decision support is a core use case — not a
register and not a fixed workflow.

> **Recommendation = selecting or highlighting an option according to an
> explicit or inferable objective, supported by corpus facts and deterministic
> capability outputs.**

`objective` is conversation state, not a routing decision:

```
explicit objective          → use it
strong contextual objective → use it, and say so
ambiguous                   → give conditional alternatives
genuinely required          → ask
```

"Which retirement age gives me the highest monthly pension?" states an
objective. "Should I retire now?" does not, and the answer is not to block but
to condition: *"If your priority is the highest monthly pension, waiting
produces X. If leaving sooner matters more, now produces Y, with the reduction
under §Z."* The member remains the decision-maker.

**Not every recommendation needs two calculations.** "Which benefit option is
available to me?" may be entirely legal. Recommendation is a reasoning
capability that *may* invoke calculation, not calculation with a different
label. Candidate options are generated by the model — "the Code treats 58 and 60
differently, so those are the meaningful alternatives" — and validated and
priced by tools.

**A deterministic consistency check.** The recommended option must exist among
the computed candidates and must actually satisfy the stated objective. A model
saying "I recommend 58 because it gives you the highest pension" against
computed values of 58 → $2,850 and 60 → $3,240 is rejected by arithmetic, not by
style.

Excluded, because they are not in the corpus: life expectancy, tax, investment
return, health, risk appetite. The agent may say so and stop there.

## 12. Evaluation

The current suite is calculation-shaped — 46% of its turns expect a figure — and
would not have caught the finding in §3.

Added, as a first-class suite:

- **General understanding** — "what is Article 11?", "explain this section",
  "what's the difference between X and Y?", "does subsection (f) override (a)?"
- **Conversational continuity** — "how does that affect me?", "actually make
  that three", "go back to the firefighter example"
- **Retrieval intelligence** — weak first retrieval → reference traversal →
  answer
- **Safety and grounding** — uncited claim, wrong Article, conflicting member
  fact, unsupported figure
- **Latency by class**, reported as a distribution, not an average

And one flagship product test: **100 diverse member questions, uncategorised**,
measured for whether the agent understood, retrieved the right law, answered
naturally, asked only when necessary, calculated when useful, avoided
calculating when not, kept context, and how long until the first grounded token.
Run once at n=100 on 2026-09-16; §3 and §15 carry the result. It moved the mix
estimate from 52% to 13% and surfaced the hedging rate, so **it earns its place
as a recurring product test rather than a one-off** — and the next run should be
on real member questions, which is the one input this test has never had.

## 13. Sequencing

Dependencies are real, not preferences.

1. **Ledger durable, provenance across turns** — foundation
2. **Subject-scoped retrieval and conversational query rewrite**
3. **Per-claim verification, incremental during the stream; the `pushed_back`
   invariant replaced**
4. **Reference closure before the first model call** — needs 2
5. **The conditional cap** — needs 2 and 3
6. **Assumption ladder, statutory parameters, capability contracts**
7. **Views and streaming ahead of prose**
8. **Subjects, comparison, objectives, recommendation**

Steps 4 and 5 are the latency win and they come *after* 3 deliberately: a faster
path must not be able to retract more than the slow one did. Step 5 needs 2
because a cap applied where the prefetch was skipped starves the turn (§6.1).

## 14. Accepted risks

**The cap can starve a turn that genuinely needs to investigate.** Measured on
one question — "which sections must be consulted before my benefit can be
calculated" dropped from 19 citations to 6 and lost its guardrail. Requirements-
shaped questions need more than one round, and the escalation rule in §6.1 must
cover them, not only the self-reference case.

**Prefetch runs on questions that should never have reached it.** "i want an ice
cream" costs two round trips and cites fourteen sections to refuse, against
`level1_regression.yaml`'s documented intent of "one cheap routing call".

**Hedging is untouched by everything in §5 and §6.** It is §7's work, and until
§7 lands the agent will be fast and still say "I could not establish".

**The mix estimate rests on biased sources** — a suite written around
calculation and transcripts collected to show failures. The flatness of the
sensitivity curve is what makes the architecture robust to that, not the point
estimate.

## 15. What the numbers rest on

Measured 2026-09-15 and 2026-09-16, run as a Container App Job inside Azure
against the live models, index and database. Probe code discarded; this is its
output.

**Termination strategies, 23 questions each, in-region** — §6's table, plus a
first run that agreed within run-to-run variance (baseline 8.23s, closure+cap
4.72s, cap-only 3.88s, no-browsing 6.66s).

**Question mix**, classified by whether `compute_benefit` ran: 13% over 100
diverse questions (0% for the 50 seeded from section headings, 26% for the 50
seeded from member circumstances); 52% over 23 drawn from the eval suite and the
reported transcripts; 60% over the transcripts alone. The eval suite self-labels
46.2% of its 158 turns. **None of these is real traffic**, and the spread is the
finding: §3 rests on the classes costing the same (10.56s against 10.89s at
n=100), not on any point estimate. Median TTFT across the full simulated mix
range spans only 18.40s → 15.47s (local).

**The 100-question run, in-region, on current code:** overall TTFT p50 10.85s,
guardrail 100/100, **33/100 answers hedged**, and nine of the ten worst turns
did no calculation.

**Corpus composition**, 2,111 distinct sections sampled: 57.3% carry no
calculation-relevance tag at all; `eligibility_rule` 11.3% outnumbers
`benefit_formula` 10.6%.

**Moving search before the first model call**, on its own: 9.71s → 7.59s,
round trips 3.0 → 2.0, guardrail 10/10. Necessary and not sufficient — the best
two-round-trip turn was 6.01s, of which 5.96s was the two model calls
themselves. That is why closure and the cap exist.

Three negative results, recorded so they are not re-litigated:

- **`gpt-5-mini-1` is slower than `gpt-5.6-luna-1` here** — 1.29s against 1.01s
  on an identical tiny prompt, eight loop calls where luna took two, and worse
  end to end. "Smaller" is not "faster" across a model generation.
- **Prompt caching is a cost lever, not a latency lever.** luna cached 2,629 of
  2,632 prefix tokens and the warm call was *slower* than the cold one (1.53s
  against 0.97s). `gpt-5-mini-1` cached nothing.
- **Capping without closure buys latency with evidence** — 3.49s but citations
  fall from 12 to 8.

First-token latency against prompt size, streaming: 1.72s at 688 tokens, 2.20s
at 7.9k, 2.82s at 15.8k, 3.57s at 26.3k, 4.14s at 42k. Shallow — trimming
context is worth far less than removing a round trip, which is why §4's state
selector is about not *undoing* §6 rather than about winning time.

The checkpoint write (`SqlAlchemyCheckpointSaver.aput`) measured 0.97–1.93s
between composition and the answer call. It can be fire-and-forget during the
stream, as `registry.note_reuse_later` already is for reuse counts.
