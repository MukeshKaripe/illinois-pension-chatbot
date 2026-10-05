# Latency and accuracy that hold as the product grows: measurement, and a call-budget foundation

**Status:** draft for review, revised 2026-10-05 (the first draft optimised the current turns; this one is built to scale).
**Scope in detail:** Stage 0 (measurement and the gate) and Stage 1 (the foundation). Stages 2–4 are outlined so the order and the direction are fixed; each gets its own spec.

## 1. Goal

Two things that have to **stay true as features are added**, not just reach a target once:

1. **Fast:** time to the first answer word (D3a: progress lines don't stop the clock), measured in-region, estimated from this laptop until a deployment exists:

   | Turn type | p50 | p95 |
   |---|---|---|
   | Most turns: statute questions, follow-ups, single-person figures | **< 3 s** | < 4.5 s |
   | Heavy turns: a formula derived on the spot, several people | **< 7 s**, with honest progress lines | < 9 s |

2. **Accurate and helpful:** answers rest on the right provisions, every figure comes from the engine, and the assistant asks for what it needs. **No degradation** (D3): capping the investigation or dropping evidence is never a latency lever.

**Fixed for now:** model `gpt-6-luna` (no faster deployment); token limit 1M per minute; semantic ranker billing on.

## 2. What was measured (2026-10-05, laptop, about 250 ms from Azure)

**The deployment.** Medians of 3–5 runs, one call at a time:
- **About 1.4 s server-side per model call,** even for a 31-token prompt (1.72 s from the laptop). Reasoning effort is already `none`.
- Prompt size costs about **0.04 s per 1,000 tokens** (3k: 1.89 s, 10k: 2.17 s, 19k: 2.36 s).
- Caching doesn't move first-token time (10k cached 2.18–2.26 s vs 2.21 s uncached).
- A call that returns a tool call costs the same as one that answers (1.59 s).
- Embedding: 0.50 s. Hybrid search: 0.34 s, or 0.42 s with the semantic ranker.

**Turns** (one run each; in-region estimate = laptop time minus each call's network share):

| Turn type | Laptop | Model calls in sequence (s) | Est. in-region |
|---|---|---|---|
| Statute, names a section | 14.4 s | investigate 2.6 → answer 1.6, plus a second search round (28k-token answer prompt) | ~4.5 s |
| Statute, open | 6.7 s | 1.8 → 1.3 | ~3 s |
| Described employee, figure | 8.3 s | 1.6 → **extract 1.6** → 1.2 | ~4 s |
| Missing info → asks | 9.8 s | 2.0 → **extract 1.2** → 1.1 | ~4 s |
| Named employee (PBMS), figure | 10.2 s | 2.1 → 2.1 | ~4–5 s |
| Named what-if follow-up | 8.5 s | 2.0 → **extract 1.4** → 1.1 | ~4 s |
| Missing info, with derivation (earlier run) | 24.1 s | 2.4 → coverage 2.0 → **derive 10.2** → 1.0 | ~16 s |

**The finding that shapes this spec.** Latency is set by **the number of model calls in sequence**: about 1.4 s each, so two calls is ~3 s, three is ~4.5 s, and derivation is ~10 s. Every extra call on today's hot path arrived with a feature: input extraction, the coverage check, derivation. **Left alone, each new feature adds another.** Per-feature tuning can't keep up with that, so the fix has to be a rule the codebase enforces.

## 3. Principles: what must hold for every feature, now and later

**P1. A model-call budget per turn type, enforced by tests.** The number of sequential model calls before the first answer word is fixed per turn type (§4.1). A change that exceeds it fails the fast test suite, before it reaches a live run.

**P2. A capability is placed in one of three ways, never as a new sequential call.**

| Placement | Use it for | Examples |
|---|---|---|
| **Deterministic code** | anything a rule decides | who a turn is about; coverage from the record or deployment; tier from the year joined; a not-yet-eligible check |
| **Arguments of a call that already happens** | anything the model has to *read* from the conversation | values the user stated (§5.2); which benefit; a what-if age |
| **Prepared ahead of time** (ingestion or curation) | knowledge that doesn't change per question | the concept-to-section map; overriding-provision tags; curated formulas |

A model call outside these needs an explicit budget exception, written down with its turn type, measured cost, and why it can't be placed otherwise. Derivation is today's only candidate, and Stage 3 takes it off the hot path.

**P3. Anything the model must not get wrong is checked in code** (the existing project rule). A value the model passes as "stated" is verified against the user's words; a figure comes only from the engine; a citation must have been read.

**P4. Prompt size grows with the question, not with the conversation or the evidence count.** Each section's text appears once per prompt; history is bounded; prompt tokens are reported per call.

**P5. Every capability ships with latency-suite cases** (call count, p50 and p95, correctness checks), so the gate grows with the product. The fast budget tests run on every change, the live suite before a merge.

## 4. Stage 0: measurement and the gate

### 4.1 Budgets

| Turn type | Sequential model calls before the first answer word |
|---|---|
| Statute question, no tool needed | **1** *(2 until Stage 2)* |
| Statute question needing more reading | 2 |
| Figure for a named or described person, or a what-if | 2 *(1 if Stage 4's option B is adopted)* |
| The assistant asks for missing details | 2 |
| Comparison of up to 4 people | 2 (one `compute_benefit` per person, issued as parallel tool calls in one decision) |
| Heavy, exception: a formula derived on the spot | 3, until Stage 3 makes it rare |

### 4.2 Fast budget tests (no live model)

The graph runs with a scripted fake model: it returns given tool calls or text, and records each call with its node and order. One test per turn type asserts:
- the number of **sequential** model calls before the first answer token is within budget;
- no model call is made by a node outside the allowed list for that turn type.

These run in `uv run pytest`, in under a few seconds. **This is what stops a feature from quietly adding 1.4 s.**

### 4.3 The live latency suite

`evals/cases/L1/*.yaml`: 12–16 single and multi-turn cases, **at least two per turn type**, drawn from `demo-script.md` and this week's failures. Each uses the existing checks (figure, citations, must-state, asks), plus a new check, **`model_calls_max`**, read from the trace.

Extend `evals/timing.py` and `evals/report.py` to report:
- **p50 and p95** of measured and estimated time to first word, **per turn type**;
- **the critical-path breakdown** per turn (model calls with each one's time, retrieval, Postgres, PBMS, gaps), from the existing `stage_breakdown` and `_union`;
- **prompt tokens per call, and repeated section text** (tokens of the answer prompt already present earlier in it), which tracks P4;
- **PBMS** in the round-trip `probe`;
- with `--repeat N`, each case run N times so p95 means something (default 3, one at a time).

### 4.4 The gate

A change is accepted when, against the recorded L1 baseline:
- **the fast budget tests pass;**
- estimated p50 and p95 improve or hold **for every turn type;**
- no case that passed now fails;
- the verification pass rate and the judge's goal/relevance scores don't drop;
- S4, S4b, S11 and S13 pass at their baseline rates.

The baseline is recorded once (`--accept`) before any Stage 1 change.

### 4.5 Calibrating "in-region" (when metric access allows)

Azure Monitor's `AzureOpenAITimeToResponse` metric gives the model's first-token time server-side. `scripts/model_ttft_metrics.py` compares it with the estimator's per-call model time for a run's window, and corrects the estimator's per-call overhead if they differ by more than 0.2 s. Without access, estimates stay labelled "estimated".

## 5. Stage 1: the foundation

Each item is its own commit, gated by §4.4 and revertible on its own.

### 5.1 The call-budget machinery

- **Per-turn record:** a call ledger (node, start, end, and whether it was before the first answer token), kept on the turn's instrument. It's logged in production as one structured line per turn, so the budget is observable in production too.
- **Budget table:** `agent/budgets.py` maps each turn type to its maximum calls and allowed nodes (§4.1), along with the exception list.
- **Turn type:** read from what the turn actually did (tools called, people, derivation), not from a guess up front. The fast tests and the L1 check share that classifier.

### 5.2 Stated values travel with the decision call (replaces the separate extraction call)

**Today:** `resolve_inputs` makes its own model call (1.2–1.6 s) whenever the message contains a digit, to find values the user stated.

**Change:** `compute_benefit` takes a new argument, `stated`: the values the user gave, in plain names. For example:

```json
{"benefit": "retirement_annuity", "stated": {"age": 61, "service_years": 10, "salary": 50000, "year_joined": 2005}}
```

The model is already reading the conversation when it decides to compute, so the extraction costs no extra call. The schema accepts **any** name, not a fixed list, so new inputs need no code. Code then:
1. **Maps names** to declared inputs through `facts.ALIAS_GROUPS`. An unknown name is kept in the ledger, unused by the formula, so nothing is lost.
2. **Checks each value against the user's own words** with `_stated_in` (now including "$85k"). A value that isn't there is **rejected** and logged, never used as "stated". So the model can't invent a stated value (P3).
3. **Records it with `source: stated`** in the fact ledger, so corrections persist across turns as they do now.

The separate `resolve_inputs` model call is **removed from the hot path.** For rollback, `settings.extract_with_separate_call` restores it (default off).

**If the model leaves a value out:**
- **an input still missing** → the engine reports it, and the answer asks for it (P2 and the ASKING rule), so no wrong figure results;
- **a correction left out** → caught by the S11 continuity cases in the gate.

**Tests:**
- stated values reach the engine with origin `stated`;
- a value not in the user's words is rejected;
- "actually 23 years" overrides the record;
- an unknown name lands in the ledger;
- "And if they worked until 65?" makes **no** extraction call;
- the budget test for "figure" turns passes at 2.

### 5.3 Coverage decided by rule

**Today:** `check_coverage` (a model call, ~2 s) is offered on every turn.

**Change:** coverage is settled in code: the record's Article, else `deployment_article`, else unresolved, as `_record_coverage` already does. The coverage tool is offered **only on turns where `classify_self_reference` finds the member describing themselves** (asserted, stipulated or doubted). That's the existing rule that a question goes to the member only when the member spoke. The same rule works whether a deployment serves one Article or several.

**Tests:**
- with no self-description, the tool isn't offered and no coverage call is made;
- "I am a park employee" still offers it, and the existing coverage tests pass.

### 5.4 Prefetch the sections a question names

The pre-search that always runs also fetches `triage`'s `explicit_sections`, concurrently with the search. The model starts with that text in front of it.

**Tests:** a named-section question has no `get_section` and no second search in its trace; the section is in the rendered evidence.

### 5.5 Each section's text once per prompt

When a section's text is already in the conversation (from the pre-search, `get_section` or completion fetches), a later addition refers to it ("Section 8-138 is above") rather than repeating it. No evidence is removed.

**Tests:** the answer prompt contains each section's text exactly once; the L1 citation and verification checks are unchanged; repeated section text in the report is 0.

### 5.6 Embedding inside the search service

Use Azure AI Search's **integrated vectorizer** (an Azure OpenAI vectorizer on the index, with `VectorizableTextQuery` at query time): one round trip instead of two, and no stored vectors change. If the vectorizer can't be added, start the keyword leg while the embedding is in flight.

**Tests:** for 20 L1 queries, the top sections match today's (same set, rank within ±2).

## 6. Later stages (outlined; each gets its own spec)

**Stage 2: single-call answering.** Each model call may either **stream the answer** or **call tools**. The fact blocks (`_worked_from`, `_assumed`, `_tier_unasked`, …) are rendered before every call, not only at compose time. A turn that needs no tool answers in **one** call, so statute turns reach ~1.6–2.3 s. A turn that needs a tool makes two calls, the second of which streams the answer. `compose` becomes the instructions present on every call, rather than a separate step. Budget: "no tool needed" goes from 2 to 1.

**Stage 3: accuracy from prepared knowledge** (it also takes derivation off the hot path):
- **A concept-to-section map** for the corpus, built at ingestion. "Who counts as an employee" leads to §8-113 without a search lottery, and every new Article brings its own map.
- **Overriding-provision tags** at ingestion ("notwithstanding…", e.g. §1-160 over §8-138), pinned into evidence for the members they reach. This is the deferred derivation fix.
- **Curated, approved formulas** for survivor, disability and refund benefits, alongside the core three. Derivation becomes the rare exception, with its own budget.
- **Code-level helpfulness:**
  - compute at the earliest eligible age instead of deriving;
  - recompute when the user answers what was asked;
  - keep provisions that turn on facts a member can't know (§1-162) out unless asked.

**Stage 4 (decision pending): compute before the model call.** On turns where code already knows the person and the question is an amount or a what-if, the engine computes first, and the model presents the result in one call. Budget: figure turns go from 2 to 1.

## 7. Expected path (estimated in-region p50)

| Turn type | Now | After Stage 1 | After Stage 2 | After Stage 4 |
|---|---|---|---|---|
| Statute, names a section | ~4.5 s | ~2.5–3 s | **~1.8–2.3 s** | same |
| Statute, open | ~3 s | ~2.8 s | **~1.8–2.3 s** | same |
| Figure / what-if | ~4–5 s | **~3–3.5 s** | ~3 s | **~1.8–2.5 s** |
| With derivation | ~16 s | ~14 s | ~13 s | **< 7 s once Stage 3 makes it rare** |

**What makes it hold as you build:** P1 and P2, enforced by the fast budget tests, keep new features from adding calls. P3 and the prepared knowledge of Stage 3 keep accuracy from depending on the model finding things. P5 grows the gate with every feature.

## 8. Open decisions

1. **Stage 4, option B:** may the engine compute *before* the model call on clear calculation turns? It changes the stated rule that the model chooses when to compute. Needed before Stage 4.
2. **Integrated vectorizer (5.6):** an index change on `pension-code-prod`; the Search service must be allowed to call the embedding deployment.
3. **Azure Monitor access (4.5):** read access to the OpenAI resource's metrics, for calibration.
