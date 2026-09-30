# Eval Suite — Design

**Date:** 2026-09-30
**Status:** Design agreed in brainstorming on 2026-09-30, awaiting review of this document.
**Scope it serves:** `2026-09-29-employer-first-scope.md`: the employer is the user, the focus is MEABF (Article 8) only, the Article boundary is hard, chat is single-session, and the latency goal is D3/D3a.
**Replaces:** `scripts/eval_agent.py` and `evals/*.yaml`. The old suite was built for member-first use: personas, coverage across 24 Articles, and funds 2–19. It is retired rather than ported.

## 1. Purpose

The suite measures **accuracy and quality of answers across every scenario the assistant is meant to handle**, in multi-turn conversations, together with **time until the first word of the answer appears on screen**. It is run after every fix or addition, scoped to what that change touched. It is also the gate for every later phase of the scope, including latency work: under D3, a change that makes answers faster but worse fails.

What prompted this is a case the old suite could not diagnose. On 2026-09-30, an MEABF Tier 2 question got a hedge citing §1-162, three runs out of three. The cause was retrieval: the three printed versions of §1-160 were interleaved, pushing (c) out of the budget. That was fixed in `89dc540`. A second cause is still open: the loop gets one tool round after the pre-search, and the model spent it on `outline`. The new suite has to point at failures like these, not just count them.

## 2. Decisions taken in brainstorming

| # | Decision |
|---|---|
| E1 | **Where records come from: fixtures now, a live tier later.** For now, MEABF-shaped fixture records are held in memory; nothing is written to any database. Once Phase 1 exists, the same cases also run against real PBMS records through the production read layer. That live tier is reported but not scored. |
| E2 | **Scenario families S1–S12, plus S4b** (§4). First-class: S1 (statute questions), S4/S4b (calculations for a named or described employee) and S11 (continuity). Next in priority: S6 (recommendations). |
| E3 | **Missing-input policy** for a described employee (§5). Inputs that change *which rule applies* are asked for, or the answer covers every branch. At most 2 secondary inputs may be assumed, and every assumption must be stated. |
| E4 | **Grading: code checks can veto, and a judge decides whether the user's goal was met, against a reference** (§7). A turn passes only if every code check passes **and** the judge finds the goal accomplished, the answer relevant and the answer within the boundary. Soft qualities (voice, hedging, clarity) are scored beside the result and never decide it. |
| E5 | **Ragas is not used as the harness.** Its agent metrics (goal accuracy, topic adherence) are applied in one judge call per turn rather than several. Ragas re-judges faithfulness, which our deterministic verification already enforces. Recorded traces can be exported to it later if the client wants those numbers. |
| E6 | **Latency is measured locally for now, broken down by stage, with an estimated in-region figure alongside** (§8). A run inside the client's region replaces the estimate once the cluster exists. Until the estimate is calibrated against such a run, the latency gate is *provisional*. |
| E7 | **A run can be limited to the areas a change touched** (`--changed`, §9). The critical cases always run. A full run happens before any merge. |
| E8 | **The backbone is medium-length conversations (3–5 turns), each centred on one family, plus about 8 long cross-family journeys (S12).** After the first failed turn in a conversation, the remaining turns are marked *after an earlier failure* and are not scored. |

## 3. Architecture

```
evals/
  cases/<family>/*.yaml          one conversation per file
  fixtures/employees/*.yaml      MEABF-shaped EmployeeRecord fixtures
  rubric.yaml                    judge rubric: goal / relevance / boundary + soft dimensions
  labels/*.yaml                  hand labels for judge calibration
  baseline.json                  the last accepted run (committed)
  runs/<timestamp>/              traces and reports (git-ignored)
src/pension_chat/evals/
  cases.py      load and validate cases; the check vocabulary; `needs:` and `areas:`
  records.py    the RecordProvider protocol, EmployeeRecord, FixtureProvider
  capabilities.py  which `needs:` tags exist today
  runner.py     concurrent conversations, turns in order, TurnTrace capture
  checks.py     code checks over a TurnTrace
  judge.py      one reference-guided judge call per turn, with a cache
  timing.py     network probe, per-call timing, estimated in-region TTFT
  select.py     `--changed`: git diff → areas → cases
  report.py     scoring, the critical block, diff against the baseline, JSON and markdown
scripts/run_evals.py
```

### 3.1 RecordProvider: the seam between the agent and employee data

```python
class RecordProvider(Protocol):
    async def find_employees(self, query: str, scope: EmployerScope) -> list[EmployeeMatch]: ...
    async def get_record(self, key: EmployeeKey) -> EmployeeRecord: ...
    async def get_salary_history(self, key: EmployeeKey) -> list[SalaryYear]: ...
```

- **`PbmsProvider` (production, built in Phase 1)** reads the client's SQL Server database `PbsMeabDev`. It uses the read-only login and fixed parameterised queries, and applies the MEABF rules in one place: the current-row rule, tier taken from the record, status mapping, and refusing to guess an unmapped code.
- **`FixtureProvider` (evals)** returns records from `evals/fixtures/employees/*.yaml`, held in memory.
- **The agent calls only the interface,** so the live chatbot and the eval suite run the same agent code.
- **This spec fixes the interface and the `EmployeeRecord` shape;** Phase 1 builds `PbmsProvider` against them.
- **Until Phase 1:** `fetch_employee_record` uses a provider when one is configured, and otherwise reads today's Postgres path unchanged.

`EmployeeRecord` holds what the scope's Phase 1 names:
- PBMS key;
- employer and department;
- birth date, hire date and termination date (each may be missing);
- status, and tier 0–3;
- service, with its source;
- reciprocal service;
- salary history by year.

Any field can be absent, with the reason recorded, because MEABF data has gaps (scope §3.3).

### 3.2 Changes to the agent (each is a seam; none changes behaviour when unused)

1. `fetch_employee_record` asks a `RecordProvider` when one is configured.
2. `run_agent_stream` accepts an injected checkpointer; evals use an in-memory one, so nothing reaches the client's Postgres.
3. Evidence accounting: the state records, for each section, which subsection ids and versions were actually rendered to the model. This is not the same as what was retrieved; §1-160 had been retrieved without its (c).
4. Timing hooks on the model, embedding, search and Postgres clients: each call's start, duration, and whether it fell on the path to the first answer token.
5. The fallback to search without the semantic ranker (`retrieval._search_one`) is counted in the turn's state, not only logged.
6. `capabilities.py` lists the `needs:` tags that exist. Adding a tag there is the act of shipping that capability to the evals.

## 4. Scenario families

| # | Family | Priority | Available today? |
|---|---|---|---|
| S1 | Statute questions with no employee named | **First-class** | yes |
| S2 | Employee record lookup | | fixtures; employer voice pending |
| S3 | Eligibility of an employee (record plus law) | | partly |
| S4 | Calculation for a named employee | **First-class** | yes (member voice until `employer_voice`) |
| S4b | Calculation for a described employee ("an employee aged 56 earning X…") | **First-class** | partly |
| S5 | Hypotheticals inside the boundary | | partly |
| S6 | Recommendations inside the boundary | **Next** | partly |
| S7 | Missing or unclear data → ask the user | | partly |
| S8 | Several employees in one chat | | pending `find_employee` |
| S9 | Same-name disambiguation | | pending `find_employee` |
| S10 | Boundary refusals (another fund's law, questions across employees (D1), off-topic) | | partly |
| S11 | Continuity (corrections, follow-ups, a held scenario) | **First-class** | partly |
| S12 | Journeys: 6–10 turns combining families | | partly |

**Starting inventory:** about 95 turns in family conversations, plus about 60 in journeys, so about 155 in a full run. At 8 conversations in parallel and about 20 s per turn locally, that is roughly 6 minutes of turns; the longest journey (10 turns, about 3.5 min) sets the minimum run time.

| Family | Conversations | Turns |
|---|---|---|
| S1 | 8 | ~20 |
| S4 | 6 | ~16 |
| S4b | 6 | ~14 |
| S6 | 3 | ~7 |
| S11 | 5 | ~16 |
| S2, S3, S5, S7, S10 | 1–2 each | ~12 |
| S8, S9 | 2 each | ~8 |
| S12 | 8 | ~60 |

The first S1 case is the §1-160 Tier 2 question, marked `critical`.

## 5. Missing-input policy (E3)

This applies to S4b, and to S4 wherever a record lacks a field.

- **Critical inputs** are those that change which rule applies, or whether the person is eligible at all:
  - when they first became a member (it decides Tier 1, 2 or 3);
  - age at retirement, as distinct from age today;
  - service near a threshold (such as 10 years).

  The assistant must never assume one silently. It must **ask for it (`ask_critical`)** or **answer each branch (`branch_on_critical`)**.
- **Secondary inputs** change the amount but not the rule: salary growth, exact months of service, a retirement date within the year. At most **2 per answer** may be assumed, and each must be stated.
- **Always a failure:**
  - an assumption the answer doesn't state;
  - asking again for something the user has already given;
  - more than one round of questions where a branched answer would have done;
  - a figure that did not come from the calculation engine.

## 6. Case format

```yaml
id: S1-003
family: S1
areas: [retrieval, citations]
critical: true
needs: []                          # capability tags; while any is missing, the case runs but is marked pending
employee: null                     # or a fixture key, e.g. tier2_standard
turns:
  - q: "Under Article 8, what is the minimum age and service to retire for a Tier 2 member, and which section says so?"
    goal: "The Tier 2 minimum retirement age and service for MEABF, and the governing section."
    reference: >
      Age 67 with at least 10 years of service (§1-160(c)); reduced annuity from 62 with 10 years.
      Members first joining on or after 2017-07-06: age 65 with 10 years (§1-160(c-5)).
    acceptable: []                 # for policy cases: ask_critical | branch_on_critical | refuse | ...
    checks:
      cites_any: ["1-160"]
      evidence_has: ["1-160(c)"]
      must_state: ["67", "10 years"]
      ttft_class: simple           # simple: 3 s, heavy: 7 s
```

**Check vocabulary** (closed; the loader rejects any unknown key):

| Kind | Checks |
|---|---|
| Citations and evidence | `cites_any`, `cites_all`, `cites_none_outside` (Articles), `evidence_has` (subsections rendered to the model) |
| Facts | `must_state`, `must_not_state` (normalised: numbers and currency parsed; case and whitespace folded) |
| Figures | `calculation`, `no_calculation`, `figures_from` (engine / record / user / statute), `worked_from_article` |
| Missing inputs | `critical_inputs`, `max_assumed`, `assumptions_disclosed`, `no_reask`, `max_questions` |
| Boundary | `refuses`, `answers_from_article` |
| Verification | `verification_passed`, `not_replaced` |
| Latency | `ttft_class` |
| Records | `about_employee` (the fixture key the answer concerns) |

Every check reads the **TurnTrace** (§7.1). It reads the answer text only for the fact checks (`must_state`, `must_not_state`) and where no structure exists.

**Fixtures:** about 12 archetypes:
- Tier 1 veteran;
- Tier 2 standard;
- Tier 3 (joined after 2017-07-06);
- no hire date;
- no stored service;
- 9.8 years of service;
- retiree;
- two same-name "J. Smith";
- reciprocal service;
- a status code that isn't mapped;
- a Tier 0 closed record.

Values are synthetic, patterned on what `PbsMeabDev` holds; no real row is copied.

## 7. Grading (E4)

### 7.1 TurnTrace

The trace records:
- the streamed and final answer, and any replacement event with its reason;
- events with timestamps;
- tool calls and their arguments;
- sections retrieved;
- **the subsections and versions rendered to the model** (§3.2 item 3);
- verified and unverified citations;
- where each figure came from (from `input_sources` and the calculation card);
- assumptions and questions asked;
- the verification report;
- token usage;
- per-stage and per-call timings.

Checks and the judge read **only the trace**. A saved trace can therefore be re-checked or re-judged without running the agent again.

### 7.2 Order

1. Code checks run. **Any failure fails the turn**, and the judge is not called.
2. The judge runs one call and returns strict JSON:
   - `goal`: yes / partly / no;
   - `relevant`: yes / no;
   - `within_boundary`: yes / no;
   - `voice`, `hedging`, `clarity`: 1–5 each;
   - a quoted span from the answer backing each verdict.
3. **The turn passes only if `goal = yes`, `relevant = yes` and `within_boundary = yes`.** "Partly" counts as a fail, with the judge's reason shown.
4. For a request outside the boundary, "goal accomplished" means declining plainly and offering what can be answered.

**What the judge sees:**
- the conversation so far;
- this turn's answer, `goal` and `reference`;
- the evidence the model had;
- `rubric.yaml`.

**How the judge runs:**
- It runs on `gpt-6-luna`, the only deployment available, at low reasoning effort.
- Verdicts are cached by the hash of (answer, reference, rubric version).
- It moves to a second deployment if the client provides one; that reduces self-grading leniency.

### 7.3 Calibrating the judge

1. The product owner labels about 20 turns: goal yes/partly/no, relevance, and boundary.
2. `run_evals.py --calibrate` compares the judge's verdicts with those labels.
3. **The judge's verdicts count toward pass/fail only once it agrees with at least 18 of the 20.** Until then they are reported but do not gate.
4. Calibration is re-run whenever `rubric.yaml` changes.

## 8. Latency (E6, D3, D3a)

- **TTFT** is the time until the first token of *answer text* is released to the stream. Progress lines do not stop the clock.
- **Classes:**
  - `simple` (statute questions, record lookups): target **3 s**;
  - `heavy` (calculations, multi-step turns): target **7 s**.
- **Network probe** at the start of each run, per endpoint (model, embeddings, search, Postgres; PBMS later):
  - connection setup (TCP and TLS);
  - a warm round trip.
- **Per-turn attribution:**
  - every remote call is timed;
  - calls on the path to the first answer token are identified;
  - calls running in parallel count once.
- **Estimated in-region TTFT** = measured TTFT − Σ over those calls of (measured round trip − 2 ms assumed in-region), counting connection setup wherever it occurred.
- **The report shows both numbers.** The estimate is labelled *estimated* and the gate *provisional*.
- **Calibration:** the first in-region run calibrates the estimate once.
- **An open latency question the stage breakdown must answer:** about 10 s passed before the first search on 2026-09-30.
- **Rate-limit (429) waits** are excluded from TTFT and flagged.

## 9. Efficiency

1. **Concurrency:** conversations run in parallel, default 8, sized to `gpt-6-luna`'s tokens-per-minute quota, which is checked before the first full run. Turns within a conversation run in order.
2. **Tiers:**
   - **smoke:** about 15 conversations (every critical case plus one per first-class family); target **under 3 min**;
   - **scoped:** `--family`, `--id`, `--changed`;
   - **full:** target **under 12 min**.
3. **`--changed`:**
   - `select.py` maps code paths to areas, e.g. `agent/retrieval.py` → `retrieval`, and `calculation/**` → `calculation`;
   - the shared core (`agent/loop.py`, `agent/graph.py`, `agent/guardrails.py`, `agent/prompts*`) maps to **every** area;
   - critical cases always run.
4. **No wasted judge calls:** no judge call after a code-check failure, and judge verdicts are cached.
5. **Flakiness:** failed conversations are re-run once. Failed twice is a failure; failed then passed is reported as *flaky*.
6. **Baseline diff:** the report opens with what changed since `evals/baseline.json`: new failures, fixed cases, and latency moves per stage and class. `--accept` promotes a run to be the new baseline.
7. **No setup or cleanup:** records and checkpoints are in memory.

## 10. Scoring and report

- **Scoring:**
  - Pass rates per family, with the first-class families first.
  - In each conversation, the first real failure is scored; the turns after it are shown and marked.
  - Pending cases are listed with their results and are not scored.
- **Critical block:** a critical case failing fails the whole run, whatever the overall rate.
- **Also reported:**
  - the judge's soft scores (mean per family);
  - verification pass rate;
  - how many answers were replaced;
  - semantic-ranker fallbacks;
  - latency: median and 90th percentile per class, measured and estimated, per stage.
- **Output:**
  - `evals/runs/<ts>/report.json` and `traces/*.json`;
  - a markdown summary in the terminal;
  - the exit code is non-zero if the critical block fails, or the pass rate falls below baseline beyond what flakiness explains.

## 11. Failure handling

| Condition | Result |
|---|---|
| The agent raises, or sends an `error` event | The turn fails with the error type |
| Azure 429 or a transient network error | Back off and retry; reported, not counted, excluded from TTFT |
| The judge returns malformed JSON | One retry, then *unjudged*, which never counts as a pass |
| A case has an unknown check, a missing fixture or an unknown `needs:` tag | The loader refuses before any turn runs |
| The index or model is unreachable at the probe | The run stops before starting and says which one |

## 12. Testing the harness

These are unit tests with no network, run from recorded traces:
- the loader's schema checks;
- every check type, with a pass and a fail example each;
- scoring, including the after-failure marking and pending cases;
- selection by `--changed`;
- the in-region estimate on a synthetic call timeline;
- the report diff.

The trace of the 2026-09-30 §1-160 failure is kept as a fixture. Run against it, `evidence_has: ["1-160(c)"]` **must fail**; run against the fixed trace, it must pass.

## 13. Retiring the old suite

- `scripts/eval_agent.py`, the old `evals/*.yaml` and `evals/_captured/` are deleted in the same branch.
- The `Evaluation` section of `CLAUDE.md` is rewritten.
- The module docstring carries forward three ideas from the old suite: a case is a conversation, a critical case fails the run, and pending cases are reported without being scored.

## 14. Out of scope

- The live PBMS tier, which needs Phase 1 (`PbmsProvider`) and the read-only SQL login.
- The in-region runner, which needs the client's cluster, or a VM in their subscription.
- Checking our figures against PBMS's stored estimates (`T_Intf_Estimate`), which is scope Phase 6.
- The loop's one-round cap after the pre-search. It is a finding the suite should measure, not part of building the suite. It is fixed separately, gated by the suite.
