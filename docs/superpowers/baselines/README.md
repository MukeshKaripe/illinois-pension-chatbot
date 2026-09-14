# Baselines

What the pipeline scored before the agent loop existed, so "the loop is better"
is a measurement rather than an impression.

Cutover (spec §7) compares the loop against `2026-09-13-pipeline.json`.

The criteria:

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

## Report shape

`{"mode": "pipeline" | "loop", "cases": [...]}`. The wrapper exists so a report
can say which agent produced it; a future reader takes `["cases"]`.

## What the first run found

Criterion 4 above is not a formality. Running the three new suites against the
pipeline on 2026-09-13 showed **five of ten cases were wrong about the system
they were written to measure**, and every one of those errors would have been
invisible if the suites had simply been committed unrun:

- **L01 and L03 passed while testing nothing.** Both asked for a future-dated
  figure; both got the retire-now figure back. `calculation: true` and
  `mentions: ["assum"]` are satisfied by the wrong answer, because the
  retire-now figure carries its own "assuming you withdraw today" disclosure.
  L03 was the sharper find: it computed on `age_at_annuity_start 58` with
  `months_before_age_60 44` and `service_years 21.5` — 44 months is the
  reduction for a 56-year-old — so the figure wears the 58 as a label over
  arithmetic that contradicts it. Both now assert `service_years: 23.5`.
- **L08 named the wrong Article.** Chicago police is Article 5; the case said 4.
  And the real failure sat upstream of the assertion: the hypothetical frame was
  created with `article: None`, so retrieval never left Article 11 and the
  answer offered the member's own law for somebody else's question.
- **L09 was marked `active` and fails.** "What is Article 11?" retrieves
  11-178, 11-149, 11-221.1 and never the Article's own opening sections —
  hybrid search has nothing to match in 11-101 "Creation of fund". It is now an
  `expected_fail` pointing at the Phase 1 `outline` tool.
- **L04, L07 and L10 pass**, and are `active` as regression guards. L10 settles
  complaint #7 empirically: a derived recipe *is* reused on a re-asked question.

The lesson is worth keeping: a case written from a diagnosis rather than a
measurement tends to assert the diagnosis. The `gap:` on each `expected_fail`
now records what the pipeline actually did, not what it was assumed to do.


## The first full-suite attempt: the machine went to sleep

`2026-09-13-pipeline-INCOMPLETE.json` is kept as evidence, not as a baseline.
The first 38 cases ran clean; **every case from index 38 onward failed**, most
with no token usage recorded at all:

```
. . . . . X . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .
X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X
```

The Windows event log settles it. The run was 01:13:09 to 01:53:46, and:

```
01:31:11  Kernel-Power 506  The system is entering Modern Standby
01:31:17  Kernel-Power 172  Connectivity state in standby: Disconnected
01:53:42  Kernel-Power 172  Connectivity state in standby: Connected
```

The lid closed 18 minutes in. Network went down at 01:31:17 — which is case 38
— and came back four seconds before the run ended. `APIConnectionError` is in
`llm.py`'s `_RETRYABLE`, so every turn retried into a network that was not
there and then gave up.

Nothing regressed, and nothing was throttled: the three category suites ran
healthy an hour earlier, and stripping the connection errors leaves only a
handful of genuine case failures.

**The lesson is mundane and worth writing down anyway:** a full-suite run is
~40 minutes of continuous network, so it needs the machine kept awake —
`powercfg /change standby-timeout-ac 0` for the duration, or simply not closing
the lid. Re-running by `--category` also works and has the side benefit of a
report per category, which is the shape cutover compares anyway.


## The pipeline baseline: 2026-09-13

Complete, clean, and the column the loop gets measured against.

```
99 cases · 156 turns · 2,011,268 tokens · 79/92 scored cases passed
TTFT median 12.17s   min 1.88s   max 53.11s
7 known gaps: G24 G27 G40 L01 L05 L08 L09
3 turns with a hard guardrail rejection
```

**All seven `checked` funds pass.** Every one of the eight `fund_smoke`
failures is an `experimental` fund (2, 3, 4, 10, 14, 15, 16, 18), which is what
that label means — statute answers sound, a figure possibly missing or not yet
checked. Three experimental funds pass anyway (13, 17, 19). So criterion 3 reads
as **no `checked` fund regresses**, not as 18/18.

### Two things a cutover run must know

**The CRITICAL gate has a flaky member.** E90 failed this run on
`ASSUMPTION_NOT_DISCLOSED` at turn 3 — the survivor question — and passes on
re-run with nothing changed. The check requires a topic cue *and* an assumption
signal in prose the model writes freshly each time, so whether it fires is a
matter of wording. **A failed CRITICAL case must be re-run before it is read as
a regression.** Criterion 1 is otherwise a coin toss on a four-turn case.

**Generation errors are scattered, not systemic.** L11 and G15 each returned "I
found the relevant sections but could not finish writing the answer", and both
pass on re-run. Two isolated failures in 156 turns is the transient rate; the
signature to worry about is the *contiguous* one below, where every case from
some point onward fails with no token usage at all.

### The failed first attempt, kept for its lesson

`2026-09-13-pipeline-INCOMPLETE.json` is the run that died. The Windows event
log settles why: the lid closed 18 minutes in.

```
01:31:11  Kernel-Power 506  The system is entering Modern Standby
01:31:17  Kernel-Power 172  Connectivity state in standby: Disconnected   ← case 38
01:53:42  Kernel-Power 172  Connectivity state in standby: Connected
```

`APIConnectionError` is in `llm.py`'s `_RETRYABLE`, so every turn retried into a
network that was not there. It was diagnosed as an Azure quota exhaustion first,
and that was wrong — worth remembering, because the two look identical from
inside the report and only the event log tells them apart.

A full run is ~40 minutes of continuous network. Idle standby is already
disabled on this machine (`standby-timeout-ac` and `-dc` both 0), so nothing
sleeps on its own — but a lid close triggers Modern Standby regardless.


## Phase 3b: both modes, five categories, 2026-09-13

`2026-09-13-phase3b-{loop,pipeline}-{routing,conversational,no_ask,goal_completion,state}.json`.
94 turns, both agents, the same afternoon. `calculation` has no cases of its
own and was skipped; every calculation case lives in `routing` or `state`.

| category | loop | pipeline | tokens/turn, loop vs pipeline |
|---|---|---|---|
| routing | **3/7** | 7/7 | 25.1k vs 11.7k |
| conversational | 2/2 | 2/2 | 18.9k vs 11.7k |
| no_ask | 1/1 | 1/1 | 19.5k vs 12.4k |
| goal_completion | 1/3 | 2/3 | 19.3k vs 14.3k |
| state | **3/7** | 6/7 | 19.4k vs 12.5k |

**Decision: do not merge.** The plan's criterion was that routing and
conversational reach the pipeline's rates. Conversational does; routing does
not, and **R03 is CRITICAL and fails**. The pipeline stays the default.

### What 3b bought

Real, and visible in the cases it was aimed at. **L08 passes on the loop** —
`check_coverage` resolves "a Chicago police officer" to Article 5 on Section
5-111, `compute_benefit` works $37,601.57 from Section 5-132(e), and the
member's own salary and service stand in because they asked for them. The
pipeline fails the same case, reaching Article 3 and then starving every input.
**L09 passes on the loop** and is a known gap on the pipeline. Routing went
2/7 to 3/7, and the token gap closed from ~4x to ~1.7x.

### What it did not buy, and why

Every remaining loop failure is one of five gaps, and all five are the same
species as the evidence-completion defect: **the loop dropped a deterministic
step the pipeline has.** None is a reasoning failure.

- **A. A corrected or restated value never reaches the engine.** R03 turn 2
  ("actually it's more like 23 years"), G19 turn 2 ("my salary is actually
  $76,000"), G16 turn 4 ("change Scenario A's salary to $80,000"). Each reports
  *no calculation ran* with nothing missing and nothing blocked — the model
  simply had no way to hand the corrected value to the engine.
  `compute_benefit` has no `stated` parameter. The plan's own Task 1 interface
  specified one and it was not built; this is the single biggest lever, and it
  accounts for R03 (CRITICAL), G16, G19 and half of L04.
- **B. Assumptions are never disclosed.** `prepare_answer` renders an
  ASSUMPTIONS slot; `compose` renders nothing, so the model cannot say what it
  could not know it assumed. `ASSUMPTION_NOT_DISCLOSED` rejected the answer on
  R03, G19, L05 and G15 — the member saw the fallback.
- **C. A second `compute_benefit` overwrites the first.** R04 asked for
  retire-now, 58 and 60 side by side. State held the base and *one* variant, so
  the 58 figure the answer quoted could not be traced: `UNVERIFIED_FIGURE
  ['$36,567.41', '$3,047.28']`, answer replaced. Results must accumulate across
  calls, not replace.
- **D. `answer_rung` is never set.** `select_rung` runs inside `prepare_answer`,
  which the loop does not use, so every loop answer reports rung 0. R07's
  content is fine and discloses its assumption properly; only the instrument
  says otherwise.
- **E. An outline is still being answered from.** L06 described what Article 4
  provides — correctly, and correcting the member's premise, which the pipeline
  did not do — having read no Article 4 section at all (`articles: ['5']`,
  cited `5-111`). The `consulted_nothing` guard only fires when *nothing* was
  read; here Article 5 had been, so an Article described from headings passed
  underneath it.

### A note on instruments

`article_only` polices retrieval. On the pipeline that is the same question as
"what did the answer rest on", because the Article filter *is* the
determination. On the loop it is not: looking at the neighbouring fund is how
you rule it out. L08 moved to `answers_from_article`, which checks the
citations the verifier accepted and the provision the figure was worked from.

L06 was checked before assuming the same applied, and it does not — L06 is a
genuine failure, of a different kind than the key was written for. The rule
that keeps paying: read the report before reclassifying the case.


## Phase 3c: parity, 2026-09-13

`2026-09-13-phase3c-loop-*.json`, against the same day's pipeline run.

| category | 3b loop | **3c loop** | pipeline | tokens/turn |
|---|---|---|---|---|
| routing | 3/7 | **7/7** | 7/7 | 21.2k vs 11.7k |
| conversational | 2/2 | **2/2** | 2/2 | 25.6k vs 11.7k |
| no_ask | 1/1 | **1/1** | 1/1 | 9.4k vs 12.4k |
| goal_completion | 1/3 | **2/3** | 2/3 | 13.5k vs 14.3k |
| state | 3/7 | **6/7** | 6/7 | 11.4k vs 12.5k |
| **total** | 10/20 | **18/20** | 18/20 | |

Beside the tally: **L08 and L09 pass on the loop and are gaps on the
pipeline**, and hard guardrail failures run 1/47 against the pipeline's 2/47 --
the loop withdraws fewer answers, not more.

What still fails, on both: **L04**, a correction to a named scenario. **G17**
turn 1 is the loop's own, and real: "which provision applies to me, do not
calculate anything yet" cites 11-127 and 11-130 rather than 11-134, and turn 3
gets it right. A retrieval-selection miss, not a structural gap.

### The one lesson worth carrying out of 3c

Four of the five gaps 3b left were the same thing in four coats. **The pipeline
consumes conversational state structurally** -- `_needs_calculation` reads
`stale_figures` on an edge, `prepare_answer` renders the ASSUMPTIONS slot from
a template, and no model is involved in either. The loop made the model the
router and showed it none of that. A router that cannot see the state cannot
route: R03 spent its whole second turn asking who the member was, because
nothing in front of it said a figure was standing.

That predicts where the next ones are, and it is a short list: anything else
the pipeline reads on an edge or renders into a slot. `shape_for`,
`_comparison_rows` and the THRESHOLDS slot are all in that category and none of
them runs on the loop path.

### Two things called wrong, and how they were caught

- **"A corrected value cannot reach the engine"** was wrong. It reaches it;
  `resolve_inputs` reads it out of the conversation and recomputes. Only the
  bookkeeping that supersedes the earlier figure was missing. Caught by running
  R03 alone with `tool_calls_spent` recorded, rather than by reasoning about
  the suite report.
- **"G16 and L01 are infrastructure"** was wrong, twice over, across two runs.
  Dumping the request showed a 400: an assistant turn that only called tools
  carried no `content` key, which the tool-offering call accepts and the
  answer-writing call rejects. It cost two cases their entire first turn and a
  third its follow-ups. A failure seen twice is not thereby transient.


## Phase 4: the blocks were not redundant, 2026-09-13

`2026-09-13-phase4*-{loop,pipeline}-coverage.json`, four gate runs.

| | before | after |
|---|---|---|
| loop | **3/8** | **8/8** |
| pipeline | 8/8 | 8/8 |

The spec's plan for this phase was to delete six CONTEXT blocks from
`prepare_answer`, four of them outright, on the reasoning that
`_verify_coverage` and `_CONDITIONAL_MARKER` already enforce what they say.

**The measurement says otherwise, and the loop is the experiment.** `compose`
never had any of the six. It scored 3/8 on the coverage suite, both CRITICALs
among the losses, and reaching 8/8 meant rebuilding four of them:

| Block | Spec verdict | What actually happened |
|---|---|---|
| `frame` | delete, guardrail covers it | The guardrail **catches** an unconditional answer under a premise and the member sees a fallback. `compose` had to say it. |
| `described_as` + `BASIS_RECORD` | its safety half is covered | Not covered on either agent. Nothing stopped a figure while the Article was open; the pipeline passed G31 on an intent classifier's good mood. |
| `budget_spent` | the situation stops existing | It does not. `check_coverage` honours the same budget and says the same thing. |
| `BASIS_STATUTORY` + `described_as` | pure permission | The only one that held: the loop passes G35 with nothing said. |

The distinction the spec drew turns out to be the right one, and the mistake
was in the tally rather than the principle. **A fact the model is given, never
a stance it is handed** — every rebuild is a fact derived from state
(`clarification.options`, `hypothetical_frame.premise`, `assumed_facts()`,
`stale_figures`), rendered by code that reads the same keys the guardrail
reads. None is a stance written in advance, and that is why they cost nothing
on the turns where the state is empty.

### On deleting them from the pipeline

Not done, deliberately. The design question was "is the guardrail enough on its
own", and the loop has answered it: no, four times. Deleting the blocks from
`prepare_answer` would test the same claim a second time on the agent that is
still the default and is scheduled for retirement at cutover, which is risk
without information. The whole prompt goes in Phase 6.

### A second way an eval case goes vacuous

The README already records that a case written from a diagnosis asserts the
diagnosis. **S4 is the other way round: a case whose subject gets fixed keeps
asserting it.** It asked what Article 8 provides, named "Safety — uningested
article", and Article 8 has been in the index for some time — so the gap it
named had stopped existing, and both agents passed on the `admits_gap` check
being satisfied by the string "article 8", which an answer about Article 8
always contains. Repointed at Article 22A, which cannot be ingested without
changing the header pattern and the `SmallInteger` primary key together.

### Named risk, first sighting

Spec §5: *"a model can be smart and lazy: skip the computation and write
something fluent and thin."* G35 did exactly that once in six runs — coverage
settled, record usable, and an answer describing the 2.40% accrual in correct
prose with no figure in it. The other five runs called `compute_benefit`. Not
acted on: one occurrence is not a mechanism, and the pipeline cannot have this
failure because `_needs_calculation` is an edge rather than a decision. Worth
watching in Phase 6's full-suite runs.


## fund_smoke, 2026-09-14: the loop passes the pipeline

`2026-09-14-{loop,pipeline}-fund_smoke.json`, both agents, same code, same day.

| | before | after |
|---|---|---|
| pipeline | 13/18 | **15/18** |
| loop | 10/18 | **17/18** |

Three fixes, and only one of them was a bug in the ordinary sense.

**F10 was never broken.** Section 10-103 gives Forest Preserve District
employees "the same annuities, refunds and benefits ... as are made and allowed
for employees of the county" — employees of the county being Article 9. So an
Article 10 figure computed under §9-134 is right, and both agents had been
marked down for getting it right for as long as Article 10 has been ingested.
`fund_figure`'s rule is true of every fund but this one. The exception is
declared in `funds.py` with the section that establishes it and never inferred
from cross-references: an Article whose text merely *mentions* another is not
incorporating it, and guessing would re-admit the failure the suite exists to
catch.

**F15 was a null in a field that was never required.** The derivation emitted
`{"section": "15-136", "subsection": None}` for a provision with no lettered
subdivision — more honest than omitting the key or inventing "(a)" — and
`dict[str, str]` threw away the whole recipe over it.

**F03, F04 and F14 were sound reasoning on facts that were wrong for the
question.** Every fact `build_member_facts` publishes describes the member at
`as_of`; `employment_status` alone passed the record's value through. So a
derivation read §3-111(a), "no longer in service", against an active record and
correctly concluded the provision does not reach them — on a question that
begins "if I retired at the end of this year". Article 11's recipe never tests
status, which is why it stayed invisible for so long, and Article 19's
documented defect is the same shape with age.

A retirement annuity posits withdrawal, so at the date the figure is for, a
member the record calls active has stopped. The record's own value keeps a
name, because §11-128 ("employees who attain age 65 in service") has to be able
to test it, and the two places that report what is *on file* read that one.

### What the run cost to get right

Making the status a framing default meant the guardrail demanded its own
disclosure, and F04 was thrown away on **both** agents for
ASSUMPTION_NOT_DISCLOSED — an answer that had disclosed "the date you stop
working, taken as today" and then said nothing about the status derived from
it. It is not a second guess, and `render_assumptions` exists to stop one
choice being reported as two. Caught by verifying rather than by reasoning,
which is the only method that has worked all week.

### What is still open

- **F15**, now failing on both, with "no figure and no reason" rather than the
  schema error. A different cause from the one just fixed.
- **F05 and F12 on the pipeline**: an undisclosed assumption, and a generation
  call that failed outright.


## The cutover run: 2026-09-14, both agents, the whole suite

`2026-09-14-full-{loop,pipeline}.json`. 99 cases, 156 turns each, same code,
same day, the comparison Phase 6 exists to make.

```
loop      81/92 scored   9,162,543 tokens   TTFT median 20.08s   0 hard guardrail failures
pipeline  85/92 scored   3,582,506 tokens   TTFT median 12.20s   0 hard guardrail failures
```

| category | loop | pipeline | | category | loop | pipeline |
|---|---|---|---|---|---|---|
| adversarial | 6/6 | 6/6 | | fund_smoke | 18/18 | 18/18 |
| applicability | **2/3** | 3/3 | | goal_completion | 2/3 | 2/3 |
| article_routing | **0/1** | 1/1 | | hypothetical | **1/4** | 4/4 |
| calculation | **4/5** | 5/5 | | no_ask | 1/1 | 1/1 |
| capabilities | **2/3** | 3/3 | | other_benefits | 3/3 | 3/3 |
| conversational | 2/2 | 2/2 | | reasoning | 5/7 | 5/7 |
| coverage | 8/8 | 8/8 | | retrieval | 2/2 | 2/2 |
| cross_reference | 1/1 | 1/1 | | routing | **7/7** | 5/7 |
| end_to_end | **2/3** | 3/3 | | scope | 7/7 | 7/7 |
| error_handling | 1/1 | 1/1 | | state | **7/7** | 5/7 |

**Decision on the run as it stands: do not cut over.** The loop fails six
CRITICAL cases to the pipeline's one, and criterion 2 fails outright — the
`hypothetical` category regresses by three. The loop also wins four
`expected_fail` cases the pipeline still cannot do (G24, G40, L08, L09) and
takes `routing` and `state` off it, which is why the answer was to fix rather
than to abandon.

### What the six CRITICAL failures turned out to be

Two behaviours and one instrument, and the instrument accounted for more cases
than either behaviour.

**The loop could not leave a supposition (G41).** `check_coverage` creates a
`hypothetical_frame` and nothing anywhere cleared one. "Now go back to my
actual record. What is my pension really?" was answered as a park employee
again — 12-133 resolved against an Article 11 member, every record-derived
input reported missing, because the frame it never left says those facts belong
to another fund. `assess_coverage` releases the frame on an edge before
retrieval using `coverage.leaves_hypothetical`; the loop has no such edge, so
the release joined the two deterministic steps `investigate` already runs before
any model sees the turn.

**The loop borrowed the record it was told not to (G38, G39).** Asked "if I
were a park employee, how would my pension be calculated?" it set
`compute_benefit`'s `stand_in_facts` unprompted and quoted $32,126.78 — the
member's own Article 11 salary and service, run through Article 12's formula,
under a premise they had only supposed. `gold_hypothetical.yaml` had said in as
many words what should have happened: "everything becomes a missing input".

The schema paragraph says to set the flag *only* when the member offered their
own figures. That is a warning, not a mechanism — the same lesson
`find_provision` was withdrawn for on the same day. So the flag is corroborated
the way `scenarios.corroborated` corroborates a carried-forward scenario: the
model still decides what the member meant, and a regex checks the words are
there, in this message or in the premise they set up. **A regex may accuse,
supply data, or check provenance; that is now the third one that checks
provenance, and none of the three decides.**

**`article_only` was measuring the pipeline's method, not the loop's answer
(16, 4, 5, G01, G02, G30).** The key polices *retrieval*. On the pipeline that
is the same question as what the answer rests on, because the Article filter
*is* the determination. On an agent that decides what to read as it reads it is
not: looking at the neighbouring fund is how you rule it out. This had already
been settled once for L08 and once for L06; the full run showed five more, so
`article_only` went with the pipeline rather than being kept as an option
nothing uses.

Moved case by case against the recorded run, never in bulk, and the check still
bites: **G02 and G30 still fail under the sharper key**, which is the evidence
the move was not a fudge.

### A third way an eval case goes vacuous

The README already records two — a case written from a diagnosis asserts the
diagnosis (L01, L03, L08), and a case whose subject gets fixed keeps asserting
it (S4). **G02 is the third: a case whose expectation forbids what its question
asks for.** It asks the agent to "name any similar-looking provisions in other
Articles that must NOT be applied to my case", and then asserted
`article_only: "11"`. The comment sitting directly above that key said "naming
Article 12 to warn the member off it is legitimate" — the case had disagreed
with itself in writing for as long as it had existed. The loop named 8-138,
9-101 and 9-134, exactly what was asked, and was marked down for it; the
pipeline, which cannot leave Article 11, named none and failed the other half.

### The one genuine content defect, and where it came from

G30's first turn names Article 11 correctly on Section 11-101 and then cites
Section 7-109 to say it cannot establish whether that provision confirms
anything. F02 cites Section 4-109 to an Article 2 member the same way, and
passes only because `fund_smoke`'s key does not look.

Both turns spent `{outline: 1, get_section: 1}` — no search — so the fallback
search added the day before ran, and **it ran unfiltered**. `retrieve_policy` is
article-filtered and that filter is the determination; the fallback was added
because running a search is the one structural thing the loop can skip, and the
filter was the half that got missed. A slice of the whole Code landed in the
citable set and the model spent it on hedges. **A gap reported with another
fund's section number on it is still another fund's law in an answer about this
member.**
