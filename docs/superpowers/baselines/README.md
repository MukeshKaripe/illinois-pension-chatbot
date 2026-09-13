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
