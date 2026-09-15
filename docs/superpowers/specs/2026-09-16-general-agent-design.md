# Design: a general assistant with registers

*2026-09-16*

## The principle

The assistant is a RAG agent over the Illinois Pension Code. Calculation is one
capability it has, not the shape of the thing. It was never calculation-first,
and the architecture has made it so.

This design restores the general assistant by giving each dimension of a query
its own mechanism, rather than deciding all of them with one setting of one
knob.

Lineage: `2026-09-12-intelligent-agent-design.md` §6 made *the corpus*
accumulate across sessions — verified retrieval memory, captured failures, a
registry trusted by its validator. This design makes *the conversation*
accumulate within a session. The two are orthogonal and both are needed.

## 1. Why

Six behaviours, reported from live use and each reproduced in the code:

| observed | mechanism |
|---|---|
| Values the member stated are forgotten within two turns | `DURABLE_KEYS` (`state.py:236`) lists nine keys that survive a turn. `input_overrides` is not among them, and `turn_input` resets every non-durable key. The member's own words are the highest-provenance fact class in the design and the only one discarded each turn. |
| A question about another fund's members retracts the whole answer | `_search_filter` (`loop.py:394`) resolves one Article: named by number, then a premise about the member, then the coverage verdict, then the record. A subject described in words — "a person who is a teacher" — moves none of them, so the safety-net search reads the member's own Article, the model answers from its own knowledge, and the citation check fails `CITED_NOT_RETRIEVED`. |
| "I'm going by your record…" never stops appearing | The line is prose in the answer prompt (`nodes.py:1491`) with a prose condition. Every other block in `compose` is rendered by code from the state key the guardrail reads. This one asks the model to police itself — the failure mode this codebase documents twice, in `stand_in_facts` and `find_provision`. |
| "Assume a standard number and calculate" is refused | `resolve_inputs` starves missing facts by design, there is no statutory-default facility, and ABSOLUTE RULE 2 of the answer prompt forbids stating any amount the engine did not work out. The refusal is correct given the rules. The rules are wrong. |
| "What are the things needed" returned investment-adviser rules under §1-113.5 | The safety-net retrieval query is the bare last message. Nothing rewrites it against the conversation. |
| The calculation card and citations appear 5–10s after the prose | Both ride on the `done` event, which fires after the full answer has streamed *and* post-generation verification has run. |

Separately, time-to-first-token measures **9.71s in-region**, against a target of
under 5s.

## 2. Diagnosis

These are one problem.

**The system has exactly one output standard — the binding determination — and
applies it to every sentence it utters.** Whether a number may be stated, which
Articles are searched, whether a gap is fatal, and whether a prior fact survives
all fall out of "can this be proven to binding grade".

Every guardrail is individually justified; each caught a real harm. Together
they encode an assumption that was never true: that the job is to *determine*
entitlements. So the agent can only say what it can prove to determination
grade, and explaining, illustrating, supposing and estimating all collapse into
*"I could not establish."*

The latency is downstream of the same cause. A turn asking what §11-134 says
pays for recipe resolution, input resolution, evidence completion and provision
naming — determination machinery, on an educational question.

### The query space

A query in this use case varies along five independent dimensions. Today one
mechanism decides all five, at its strictest setting.

| dimension | values |
|---|---|
| claim | the law says X / a quantity is N / something is missing / about this conversation / the Code is silent |
| subject | nobody's facts / this member's / a supposed version of them / another person's |
| scope | their Article / a named Article / an Article implied by a description / cross-cutting / unknown |
| completeness | complete / 1–2 gaps / mostly gaps / contradictory |
| dependence | self-contained / prior facts / prior answer / inside a frame |

The design gives each dimension its own mechanism:

| dimension | decided by |
|---|---|
| claim | the **register** |
| subject and scope | the **retrieval subject**, decoupled from coverage |
| completeness | the **ledger's provenance counts** — deterministic |
| dependence | ledger, `hypothetical_frame`, `open_question`, `scenario_set` |

Generalization comes from that orthogonality, not from covering more cases.

## 3. Foundations

### 3.1 Register — what a block claims

Declared by the model **per block, not per turn**, so a compound question does
not collapse to its strictest part. The default is `explain`.

| register | claim | standard |
|---|---|---|
| `explain` | what the law provides | every proposition traces to retrieved text; no member-specific figures |
| `requirements` | what is needed to go further | list computed from the recipe's declared inputs and the Article's eligibility section — provably complete, not a model's guess |
| `estimate` | this member's figure | engine figure; assumptions counted from ledger provenance |
| `illustrate` | a worked example for a generic person | engine figure on statutory defaults; record values unreachable |
| `suppose` | this member under a premise | engine under the premise; their own values only if invited |
| `recommend` | a consequence the Code creates | two or more engine-computed alternatives, a citable provision that makes the difference, and a code-rendered disclaimer |
| `reflect` | what was assumed, why something was said | cites conversation state, not statute |
| `boundary` | the Code is silent on this | requires evidence of absence — a retrieval that ran and came back below threshold |

The claim dimension is a closed set, which is why registers can be complete: an
answer either describes the law, gives a quantity, describes what is missing,
describes itself, prices a consequence, or reports that the corpus does not
reach the question.

**Every hedge in the reported transcripts is an answer that did not know which
shape it was.** "I could not establish whether…" is `requirements` or `boundary`
failing to be either. Giving each a first-class shape with a completeness
standard leaves the hedge nowhere to live.

The model declares the register. **Code corroborates wherever a register relaxes
a protection**, following `_invited_stand_ins`:

- `illustrate` cannot reach the record. That is G38 — an Article 11 salary
  history run through Article 12's formula — and it stays impossible.
- `suppose` requires a premise present in the member's own words.
- `estimate` for someone with no record and no stated facts downgrades to
  `illustrate`, because there is nothing of theirs to estimate from.

### 3.2 Ledger — what the conversation knows, and how it knows it

Durable, per subject, provenance-ranked.

This promotes structures that already exist and are discarded each turn.
`_input_origins` (`nodes.py:692`) already classifies every input as `assumed`,
`stated`, `derived` or `record`, computed by code at resolution time rather than
declared by the model — deliberately, because "the calculation card shows these
to the member, so a guess here is a false claim about their own figure". Two
things are wrong with it: `_stated_in` consults only the current message, and
`input_sources` is not durable.

```
me:
  total_service_years   21.5        record
  final_average_salary  72743.62    record
  retirement_age        60          member_stated (turn 4)
  withdrawal_date       2026-12-31  statutory_default §9-134
```

Precedence: **member_stated > record > derived > statutory_default**.
Supersession rather than overwrite, so a correction leaves an audit trail and
`reflect` can answer "what changed?".

### 3.3 Subject — who a block speaks about

Not always "the member". Each subject carries its own facts, provenance,
coverage and Article, and stays addressable by name for the session, so "go back
to the firefighter one" works in turn nine.

This names what three existing durable keys have in common:

| today | is really |
|---|---|
| `hypothetical_frame` | one derived subject |
| `scenario_set` | subjects differing in one fact |
| `baseline` | the subject "me, retiring today" |

It is not a new layer; it is the generalization that makes N-way comparison fall
out. Ledgers are starved across subjects exactly as `_available_facts` starves
record-derived facts under a supposition today, so the rule that a figure is
computable only when no input is borrowed from another Article's record holds
unchanged.

### 3.4 View — how a figure is shown

A fixed vocabulary of markers the model *names* and code *fills*, extending
`MarkerSubstituter`, which already discards anything the model writes in a
marker's place.

| marker | renders | fed by |
|---|---|---|
| `<<<COMPARISON>>>` | subjects × figures | the subject ledgers |
| `<<<SENSITIVITY>>>` | one variable swept | N engine runs |
| `<<<BREAKDOWN>>>` | the worked steps | the worksheet |
| `<<<TIMELINE>>>` | thresholds ahead, priced | `_cliff_ahead` |
| `<<<REQUIREMENTS>>>` | what is missing, per subject | ledger provenance |

**The model never writes numbers into a view** — the same rule as arithmetic,
for the same reason.

## 4. Retrieval follows the subject

`_ARTICLE_BY_NAME` deliberately refuses to match fund Articles by name, so that
"I am a park employee" cannot bypass the coverage determination. That same
refusal is why "a person who is a teacher" cannot move the retrieval subject.
**One variable decides both which law may be read and which fund the member may
be told they are in.**

Retrieval scope becomes the union of the Articles of all active subjects, plus
the cross-cutting ones. A subject acquires an Article from an explicit number,
from `check_coverage` assessing a description, or from the record.

Naming a fund in words now creates or updates a *subject*; it does not touch the
member's coverage. The protection moves from the filter to the register: in
`explain` and `illustrate` a block may not claim any Article as the member's, so
widening what may be read carries no risk of welding a self-description to a
fund.

`_search` (`tools.py:353`) already honours an `articles` argument from the model
and searches the whole Code when it is omitted, so only the safety-net path
changes.

**Conversational query rewrite.** The safety-net search uses the bare last
message, which is how "what are the things needed, please list them" retrieved
investment-adviser rules. It takes the recent turns and the active subject
instead.

## 5. Assumptions

### 5.1 The ladder

**member_stated → record → derived → statutory_default → never invented.**

Ranks 3 and 4 are where the current agent gives up. A retirement date derivable
from a stated age is not an assumption at all; an Article's own normal
retirement age is citable rather than guessed.

### 5.2 Value gaps and governing gaps

A **value gap** — salary, service, a date — changes the number: assume it, flag
it, proceed. A **governing gap** — tier, Article, employment status — changes
which provision applies: ask, or compute both branches and compare.

This extends an existing rule from conflicts to absences: *"a conflict in a
routing field is a question; a conflict in a value stays an
override-with-note"*.

The transcripts contain the proof. Asked to calculate with an unknown tier, the
agent correctly identified the branch — "If you are Tier 1… If you are Tier 2…"
— and then refused to compute either. The right behaviour is both branches,
computed, side by side: `illustrate` across two subjects, rendered as
`<<<COMPARISON>>>`.

### 5.3 The policy that constrains the register

The model declares what a block is trying to do; this decides what it is
*permitted* to be, and the narrower of the two wins. The model asking to give a
figure does not make one available — the ledger does. Counted from provenance,
so the model cannot dodge it:

| ledger state | register |
|---|---|
| no gaps | `estimate` |
| 1–2 value gaps fillable at rank 3 or 4 | `estimate`, assumptions named |
| more than 2 value gaps, or any fillable only by invention | `illustrate` **and** `requirements` |
| any governing gap | ask, or branch and compare |

`assumption_ceiling` (2) becomes the value-gap threshold. `materiality_threshold`
(0.02) already decides whether an assumption reaches the opening sentence rather
than only the assumptions block. A supplied value outranks a default, triggers a
recompute, and the answer says what moved.

### 5.4 Where this stops

The Pension Code states rates, thresholds and averaging periods. **It does not
contain typical salaries.** A salary can be derived from a member's history,
taken from their words, or used as a labelled illustration figure — but there is
no citable standard salary, and inventing one would be a number with no
provenance in a system whose whole discipline is provenance.

That is the corpus's edge, not the architecture's rigidity. The honest move is to
say so and show an illustration, which the current agent cannot do at all.

## 6. Guardrails, per register

Verification is one turn-level verdict today, and a hard failure replaces the
entire answer — which is how a sound explanation of teacher pensions became
"I have taken that answer back — sorry."

Per-block verdicts, with a downgrade ladder instead of retraction:

| check | applies to | on failure |
|---|---|---|
| citations are retrieved sections | all statute registers | drop the offending block |
| figures trace to engine output | `estimate` `illustrate` `suppose` `recommend` | **whole-answer retraction, as today** |
| coverage claim permitted | `estimate` and `explain` may attribute; `illustrate` never claims | downgrade `estimate` to `requirements` |
| stays conditional | `suppose` `illustrate` | re-render the block |
| composed inputs passed | `recommend` | drop the block, keep the figures |

**An untraceable figure remains the one failure that retracts everything**,
because it is the one that actually harms a member.

The record-attribution sentence is removed from the answer prompt and becomes a
code-rendered block that fires only when `coverage.basis == "record"` and the
member has not already confirmed — so it stops appearing once it has been
answered.

## 7. The answer pipeline

**One fused call.** The call that reads the statute becomes the call that writes
the answer. Streaming delivers tool calls as deltas, so the model can still fall
back to the loop when it needs more: if the first delta is a tool call, buffer
and iterate; if it is content, stream it through as the answer.

**Views stream ahead of prose.** Every view renders from state that exists
before generation starts, so each becomes its own SSE event the moment it is
ready rather than riding on `done` behind the full generation and verification.
The table arrives before the sentence that introduces it.

**Progress past four seconds.** A quiet indicator from zero; real steps after
four seconds, emitted with `get_stream_writer()` (langgraph 1.2.11) from inside
the loop — the actual query, the actual section heading, the actual derivation.
A status that flashes for 200ms is noise, so fast turns show none of it. The
steps are generated from the same state the answer is generated from: never a
second model call, which would add the latency it is explaining.

This also repairs a bug. `graph.py:256` emits stage labels from LangGraph's
`updates` stream, which fires when a node *finishes*, so the label shown during
the longest wait is the step that already completed — "loading your record"
while the loop is reading the Code.

## 8. Latency budget and the guard

| register | TTFT | basis |
|---|---|---|
| `reflect` | ~1.8s | projected |
| `boundary` | ~2.5s | projected |
| `explain`, `requirements` | ~3.2–3.5s | projected from a measured 2.9–3.2s single call |
| `estimate`, baseline precomputed | ~4.0s | projected |
| `estimate`, uncommon | ~6.0s | measured, 5.40–6.16s in-region |
| `illustrate`, `suppose` on a fresh premise | ~6.5s | measured shape |
| first-ever recipe derivation | ~30s | measured; documented exception |

**Median about 3.2–3.5s. Not every turn under 5s** — a fresh premise is
structurally two model calls, because it cannot be guessed before the model
reads it. What changes about those slow turns is that they buy a figure with its
assumptions named, instead of buying "I could not establish".

The `estimate` fast path depends on a **speculative baseline**: `baseline` is
already a durable key holding "the 'if you retire now' figure every calculation
files". Computing it concurrently with retrieval, before the model asks, is the
`estimate` equivalent of moving the search to the front.

**The guard.** A benchmark someone has to remember to run will not hold this.

1. A unit test with a fake LLM asserting the number of **model round trips
   before the first token**: at most one for `explain`, at most two for
   `estimate`. No network, runs in CI, fails the moment a step is added to the
   critical path. Round-trip count is what regresses silently; wall-clock is its
   shadow.
2. `bench_ttft.py --budget` exiting non-zero.

## 9. What does not change

The DSL interpreter, `formula_registry`'s lifecycle, `risk_assessor`,
`validator`, `custom_handlers`, row-level security, the checkpointer, and the
rule that the model never performs arithmetic. `compute_benefit` remains the
only path to a figure.

`execution_policy` (`no_execute` / `estimate` / `authoritative`) is already a
register for recipes — a declared trust level gating what an output may be used
for. It composes with answer registers, and the weaker of the two wins.

**One addition:** a **statutory defaults registry**, built on
`formula_registry`'s lifecycle — discovered from retrieved statute text,
persisted, reusable, citable. It is what makes "assume a standard number"
answerable with a citation instead of a guess.

## 10. How capabilities are added

A capability enters as three things, and the pattern is what makes the base
worth building on:

1. a **tool** the model may call,
2. a **fact class with provenance** it writes into the ledger,
3. a **register constraint** — what standard its output must meet.

| capability | fits? |
|---|---|
| survivor and disability benefits | yes — new `BENEFIT_TYPES` and recipes, zero architectural change |
| post-retirement increases (COLA) | yes — a projection tool writing to the ledger |
| upload a benefit statement | yes — a new provenance tier above `record` |
| Reciprocal Act service aggregation | yes — writes `combined_service` |
| "how do I apply" / forms | **no** — needs a second corpus |
| versioned statute ("what changed in 2023") | **no** — the index holds current text only |

The pattern holds for anything producing facts or figures. Capabilities needing
a different corpus are real extensions, not free.

## 11. Sequencing

Each step independently shippable and testable:

1. Ledger durable, provenance across turns
2. Registers and the counting policy
3. Retrieval subject and conversational query rewrite
4. Per-block guardrails; delete the attribution prose
5. Fused call and speculative baseline
6. Views, streaming ahead of prose
7. Subjects and comparison
8. `recommend`

Order matters. Steps 1 and 2 are the foundation — registers without the ledger
would permit an estimate that still has nothing to estimate from. Step 5's
latency gain is only safe after step 4, so that a faster path cannot retract
more.

## 12. Accepted risks

**Registers are model-declared flags, and this codebase's history is a catalogue
of those going wrong** — `stand_in_facts` set when the member had not asked,
`find_provision` cited without being read. The load-bearing mitigation is that
**assumptions are counted, never declared**: their number and identity come from
ledger provenance, so "what I assumed" cannot be wrong and the ceiling cannot be
evaded by a model that would rather not mention it.

**`recommend` is the register most likely to drift** from consequence into
advice. Its standard is structural rather than stylistic: it composes only over
engine output, and it cannot reach life expectancy, tax, investment return,
health or risk appetite, because none of those are in the corpus.

**Deliberately out of scope:** recommendation resting on anything outside the
Code. The agent shows the comparison; the member chooses.

**Not covered here:** a second corpus for administrative questions, and
versioned statute text. Both are named above as real extensions.

## 13. What the numbers rest on

Measured 2026-09-15 and 2026-09-16, ten turns per configuration, run as a
Container App Job inside Azure against the live models, index and database. The
probe code was thrown away; these are its output.

| configuration | TTFT p50 | round trips p50 | per loop call | answer first token | guardrail |
|---|---|---|---|---|---|
| current | 9.71s | 3.0 | 2.34s | 2.75s | 10/10 |
| search moved before the first model call | 7.59s | 2.0 | 3.09s | 2.92s | 10/10 |

Moving the search to the front collapsed six of ten turns to two model round
trips. It is necessary and not sufficient: the best two-round-trip turn was
6.01s, of which 5.96s was the two model calls themselves. **Two round trips is
the budget, and it overspends it** — which is why §7 fuses them.

Two negative results, recorded so they are not re-litigated:

- **`gpt-5-mini-1` is slower than `gpt-5.6-luna-1` here**, on the same network:
  1.29s against 1.01s on an identical tiny prompt. It is also less decisive,
  reaching eight loop calls on a question the larger model settled in two, and
  end-to-end it made things worse (27.19s against 20.93s). Routing the loop to a
  smaller model is not available. "Smaller" is not "faster" across a model
  generation.
- **Prompt caching is a cost lever, not a latency lever.** luna cached 2,629 of
  2,632 prefix tokens and the warm call was *slower* than the cold one (1.53s
  against 0.97s). `gpt-5-mini-1` cached nothing at all.

First-token latency against prompt size, streaming, same deployment: 1.72s at
688 tokens, 2.20s at 7.9k, 2.82s at 15.8k, 3.57s at 26.3k, 4.14s at 42k. The
curve is shallow, so trimming context is worth far less than removing a round
trip.

The checkpoint write (`SqlAlchemyCheckpointSaver.aput`) measured 0.97–1.93s
sitting between `compose` and the answer call. `durability="exit"` already made
it one write rather than seven; it can be fire-and-forget during the answer
stream, as `registry.note_reuse_later` already does for reuse counts.
