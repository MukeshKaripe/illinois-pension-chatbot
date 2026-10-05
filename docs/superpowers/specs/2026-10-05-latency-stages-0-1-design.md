# Latency, Stages 0–1: measure honestly, then remove what the hot path doesn't need

**Status:** draft for review, 2026-10-05.
**Scope:** the measurement harness (Stage 0) and the low-risk latency work (Stage 1). Stages 2–3 are summarised at the end so the order is clear; each gets its own spec.

## 1. Goal and targets

Time to first answer word, as D3a defines it (progress lines don't stop the clock), **measured in-region** (estimated from this laptop until a deployment exists):

| Turn type | Target p50 | Target p95 |
|---|---|---|
| Most turns: statute questions, follow-ups, single-person figures | **< 3 s** | < 4.5 s |
| Heavy turns: a formula derived on the spot, several people | **< 7 s**, with honest progress lines | < 9 s |

**The constraint (D3) still holds:** no degradation of any kind. Capping the investigation or dropping evidence is not a latency lever. Every change here is gated on the quality checks in §3.3 as well as on time.

**Fixed for now:** the model is `gpt-6-luna`, with no faster deployment. The token limit is 1M per minute, and semantic ranker billing is on (both as of 2026-10-05).

## 2. What was measured (2026-10-05, laptop, about 250 ms from Azure)

### 2.1 The deployment

Medians of 3–5 runs, one call at a time:

| Measurement | Laptop | Meaning |
|---|---|---|
| First token, 31-token prompt | 1.72 s | **About 1.4 s server-side per model call**, even for an empty prompt (reasoning effort is already `none`) |
| First token, 3k / 10k / 19k-token prompt | 1.89 / 2.17 / 2.36 s | Prompt size costs about **0.04 s per 1,000 tokens** |
| 10k prompt, fully cached | 2.18–2.26 s vs 2.21 s uncached | **Caching doesn't move first-token time** on this deployment |
| 10k prompt with tools, one tool call returned (non-streamed) | 1.59 s | Choosing a tool costs the same as answering |
| Embedding one query | 0.50 s | ~0.1–0.3 s in-region |
| Hybrid search / with semantic ranker | 0.34 / 0.42 s | **The ranker adds ~0.08 s.** It now works; before billing it failed and retried on every query |

### 2.2 Turns (one run each; in-region estimate = laptop time minus each call's network share)

| Turn type | Laptop | Model calls in sequence (s) | Answer prompt | Est. in-region |
|---|---|---|---|---|
| Statute, names a section | 14.4 s | investigate 2.6 → answer 1.6 *(plus a second search round)* | 28k | ~4.5 s |
| Statute, open | 6.7 s | 1.8 → 1.3 | 14k | ~3 s |
| Described employee, figure | 8.3 s | 1.6 → **extract 1.6** → 1.2 | 10k | ~4 s |
| Missing info → asks | 9.8 s | 2.0 → **extract 1.2** → 1.1 | 11k | ~4 s |
| Named employee (PBMS), figure | 10.2 s | 2.1 → 2.1 | 11k | ~4–5 s |
| Named what-if follow-up | 8.5 s | 2.0 → **extract 1.4** → 1.1 | 11k | ~4 s |
| *(earlier run)* Missing info, with derivation | 24.1 s | 2.4 → coverage 2.0 → **derive 10.2** → 1.0 | 10k | ~16 s |

**Conclusions:**
1. At about 1.4 s per call, **the number of model calls in sequence decides the latency.** Two calls is ~3 s, three is ~4.5 s, and derivation is ~10 s on its own.
2. The **input-extraction call** (`resolve_inputs`, 1.2–1.6 s) runs on four of six turn types. It runs whenever the message has a digit, even when the digit already arrived as a tool argument ("65" → `retirement_age=65`).
3. A **section the question names** is extracted at triage but not prefetched, so the turn spends a tool round finding it.
4. Prompts reach **18–28k tokens.** At 0.04 s per 1k that costs less than a call, but they hit the token limit sooner and may repeat section text. Not yet measured.
5. Shrinking the network (in-region) helps every turn, but it can't take a three-call turn under 3 s.

## 3. Stage 0: measurement

### 3.1 A latency journey set

`evals/cases/L1/*.yaml`: a fixed set of 12–16 single and multi-turn cases, **at least two per turn type** in §2.2, plus the heavy types (a two-person comparison; a not-yet-eligible member, who triggers derivation today). The questions come from `demo-script.md` and from this week's failures. Each case uses the existing checks (figure, citations, must-state, asks), so a latency gain that breaks an answer fails the case.

### 3.2 Report per turn type, not one median

Extend `evals/timing.py` and `evals/report.py`:
- **p50 and p95** of measured and estimated time to first word, **per turn type** (the case declares it).
- **Critical-path breakdown** per turn: model calls (count, and each one's time), retrieval, Postgres, PBMS, and gaps, using the existing `stage_breakdown` and `_union`.
- **Prompt tokens per model call,** and a **repeated-text measure:** how many tokens of the answer prompt are section text already present earlier in the same prompt.
- **Add PBMS to `probe`** (one TCP round trip to the PBMS SQL Server), so a named turn's estimate subtracts its round trips too.
- **Repetition:** `--repeat N` runs each case N times, so p95 means something. Default 3 for L1, run one at a time.

### 3.3 The gate

A Stage 1+ change is accepted when, on the L1 set against the baseline run:
- **estimated p50 and p95 improve** (or hold) for every turn type;
- **no case that passed now fails;**
- **verification pass rate and the judge's goal/relevance scores don't drop;**
- and S4, S4b, S11 and S13 pass at their baseline rates.

The baseline is recorded once (`--accept` on L1) before any Stage 1 change.

### 3.4 Calibrating "in-region" (optional, when access allows)

Azure Monitor's `AzureOpenAITimeToResponse` metric gives the model's first-token time server-side. A small script (`scripts/model_ttft_metrics.py`, reading Azure Monitor for the run's time window) compares it with the estimator's per-call model figure. If they differ by more than 0.2 s, the estimator's per-call overhead is corrected. If metric access isn't available, the estimate stays labelled "estimated", as today.

## 4. Stage 1: remove what the hot path doesn't need

Each item is its own commit, measured against the Stage 0 baseline, and revertible on its own.

### 4.1 Input extraction: code first, model only when needed (est. −1.2 to −1.6 s on calculation turns)

**Today:** `resolve_inputs` calls the model whenever the latest message contains a digit, or an input is outstanding (`nodes.py`, around line 1414).

**Change:** before the call, the code readers run on the latest message:
- age: `_stated_age`
- salary: `_stated_annual_salary`, including "$80k"
- year joined: `_JOINED_RE`
- years of service: a new `_stated_service`, e.g. "10 years of service", "has 25 years"
- dates already parsed

The model call is skipped when **both** of these hold:
1. no declared input is still outstanding after the record, the ledger and the readers; and
2. **every number in the latest message is accounted for:** read by a reader, equal to a tool argument (`retirement_age`, `years_from_now`), or part of a section reference or a year.

Otherwise the call runs exactly as today. It's the only way to hear a correction to the record phrased unusually.

**Why that's no degradation:** the call is skipped only when it has nothing left to hear. Corrections ("actually I have 23 years") are numbers the readers catch, so they still land. Unrecognised numbers still go to the model.

**Tests:**
- "And if they worked until 65?" makes **no extraction call;**
- "Actually it's 23 years of service" still overrides the record;
- a number no reader recognises still triggers the call;
- the S4, S4b and S11 continuity cases are unchanged.

### 4.2 Prefetch the section a question names (est. −1.5 to −2.5 s on those turns)

**Today:** `triage` extracts `explicit_sections` ("Section 8-138(g-1)"), but nothing reads them before the model; the model has to search or call `get_section`.

**Change:** the pre-search that always runs also fetches the named sections (`retrieve(..., must_include_sections=explicit_sections)`), concurrently with the search. The model starts with the text in front of it, so a turn that only needed that text answers after one decision call.

**Tests:** a named-section question reaches its answer with no `get_section` or second search in the trace. The section is in `rendered` evidence.

### 4.3 No coverage check on a single-Article deployment (est. −2 s on the turns that hit it)

**Today:** `check_coverage` (one model call, ~2 s) is offered on every turn. Under D4 (MEABF only: everyone is Article 8, the coverage layer deferred) it can only confirm Article 8.

**Change:** when `deployment_article` is set, `COVERAGE_TOOLS` aren't offered. `coverage` resolves to the deployment Article, as `_record_coverage` already does. The tool returns when a deployment serves several Articles.

**Tests:** with `deployment_article=8`, no `check_coverage` is offered or called. With it unset, behaviour is unchanged (existing coverage tests).

### 4.4 Remove repeated section text from prompts (gain set by the §3.2 measurement)

**Only if §3.2 shows repetition.** When the same section's text is already in the conversation (from the pre-search, `get_section` or completion fetches), later additions refer to it ("Section 8-138 is above") rather than repeating it. **No evidence is removed;** each section appears once.

**Tests:** the answer prompt contains each section's text exactly once. The citation and verification checks on L1 are unchanged.

### 4.5 Embedding and search start together (est. −0.1 to −0.3 s in-region)

**Today:** `retrieve` embeds the queries, then searches.

**Change:** use Azure AI Search's **integrated vectorizer** (an Azure OpenAI vectorizer on the index, with `VectorizableTextQuery` at query time), so the service embeds the query itself. That's one round trip instead of two, and no stored vectors change. If the vectorizer can't be added to the index, fall back to starting the keyword leg while the embedding is in flight.

**Tests:** results for 20 L1 queries match the current top sections (same set, rank within ±2). Retrieval time drops in the breakdown.

### 4.6 Not in Stage 1

- **Prompt caching** for latency: measured no gain (§2.1). Kept for cost only.
- **Trimming retrieved evidence:** ruled out by D3. §4.4 removes repetition, not evidence.

## 5. Expected result after Stages 0–1 (estimated in-region)

| Turn type | Now | After Stage 1 |
|---|---|---|
| Statute, names a section | ~4.5 s | **~2.5–3 s** (4.2, 4.4) |
| Statute, open | ~3 s | ~2.8 s |
| Described / named figure, what-if | ~4–5 s | **~3–3.5 s** (4.1) |
| Turns that hit coverage or derivation | ~16 s | ~14 s (4.3). **Derivation is Stage 2.** |

Stage 1 alone doesn't bring calculation turns under 3 s: they still make two calls. That's Stage 3.

## 6. Later stages (separate specs)

- **Stage 2, deterministic helpfulness** (also takes derivation off the hot path):
  - compute at the earliest eligible age instead of deriving;
  - recompute when the user answers what was asked;
  - keep §1-162 out unless asked;
  - pin overriding provisions (§1-160 over §8-138);
  - curated formulas for survivor, disability and refund.
  - Target: heavy turns under 7 s, derivation rare.
- **Stage 3, structural:**
  - **A.** Answer in the same call when no tool is needed: each call either streams the answer or calls a tool, with the fact blocks rendered before every call. Saves one call on most statute turns, to ~1.6–2.3 s.
  - **B.** *(Needs a decision; see §7.)* Compute before the model call on clear calculation turns, so they answer in one call (~2 s).

## 7. Open decisions

1. **Option B:** may the engine compute *before* the model call on clear calculation turns (a known person or stated inputs, and an amount or what-if question)? The engine stays the only source of figures and the model still presents them, but it changes the stated rule that the model chooses when to compute. Needed before Stage 3, not before this spec.
2. **Integrated vectorizer (4.5):** adding it to `pension-code-prod` is an index change. It needs the Search service to be allowed to call the embedding deployment (key or managed identity).
3. **Azure Monitor access (3.4):** read access to the OpenAI resource's metrics, for calibration.
