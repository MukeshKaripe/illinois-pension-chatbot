# Baselines

What the pipeline scored before the agent loop existed, so "the loop is better"
is a measurement rather than an impression.

Cutover (spec §7) compares the loop against the full-suite pipeline report.
**That report does not exist yet** — see "The full-suite run does not fit in
one quota window" below. The three category reports here are valid; the
full-suite one is not.

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


## The full-suite run does not fit in one quota window

`2026-09-13-pipeline-INCOMPLETE.json` is kept as evidence, not as a baseline.
The first 38 cases ran clean; **every case from index 38 onward failed**, with
no token usage recorded at all on most of them:

```
. . . . . X . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .
X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X X
```

A hard cutover partway through, not a scatter. `llm.py` already retries
`RateLimitError`, so the retries were exhausted too: the run spent 660,750
tokens across 152 turns and ran out of Azure quota. Nothing in the agent
regressed — the three category suites, run separately an hour earlier, were
healthy, and excluding the errors leaves only a handful of genuine case
failures.

**This matters beyond one bad run.** Cutover needs the *same* suite run twice,
once per mode, and neither fits. So the procedure has to change: run by
category with `--category`, write a report per category, and compare
category-to-category rather than whole-run to whole-run. Whoever executes
Phase 3 should plan for that rather than discover it.
