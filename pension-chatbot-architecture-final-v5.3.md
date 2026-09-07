# Pension Chatbot — Architecture & Implementation Plan

**Scope:** Limited-pilot chatbot for Illinois pension plan members — active, separated, and retired — to ask questions about their pension and get accurate, cited, and (where applicable) calculated answers grounded in the Illinois Pension Code (40 ILCS 5/). Not tied to any single employer's IT tenant — the pilot population signs up directly, not through an organizational account.
**Constraint:** 2-week build window. **Strategy:** prove the entire pipeline on a small, real, adversarially-chosen slice of the code before committing engineering time to ingesting all ~20 Articles.
**Confirmed decisions:** limited pilot rollout, not full public launch (§10) · full formula coverage across the entire code is the end-state goal, reached progressively (§12) · human/legal/actuarial review is required for capabilities promoted to `authoritative`, while ordinary `estimate` execution follows the risk-gated policy in §7.2 · employee data is synthetic for the pilot (§7.4).

---

## 1. The one architectural decision everything else follows from

Pension questions can contain **multiple intents in the same request**. A single employee message may ask what the law says, whether they qualify, what their benefit would be, and how the answer changes under one or more what-if scenarios. The system therefore uses an **LLM agent as the planner and tool selector**, while keeping evidence handling structured and calculation execution deterministic.

- **"What does the law say?"** — informational, eligibility, definitions, and legal explanations. A retrieval problem: find the right section(s) of a ~2,000-page code and explain them with citations.
- **"What will my pension be?"** — a calculation. The agent may construct the calculation logic from the retrieved statutory evidence, but the actual arithmetic is executed by a deterministic calculation engine rather than free-form model arithmetic.
- **"What if I retire at 58, 60, or 62?"** — scenario execution. The agent may create multiple hypothetical input sets and run the same or different calculation capabilities for each scenario.

The system therefore treats the Pension Code as **three complementary assets/layers**, with a deterministic calculation interpreter / controlled custom-handler layer:

1. **Unstructured legal corpus** — the complete statute, chunked and indexed for retrieval and citation.
2. **Reusable calculation knowledge** — structured calculation recipes discovered by the agent from that corpus and persisted for reuse.
3. **Deterministic execution layer** — a small rule interpreter plus specialized handlers that execute structured recipes without asking the LLM to perform authoritative arithmetic.

The key design principle is **not** "every formula must be manually encoded before the system can calculate." Instead:

> **The agent may discover and construct a calculation plan from the legal corpus at query time, and the system may persist that plan for reuse; however, the plan must pass deterministic structural/evidence validation before execution, and every result must retain its provenance.**

A newly discovered calculation does **not** have to wait for a human administrator before it can be reused for ordinary estimate-style questions, provided it satisfies the automated validation policy. Human/legal/actuarial review remains an escalation and promotion mechanism for high-risk or authoritative calculations rather than a prerequisite for every new recipe.

The **Calculation Knowledge Registry** is therefore both a reusable memory and a governance layer. It stores logical capabilities/recipes, source sections, effective-date context, rule specifications, evidence fingerprints, required inputs, outputs, and lifecycle state. The LLM can select an existing recipe or derive a new one from evidence; it never selects arbitrary executable code.

---
---

## 2. Build strategy: validate on two Articles before scaling ingestion

Full ingestion of ~20 Articles is real work — parsing quirks, chunking edge cases, and retrieval tuning all cost more to fix *after* they've been replicated across the whole corpus than before. So Week 1 doesn't start with "ingest everything." It builds the **entire pipeline, end-to-end, against two Articles only**, then benchmarks it against real test questions before scaling. This holds even though full-document ingestion and full formula coverage are now confirmed end-state requirements (§12) — validating small first is exactly what makes committing to that larger goal safe.

**Article 11** (Laborers' & Retirement Board Employees' Annuity and Benefit Fund, Chicago) is the primary target — self-contained, medium complexity, real annuity/disability/survivor formulas.

**Article 12** (Park Employees' & Retirement Board Employees' Annuity and Benefit Fund) is the cross-article-confusion check: same city, adjacent article number, nearly identical drafting pattern — the sharpest available stress test. (Article 18, Judges — short, structurally different — is an optional third if Phase 1 has slack, useful for parser coverage but not testing the same failure mode.)

---

## 3. MVP scope vs. Phase 2

### Building now (MVP)
- Full pipeline validated end-to-end against Article 11 + Article 12 first (§2), *then* scaled to all Articles
- Azure AI Search, hybrid retrieval (vector + BM25 + semantic ranker), one index
- **Agentic calculation planning:** the LLM can combine legal retrieval, employee data, existing calculation recipes, and new evidence-derived plans in one request
- **Calculation Knowledge Registry:** reusable recipes are persisted automatically after successful validation; they are not dependent on an admin manually creating every formula
- **Deterministic calculation interpreter:** supports a small declarative rule vocabulary for common arithmetic/conditions, with a controlled custom-handler path for genuinely complex algorithms
- Calculation/recipe lifecycle with `discovered → agent_derived → validated → approved/stale/retired` semantics (§7.2), with execution policy controlling whether a recipe may be used for an estimate or authoritative calculation
- Alembic-managed Postgres schema (§7), realistic synthetic employee data (§7.4)
- LangGraph orchestration graph (§9), built with concurrent dispatch for independent retrieval/lookup calls and independent tasks from the start (§13) — required to meet §9.8's latency targets, not an optional optimization
- Plain Container App hosting for the backend
- Entra External ID login (new CIAM tenant, open self-service sign-up); email-matched linking to pre-seeded employee records (§10)
- Session-level memory only — the model doesn't recall context across separate sessions (§9's state is per-session); this is distinct from durable calculation knowledge, which is stored separately in the registry and can be reused across sessions
- Minimum viable compliance logging to Log Analytics
- **On-demand calculation discovery:** the pilot does not require complete manual encoding of every calculation in the code before launch; unsupported calculations become discoverable/trackable candidates through actual user requests

### Explicitly deferred to Phase 2
- Completing broad calculation-capability coverage across the full corpus
- Automated Public Act change detection + staging/production index promotion workflow
- Automatic stale-recipe invalidation at scale and background re-derivation of affected recipes
- Rich reviewer UI for high-risk calculation promotion and side-by-side rule diffs
- Foundry Agent Service hosting adapter, cross-session semantic memory
- Formula engine split into its own governed microservice
- Standalone graph database and standalone reranker service (§5)
- Azure Front Door + WAF, private endpoints/VNet integration — safe to defer given confirmed limited-pilot scope (§10)
- Advanced multi-model routing
- StateRAMP/Azure Government evaluation, formal accessibility audit
- Real (non-synthetic) employee data integration
- Tracking mechanism for pending General Assembly bills (§14 — still open)

---
---

## 4. Research findings — verified against primary sources

- **Structure confirmed:** Chapter 40 → Act 5 (40 ILCS 5/) → Article (one per system) → Division (optional, nullable) → Section → Subsections.
- **Repealed text is a real hazard** — needs a `status: active | repealed` field at ingestion.
- **The "effective date trap" is confirmed by ILGA itself**: the site states statutory changes can appear before they take effect, and directs readers to the underlying Public Act for what's actually in force.
- **Tier 2's annual CPI adjustment**: the Illinois Department of Insurance calculates the salary cap/COLA from CPI-U each year and delivers figures to boards by November 1 — an administrative determination under the code's formula, not a text change, so the formula engine needs "this year's parameter" versioned separately from "the formula's logic."
- **Correction to the original research:** Public Act 104-0065 (August 2025, the "Public Safety Tier II Adjustment Act") enhances Tier 2 benefits for Chicago police/fire — it does not reclassify anyone into Tier 1. Bills that would do a full Tier 1 reclassification remain pending, not enacted. **Never treat pending bill text as current law** — enforced by the `status` field plus an ingestion-source rule (statute text only, never bill text).
- **Cross-system references are a second category** beyond in-Article citations: the Retirement Systems Reciprocal Act (Article 20) links service credit across systems.

---

## 5. RAG architecture: why hybrid, and how it actually runs

### 5.1 Why not vector-only or vectorless-only

| Approach | Handles well | Fails on | Verdict |
|---|---|---|---|
| Vector-only | Plain-language questions | Exact citations; loses Article/Tier context once split | Not sufficient alone |
| Vectorless-only (BM25/structural) | Exact section lookups | Abstract questions without statutory vocabulary | Not sufficient alone |
| **Hybrid** | Both | Higher build complexity | **Chosen** |

Vector and keyword search run against the same records in parallel — not a fallback chain — and Azure AI Search's built-in semantic ranker fuses and re-scores both result sets in one request.

### 5.2 What one indexed record looks like

```json
{
  "id": "16-133_c",
  "content": "If the disability commences after the member's attainment of age 62...",
  "content_vector": [0.0123, -0.0456, "... one float per embedding dimension"],

  "subsection_id": "(c)",
  "subsection_text": "(c) If the disability commences after the member's attainment of age 62...",
  "section_text": "Sec. 16-133. [full text of subsections (a) through the last one]",

  "chapter": "40", "act": "5", "article": "16", "division": null,
  "section": "16-133", "section_title": "Retirement annuity amount",

  "effective_date": "2011-01-01",
  "status": "active",
  "calculation_relevance": ["benefit_formula"],
  "referenced_sections": ["16-133.2"],
  "referenced_subsections": ["16-133(a)"],
  "source_public_act": "P.A. 96-0889"
}
```

`calculation_relevance` is a discovery signal, not a claim that the section is a standalone formula. It can contain roles such as `eligibility_rule`, `input_definition`, `service_calculation`, `benefit_formula`, `reduction`, `limit`, `parameter`, `actuarial_method`, `refund`, or `cross_system_rule`. A section can participate in a calculation without becoming a separate callable capability. This distinction matters because Article 11 distributes salary, service, retirement, survivor, disability, transfer, refund, and actuarial rules across multiple interacting sections.

### 5.3 Query-time flow

```
 Employee question
        │
        ├─────────────┬─────────────┐
        ▼              ▼
 Vector search     BM25 keyword search
        └──────┬──────┘
               ▼
   Semantic ranker (fuses + re-scores)
               ▼
   Top-k Clauses, each with hierarchy metadata
               ▼
   Context assembly: walk up toward Section,
   stop at a token budget (~800 tokens)
               ▼
   Cross-reference resolution (plain ID-filter fetch)
               ▼
   Guardrail: citations checked against retrieval
               ▼
   Cited, grounded answer
```

### 5.4 What we deliberately simplified

- **No standalone graph database** — cross-references are a metadata field resolved by direct lookup.
- **No standalone reranker service** — Azure AI Search's built-in semantic ranker covers this role.

### 5.5 Agentic query handling

The retrieval layer is not restricted to a binary informational/calculation classification. The LangGraph agent may create multiple tasks from one user message and may either **reuse an existing calculation recipe or derive a new one from the retrieved legal evidence**.

```
Employee question
      │
      ▼
LLM planner
      │
      ├── policy / eligibility task ─────► Azure AI Search
      │
      ├── calculation task
      │       │
      │       ├── matching recipe ───────► Calculation Knowledge Registry
      │       │
      │       └── no matching recipe ────► Evidence → new rule plan
      │                                      │
      │                                      ▼
      │                                Validation/normalization
      │
      └── scenario task(s) ──────────────► same recipe/plan + different inputs
      │
      ▼
Deterministic calculation interpreter / controlled custom handler
      │
      ▼
Evidence + deterministic results
      │
      ▼
Answer generation + provenance guardrail
      │
      ▼
Persist/update reusable recipe
```

A newly derived recipe is **evidence-backed but not automatically treated as a legal determination**. The corpus remains the source of legal truth; the recipe is a cached interpretation of the corpus. Before reuse, the system verifies that the cited source sections still match the stored evidence fingerprints and that the recipe remains applicable to the requested date/system/tier.

This lets the system learn from its own successful work without requiring a developer or administrator to manually create a new formula after every novel question. It also means a novel or unsupported calculation can still receive a useful cited legal explanation even when the system does not have enough validated structure to safely produce a number.

---
---

## 6. Recommended architecture: component walkthrough

```
 Employee asks a question
            │
            ▼
 ┌──────────────────────────────────────────┐
 │  Orchestration layer: LLM planner          │
 │  decomposes into 1..N tasks (§9) —         │
 │  a scenario question produces N formula    │
 │  tasks, not one                            │
 └──────────────────────┬──────────────────────┘
                         ▼
         ┌───────────────┼────────────────────┐
         ▼                ▼                     ▼
  Policy search    Employee record      Calculation Knowledge
  (Azure AI         (Postgres,           Registry + engine
   Search)           row-level           (recipe reused, validated,
                      security)           or derived per task —
                                          execution policy gates
                                          estimate vs. authoritative)
         └───────────────┼────────────────────┘
                          ▼
          Guardrail: every task's output checked
          by provenance type (§9) before any of
          them reach the answer
                          │
                          ▼
          Cited, calculated answer covering every
          requested task and scenario in one reply
```

- **Orchestration layer.** Runs an LLM planner inside an explicit LangGraph graph. The planner decomposes the request into one or more tasks, selects from approved retrieval/formula tools, and may execute several calculations or scenarios in one request. The graph still enforces the non-negotiable boundaries and always runs the guardrail before returning.
- **Policy search (Azure AI Search).** The hybrid retrieval in §5. The orchestrator never invents a citation.
- **Employee record (Postgres).** System-of-record for a *linked* employee's own stored data — one of two calculation-input sources the orchestrator can draw on, the other being parameters stated directly in the question (§9). One fetch serves as the shared baseline for every task in the request, not a separate fetch per task. Row-level security scopes a session to its own employee. Schema in §7.
- **Calculation Knowledge Registry + engine.** The registry stores reusable logical capabilities and agent-derived recipes, including rule specifications, evidence fingerprints, applicability, effective dates, required inputs, outputs, and execution policy (§7.2). The deterministic engine executes the resulting structured rule plan. Scenario execution is repeated invocation with different hypothetical inputs; it is orchestration, not a separate service.
- **Guardrail.** Confirms provenance by type, per task: legal facts/citations trace to retrieved evidence; calculated values trace to a deterministic calculation result; user-provided values trace to the user message; employee-specific values trace to the authenticated employee record. It also verifies that reused recipes still match their evidence fingerprints and that every executed rule/handler is permitted by its lifecycle and execution policy.

**Deployment topology:** two things get deployed — a frontend and a backend Container App (LangGraph, guardrails, and formula-engine code run as one process inside it). Everything else is a managed Azure service. Scaling means more replicas, not more containers.

---

## 7. Data layer: schema, review workflow, synthetic data

### 7.1 Schema (Postgres, managed by Alembic)

```sql
-- Reference table: one row per Article, all ~20 eventually, populated as ingested
CREATE TABLE pension_articles (
    article_number   SMALLINT PRIMARY KEY,             -- e.g. 11, 12, 16
    system_name      TEXT NOT NULL,                    -- "Laborers' & Retirement Board Employees' Annuity and Benefit Fund"
    chapter          TEXT NOT NULL DEFAULT '40',
    act              TEXT NOT NULL DEFAULT '5',
    ingestion_status TEXT NOT NULL DEFAULT 'not_ingested'
                     CHECK (ingestion_status IN ('not_ingested','validating','ingested')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Employees (synthetic for the pilot; schema is identical for real data later)
CREATE TABLE employees (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                 TEXT UNIQUE NOT NULL,         -- pre-seeded contact email; matched against the External ID token's email claim to link entra_object_id on first login
    entra_object_id       TEXT UNIQUE,                  -- Entra External ID 'oid' claim; NULL until first login links it — pre-seeded records start unlinked
    article_number        SMALLINT NOT NULL REFERENCES pension_articles(article_number),
    tier                  SMALLINT NOT NULL CHECK (tier IN (1,2)),
    birth_date            DATE NOT NULL,
    hire_date             DATE NOT NULL,
    employment_status     TEXT NOT NULL DEFAULT 'active'
                          CHECK (employment_status IN ('active','separated','retired')),
    total_service_years   NUMERIC(5,2) NOT NULL DEFAULT 0,
    current_annual_salary NUMERIC(12,2) NOT NULL,
    is_synthetic          BOOLEAN NOT NULL DEFAULT true,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE employees ENABLE ROW LEVEL SECURITY;
ALTER TABLE employees FORCE ROW LEVEL SECURITY;
CREATE POLICY employee_self_access ON employees
    USING (entra_object_id IS NOT NULL AND entra_object_id = current_setting('app.current_entra_id', true));
-- Linking (first login only) runs as a separate, narrowly-scoped statement outside this
-- policy's reach — e.g. a SECURITY DEFINER function that looks up by email claim and sets
-- entra_object_id — since a row with entra_object_id still NULL is, correctly, invisible
-- to every session under the policy above. No pre-shared code for MVP: signup is open,
-- and only email-matched, pre-seeded rows ever get linked. Anyone who signs up without a
-- matching email is a real, authenticated user with no linked employee record — general
-- Q&A and hypothetical calculations (§9) still work for them; "my actual number" doesn't.

-- Salary history — final-average-salary formulas need multiple years, not just current salary
CREATE TABLE employee_salary_history (
    id              BIGSERIAL PRIMARY KEY,
    employee_id     UUID NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    effective_date  DATE NOT NULL,
    annual_salary   NUMERIC(12,2) NOT NULL,
    UNIQUE (employee_id, effective_date)
);

-- Chat sessions. employee_id is NULL for unauthenticated/unlinked sessions; session_token
-- is what scopes THOSE to the one browser/client that opened them, instead of leaving every
-- anonymous session readable by anyone (the previous policy's actual, unintended behavior).
CREATE TABLE sessions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id    UUID REFERENCES employees(id),
    session_token  TEXT NOT NULL DEFAULT encode(gen_random_bytes(24), 'base64'),
    title          TEXT,                     -- sidebar label; set from the first message (§8), nullable until then
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','ended'))
);
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY session_self_access ON sessions
    USING (
        (employee_id IS NOT NULL AND employee_id = current_setting('app.current_employee_id', true)::uuid)
        OR
        (employee_id IS NULL AND session_token = current_setting('app.current_session_token', true))
    );

-- Turn-by-turn messages: conversation state AND the DB-level audit trail.
-- No RLS in the prior revision — the most sensitive table (actual message text) was
-- relying on the API layer alone. Delegates to sessions' own boundary rather than
-- redefining it, so there's one definition of "whose session is this," not two.
CREATE TABLE session_messages (
    id                BIGSERIAL PRIMARY KEY,
    session_id        UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role              TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content           TEXT NOT NULL,
    citations         JSONB,        -- [{article, section, subsection_id}, ...]
    tool_calls        JSONB,        -- [{tool, task_id, formula_id, recipe_version, inputs, result,
                       execution_policy, knowledge_status, risk_tier}, ...]
    guardrail_passed  BOOLEAN,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_session_messages_session ON session_messages(session_id, created_at);
ALTER TABLE session_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE session_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY session_messages_self_access ON session_messages
    USING (session_id IN (SELECT id FROM sessions));

-- Calculation knowledge registry: stores reusable agent-derived recipes
-- and promoted/approved capabilities. Rules are declarative where practical; complex
-- algorithms may point to a controlled custom handler.
CREATE TABLE formula_registry (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id             TEXT NOT NULL DEFAULT 'il_pension_code_40_ilcs_5',
    article_number          SMALLINT NOT NULL REFERENCES pension_articles(article_number),
    formula_id              TEXT NOT NULL UNIQUE,          -- logical capability ID
    section                 TEXT,                          -- primary source section; nullable for multi-section recipes
    formula_name            TEXT NOT NULL,
    formula_type            TEXT NOT NULL,
    tier                    SMALLINT,                      -- NULL if tier-independent
    description             TEXT NOT NULL,
    required_inputs         JSONB NOT NULL DEFAULT '[]',
    optional_inputs         JSONB NOT NULL DEFAULT '[]',
    output_schema           JSONB NOT NULL DEFAULT '{}',
    applicability           JSONB NOT NULL DEFAULT '{}',
    dependencies            JSONB NOT NULL DEFAULT '[]',
    rule_specification      JSONB NOT NULL DEFAULT '{}',  -- normalized declarative rule/plan
    source_sections         JSONB NOT NULL DEFAULT '[]',  -- all legal sections used by the recipe
    evidence_fingerprint    JSONB NOT NULL DEFAULT '{}',  -- source section/version hashes
    execution_type          TEXT NOT NULL DEFAULT 'declarative'
                            CHECK (execution_type IN ('declarative','custom')),
    execution_reference     TEXT,                          -- internal handler reference; never LLM-selected
    implementation_hash     TEXT,                          -- engine/handler fingerprint where applicable
    version                 INT NOT NULL DEFAULT 1,
    effective_start_date    DATE NOT NULL,
    effective_end_date      DATE,
    annual_parameter_set    TEXT,
    knowledge_status        TEXT NOT NULL DEFAULT 'discovered'
                            CHECK (knowledge_status IN
                              ('discovered','agent_derived','validated','approved','stale','retired')),
    risk_tier               TEXT NOT NULL DEFAULT 'high'
                            CHECK (risk_tier IN ('low','medium','high')),
    execution_policy        TEXT NOT NULL DEFAULT 'no_execute'
                            CHECK (execution_policy IN ('no_execute','estimate','authoritative')),
    derivation_method       TEXT,                          -- e.g. 'agent_from_statute', 'reviewed'
    completeness_check      JSONB NOT NULL DEFAULT '{}',  -- independent completeness/risk evidence
    created_from_message_id BIGINT REFERENCES session_messages(id),
    reviewed_by             TEXT,
    reviewed_at             TIMESTAMPTZ,
    test_reference          TEXT,
    source_public_act       TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_number, formula_id, version)
);
CREATE INDEX idx_formula_lookup ON formula_registry
    (article_number, tier, formula_type, effective_start_date)
    WHERE knowledge_status IN ('agent_derived','validated','approved');

CREATE INDEX idx_formula_status ON formula_registry (knowledge_status, execution_policy);
```

All of the above ships as Alembic migrations, not hand-run SQL — migration #1 in Phase 0 creates `pension_articles`, `employees`, `employee_salary_history`, `sessions`, `session_messages`, `formula_registry`, and all three RLS policies (`employees`, `sessions`, `session_messages`) together, since they're the base schema every later phase builds on.

### 7.2 Calculation Knowledge lifecycle and low-manual-effort workflow

The system does **not** require a human to manually define every calculation before the agent can answer a question. Instead, calculation knowledge is created progressively from real user demand and the statutory corpus.

However, **structural validity is not the same thing as legal completeness**. A recipe can cite real sections, use valid DSL syntax, and still miss a sibling subsection, exception, historical rule, or cross-reference that changes the result. The execution gate therefore has two distinct layers: deterministic structural/evidence validation and an independent completeness/risk gate before `estimate` execution is allowed.

The lifecycle is:

```text
DISCOVERED
    │
    ▼
AGENT_DERIVED
    │
    ▼
VERIFY_RECIPE_COMPLETENESS
    │
    ├─────────────── fail ──────────────► re-derive / limitation
    │
    ▼
DETERMINISTIC VALIDATION
    │
    ├─────────────── fail ──────────────► re-derive / limitation
    │
    ▼
RISK TIER ASSIGNMENT
    │
    ├── LOW/MEDIUM + required evidence gate passes ──► VALIDATED + estimate
    │
    └── HIGH / complex / insufficient evidence ──────► VALIDATED + no_execute
                                                               │
                                                               ▼
                                                      HUMAN PROMOTION
                                                               │
                                                               ▼
                                                           APPROVED
                                                               │
                                                               ▼
                                                        authoritative
```

**1. Discover.** Ingestion marks sections using `calculation_relevance` signals and the runtime agent also discovers calculation needs from actual user questions.

**2. Agent-derived recipe.** When no reusable recipe matches, the LLM retrieves the necessary legal evidence and constructs a structured calculation plan: inputs, derived inputs, conditions, branches, operations, exceptions, dependencies, effective-date assumptions, and source sections. This remains `agent_derived`, not executable code.

**3. Verify recipe completeness — mandatory.** An independent critic pass reviews every newly derived recipe before it can become `validated`. It challenges:
- sibling subsections in the same Section;
- exceptions, provisos, and alternative branches;
- cross-referenced Sections that may alter eligibility or the amount;
- historical/effective-date provisions;
- conflicting or superseding provisions;
- whether retrieved evidence is sufficient to support every branch in the proposed recipe.

The critic returns structured findings and cannot activate execution or bypass deterministic validation. It is a challenge pass, not a second source of legal truth.

**4. Deterministic validation.** A deterministic validator checks that:
- every cited section exists in the current indexed corpus;
- every numeric constant in the recipe has a source citation;
- every referenced section/subsection is actually present;
- rule syntax uses only the supported calculation DSL;
- all required inputs are typed and declared;
- dependencies exist and contain no cycles;
- effective-date ranges are coherent;
- the recipe does not select an internal executable handler;
- the stored evidence fingerprint is captured for later reuse.

**5. Deterministic risk-tier assignment.** Risk is assigned by code from observable signals, not by the agent's own self-assessment. Initial signals include source-section count, conditional-branch count, historical/effective-date branching, cross-Article or reciprocal-service dependencies, custom actuarial handlers, maintained high-risk topic lists, availability of independent worked-example evidence, and input ambiguity. The result is `low`, `medium`, or `high`.

**6. Execution gating.**
- **Low risk:** may become `validated` + `estimate` after completeness verification, deterministic validation, and a configured worked-example/reference check where applicable.
- **Medium risk:** may become `validated` + `estimate` only when configured evidence/worked-example thresholds are satisfied; otherwise remain `no_execute`.
- **High risk:** defaults to `no_execute` and requires human/legal/actuarial promotion before an authoritative result is permitted. The system may still provide the cited legal explanation.
- **Authoritative:** only an `approved` capability may use `execution_policy='authoritative'`.

This is deliberately risk-based rather than "human approval before every calculation." The objective is low manual effort without treating a structurally valid agent interpretation as a legally authoritative determination.

**7. Reusable execution.** A validated recipe may be reused for an estimate only when its execution policy, risk tier, completeness result, source fingerprint, and applicability checks permit it. On reuse, the system re-checks source fingerprints and applicability against the current question.

**8. Human promotion where warranted.** Legal/actuarial review remains available for high-risk capabilities, actuarial handlers, complex survivor/disability cases, or any capability the organization wants to classify as `authoritative`. Approval changes the knowledge state to `approved` and permits authoritative execution.

**9. Versioning.** A changed statute, effective-date rule, or parameter set creates a new recipe/version rather than mutating historical knowledge. Old recipes remain for reproducibility and can be marked `stale` or `retired`.

**Known limitation of fingerprint invalidation.** Fingerprints catch changes to Sections a recipe already depends on, but cannot by themselves detect a *new* Section becoming relevant to an existing calculation pathway. Broader change-detection/re-derivation is therefore a Phase 2 capability (§3).

**Concurrent novel derivations.** Two near-simultaneous novel questions may initially derive separate recipes. Normalize the logical recipe key from calculation type + article/system + source sections + applicability/effective-date scope, then reconcile duplicates transactionally before persistence. The canonicalization step is deterministic; the LLM does not choose the final registry identity.

### 7.3 Rule Specification / declarative execution model

The preferred execution format is a **small, typed declarative rule DSL**, stored as YAML in source control when human maintainability and diffing matter and represented as validated JSON/JSONB at runtime. The engine interprets the specification; the specification is not executable code.

Example:

```yaml
formula_id: article11_retirement_example
execution:
  type: declarative

inputs:
  - name: final_average_salary
    type: money
  - name: service_years
    type: decimal
  - name: accrual_rate
    type: decimal

calculation:
  operation: multiply
  operands:
    - variable: final_average_salary
    - variable: service_years
    - variable: accrual_rate

parameters:
  accrual_rate:
    value: 0.024
    source:
      section: "11-134"
      subsection: "(a)"
```

Supported primitives should initially be small and strongly typed:

`ADD`, `SUBTRACT`, `MULTIPLY`, `DIVIDE`, `MIN`, `MAX`, `IF`, `COMPARE`, `LOOKUP`, `PERCENTAGE`, `ROUND`, `CAP`, `FLOOR`, `CEILING`, date comparisons, ranges, and controlled references to other approved capabilities.

For genuinely complex actuarial logic, use:

```yaml
execution:
  type: custom
  handler_capability: actuarial.single_life_annuity
```

The registry resolves `handler_capability` to an internal implementation. The LLM never selects or modifies the underlying function name or code version.

### 7.4 Synthetic employee data (confirms open question 4)

Real employee data isn't available for the pilot, so realism has to come from the distribution design, not from real records:

- **Hire dates spanning both tiers** — enough pre-2011 (Tier 1) and post-2011 (Tier 2) synthetic employees to exercise both formula paths.
- **Service years spread 0–35**, deliberately including boundary cases: just under and just over the vesting threshold, just under and just over early-retirement age, at or near the benefit accrual cap.
- **Salary histories with realistic year-over-year growth** (roughly 2–4%, not flat), since final-average-salary calculations depend on the trend — populate `employee_salary_history` with a multi-year series per synthetic employee.
- **Scope for Phase 1:** roughly 10–20 synthetic employees split across Article 11 and Article 12 and both tiers, including the boundary cases above.
- Build this as a small seed script so the same generator can be re-run and extended as more Articles come online.
- **Each synthetic employee needs a real, testable email** so email-based linking can be exercised end-to-end in Phase 1/2.

---
---

## 8. API design

REST endpoints, backend Container App. Auth via Entra External ID bearer tokens (self-service sign-up in a dedicated CIAM tenant — see §10); general policy questions may be asked anonymously. Any endpoint that resolves to `employee_id` requires a valid token, but a valid token alone doesn't guarantee a resolved `employee_id` — see linking, below. Either way, §7.1's RLS policies are what actually enforce the data boundary; the API layer's checks are a second layer, not the only one.

**Account linking (first authenticated request only).** Sign-up is open — no gating code for MVP (§10). The first time a request arrives with a token whose `oid` doesn't yet match any `employees.entra_object_id`, the backend takes the token's email claim and looks for a pre-seeded, still-unlinked `employees` row with that email; on a match, it links them (the `SECURITY DEFINER` path noted in §7.1). No match just means an authenticated user with no linked record — general Q&A and hypothetical calculations (§9) still work for them; "my actual number" doesn't, since there's no stored record for the formula engine to read.

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /api/v1/sessions` | Optional | Start a session. Anonymous if no token — returns a `session_token` (§7.1) the client must send on every later call to that session. Bound to `employee_id` if a token is present and linking (above) resolves one. |
| `GET /api/v1/sessions` | Required, linked employee | List the caller's own past sessions — `id`, `title`, `started_at`, `last_active_at` — ordered by `last_active_at desc`. This is the sidebar feed. Anonymous/unlinked callers get an empty list (see note below), not an error. |
| `POST /api/v1/sessions/{id}/messages` | Inherited from session | Send a message, stream the answer back. Main chat endpoint. Sets `sessions.title` from a truncated first user message if `title` is still `NULL`. |
| `GET /api/v1/sessions/{id}/messages` | Inherited from session | Reload one session's conversation (page refresh / clicking it from the sidebar list above). |
| `GET /api/v1/health` | None | Liveness/readiness probe for Container Apps. |
| `GET /api/v1/admin/formulas` | Admin/reviewer role | List calculation-knowledge rows, lifecycle state, execution policy, source/evidence metadata — mainly for monitoring/escalation. |
| `POST /api/v1/admin/formulas/{id}/promote` | Admin/reviewer role | Promote a `validated` or `agent_derived` capability to `approved` / `authoritative` after review; ordinary estimate recipes do not require this endpoint. |
| `GET /api/v1/admin/articles` | Admin/ops role | `pension_articles.ingestion_status` per Article — Phase 3 progress at a glance. |

**Sidebar/history is a linked-employee feature, by design, not by oversight.** A linked employee's sessions are queryable by `employee_id` — durable, so a "your chats" sidebar works the way it does in any normal chat app. An anonymous or unlinked session only has `session_token`, held by that one browser, with nothing durable to list *across* visits (no login, no persistent identity to key a list off). So: linked employees get the full sidebar experience; anonymous/unlinked users get one continuous chat per visit, with no cross-visit history — an intentional scope line for MVP, not a bug, but flagging it since it's a real product decision the frontend needs to branch on (show the sidebar only when `GET /sessions` is available, i.e. only for linked users).

**Chat endpoint detail** — `POST /api/v1/sessions/{id}/messages` streams via Server-Sent Events, since token-by-token output matters more here than in the admin endpoints:

```
Request:
{ "content": "When can I retire if I started working for the Park District in 2016?" }

Response stream (text/event-stream):
event: preamble
data: {"text": "Let me check the applicable pension rules for your question..."}
event: status
data: {"stage": "planning"}
...
event: status
data: {"stage": "answering"}
event: token
data: {"delta": "Based on "}
event: token
data: {"delta": "Section 12-134..."}
...
event: done
data: {
  "message_id": "…",
  "citations": [{"article": 12, "section": "12-134", "subsection_id": "(a)"}],
  "tool_calls": [
    {"tool": "formula_engine", "task_id": "t1", "formula_id": "…", "recipe_version": 2,
     "knowledge_status": "validated", "risk_tier": "low",
     "execution_policy": "estimate", "result": {...}}
  ],
  "guardrail_passed": true
}
```

The example above answers a single-task question. A scenario question ("what if I retire at 58, 60, or 62") produces the same shape with three entries in `tool_calls`, one per `task_id` — not three separate messages — since §9's planner decomposes it into three tasks up front and the guardrail checks all three before any of them reach the client.

If `guardrail_passed` is `false`, the `done` event carries the safe fallback answer instead of the draft — the client never sees an unverified draft, only the final gated one.

**Why `preamble` is a separate event, and why it's templated rather than generated.** §9's graph is fully serial by design — `plan_request` → retrieval → possible recipe derivation → the mandatory critic (§9.2, node 5) → deterministic validation → execution → `generate_answer` is the only node that produces the actual answer tokens shown above. For a novel-calculation request, that chain can genuinely take several seconds before the first real token exists, which would leave the user staring at nothing. The `preamble` event is emitted **immediately on request acceptance, before the graph runs at all** — not by an LLM call, but from a small fixed set of templated strings (e.g. "Let me check the applicable pension rules for your question..."), selected by a cheap keyword check on whether the message looks like a calculation or a general question. This is deliberately *not* the "fast planner emits a preamble" pattern sometimes suggested for this kind of latency gap: an LLM-generated preamble, even a short one, is a fresh unvalidated generation, and nothing structurally prevents it from drifting into an actual claim ("you may qualify for early retirement...") before any evidence has been retrieved — which would be the one invariant this whole architecture exists to protect, violated in the first three words of the response. A templated preamble costs one string lookup, not an API call, and carries zero provenance risk because it makes no claim about anything. `status` events between `preamble` and the first `token` are optional UI sugar (a spinner label) and carry no content requiring guardrail treatment — they can be dropped entirely if the frontend doesn't need them.

**What this changes about §9's graph, and what it doesn't.** Nothing inside the graph moves earlier and no safety gate is skipped — `generate_answer` still only runs after the critic and deterministic validation have done their work for a newly derived recipe. The only change is that the *client* now sees something within milliseconds of sending a message, decoupled from how long the graph actually takes. Two follow-on optimizations, both inside the existing node boundaries and neither loosening a gate: (1) `retrieve_policy`, `fetch_employee_record`, and the *registry-lookup* half of `retrieve_or_derive_recipe` (§9.2, nodes 2–4) have no dependency on each other and should run concurrently (`asyncio.gather`) rather than sequentially — only the *derive-a-new-recipe* half of node 4 genuinely depends on `retrieve_policy`'s output and has to wait for it; (2) the critic in node 5 stays mandatory for every newly derived recipe exactly as designed, but should default to the cheapest model tier and only escalate tier — not skip — when a cheap deterministic pre-screen (a plain set-difference between the recipe's `source_sections` and the chunk's `referenced_sections`/sibling-subsection metadata, already sitting in the index per §5.2) flags a candidate omission. That pre-screen doesn't replace the critic; it only decides how hard the critic has to work, which is where most of the avoidable latency in the derivation path actually was.

For calculated results, client-visible `tool_calls` expose `knowledge_status`, `risk_tier`, `execution_policy`, and recipe/formula version. Any result produced under `execution_policy='estimate'` must be labeled plainly as an estimate; only `approved` + `authoritative` results may be presented as authoritative calculations.

**Minor details worth deciding now rather than mid-build:**
- **Correlation IDs.** `session_id` + the returned `message_id` are the correlation key threaded through Log Analytics traces — every retrieval call, formula call, and guardrail check for one answer should be findable by that pair alone.
- **Sign-up is open, not gated, for MVP.** A workforce Entra tenant's "assigned users/groups" model doesn't exist in a CIAM tenant anyway — Entra External ID uses self-service sign-up by design. "Limited pilot" for now means only pre-seeded, email-matched employees get personalized answers (linking, above); it isn't a hard technical gate on who can create an account. If exclusivity needs to be enforced later, a custom authentication extension (API connector) checking sign-up attempts against an allow-list is the standard CIAM way to add it — deferred, not designed away.
- **Anonymous session scoping.** `session_token` (§7.1) is what keeps one anonymous user's chat history from being globally readable — the client stores it (e.g. a cookie) and sends it on every request to that session; the backend sets `app.current_session_token` from it for that connection, the same way an authenticated request sets `app.current_employee_id`. No token presented, no access to that session's messages, including from the backend's own general queries.
- **Basic rate limiting.** No Redis or Front Door in the MVP resource group, so a simple per-session message-rate check (e.g., reject if the last N `session_messages` rows for a session are under M seconds apart) living in the backend itself is enough for a limited pilot. Open sign-up (above) makes this more relevant sooner than a gated pilot would have — worth confirming it's live before Phase 4 launch, not treating it as a nice-to-have. Real rate limiting at the edge is Phase 2 (§3).

---

## 9. LangGraph state & node design

### 9.1 State schema

The graph is **agentic but bounded**. The LLM is responsible for understanding the request, decomposing it into tasks, selecting or deriving calculation capabilities, deciding which hypothetical scenarios to run, and composing the final explanation. Deterministic nodes enforce authentication, input precedence, recipe validation, effective-date resolution, calculation execution, and provenance checks.

```python
class CalculationInputs(TypedDict, total=False):
    values: dict[str, Any]
    source: dict[str, Literal["stored_record", "question_text", "derived", "system"]]


class AgentTask(TypedDict, total=False):
    task_id: str
    type: Literal["retrieve_policy", "calculate", "scenario"]
    purpose: str
    formula_id: str | None
    recipe_id: str | None
    applicable_date: date | None
    inputs: dict[str, Any]
    depends_on: list[str]


class CalculationRecipe(TypedDict, total=False):
    recipe_id: str
    formula_id: str
    status: Literal["discovered", "agent_derived", "validated", "approved", "stale", "retired"]
    execution_policy: Literal["no_execute", "estimate", "authoritative"]
    risk_tier: Literal["low", "medium", "high"]
    rule_specification: dict[str, Any]
    source_sections: list[str]
    evidence_fingerprint: dict[str, str]
    completeness_check: dict[str, Any]
    effective_start_date: date
    effective_end_date: date | None
    dependencies: list[str]


class FormulaResult(TypedDict, total=False):
    task_id: str
    formula_id: str
    recipe_version: int
    execution_policy: str
    effective_start_date: date
    effective_end_date: date | None
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    source_sections: list[str]
    recipe_fingerprint: dict[str, str]
    knowledge_status: str
    risk_tier: str


class PensionAgentState(TypedDict):
    session_id: str
    employee_id: str | None

    question: str
    agent_tasks: list[AgentTask]

    retrieved_chunks: list[RetrievedChunk]
    resolved_articles: list[int]
    resolved_sections: list[str]

    employee_record: EmployeeRecord | None
    extracted_inputs: dict[str, CalculationInputs]
    calculation_inputs: dict[str, CalculationInputs]
    missing_inputs: dict[str, list[str]]

    reusable_recipes: list[CalculationRecipe]
    derived_recipes: list[CalculationRecipe]
    formula_results: list[FormulaResult]
    tool_calls: list[dict[str, Any]]

    draft_answer: str | None
    citations: list[Citation]

    guardrail_passed: bool | None
    guardrail_failure_reason: str | None
    retry_count: int

    final_answer: str | None
```

### 9.2 Nodes

1. **`plan_request`** — LLM planner. Reads the user question plus a scoped catalog of reusable calculation capabilities. Decomposes the request into one or more tasks. The planner may reference an existing logical `formula_id`, request a new calculation derivation, or create several scenario tasks. It never selects an internal handler or code version.

2. **`retrieve_policy`** — hybrid query against Azure AI Search (§5.3). Runs for policy/eligibility tasks and whenever calculation derivation needs statutory evidence. Populates `retrieved_chunks` and resolves Articles/Sections from actual matches. Has no dependency on nodes 3 or 4's registry-lookup path — see the parallelization note below.

3. **`fetch_employee_record`** *(conditional)* — runs if a planned task needs employee-specific stored data and `employee_id` is set. One RLS-scoped fetch becomes the shared baseline for all tasks. Independent of nodes 2 and 4.

4. **`retrieve_or_derive_recipe`** — for each calculation/scenario task, first checks the Calculation Knowledge Registry for a compatible non-stale recipe (a plain Postgres lookup, independent of node 2). Only if none exists does the LLM derive a structured calculation recipe — and that half genuinely depends on node 2's retrieved evidence, so it waits for it. A new recipe remains `agent_derived` until completeness verification and deterministic validation finish.

**Parallelization note (nodes 2–4).** The registry-lookup half of node 4, node 2, and node 3 share no data dependency and should run concurrently (e.g. `asyncio.gather`) rather than sequentially once `plan_request` has decided a task needs them — this is a pure latency win with no correctness trade-off. Only the *derive-a-new-recipe* half of node 4 has a real dependency: it needs node 2's output and therefore starts after it, not alongside it.

5. **`verify_recipe_completeness`** — mandatory independent critic pass for every newly derived recipe; this stays a hard requirement, never skipped, regardless of the latency work below. It runs in two parts: first a **deterministic pre-screen** — a plain set-difference between the derived recipe's `source_sections` and the chunk-level `referenced_sections`/sibling-subsection metadata already captured at ingestion (§5.2) — flagging any candidate omission a cheap Python check can catch without a model call at all. The **LLM critic** then challenges missed sibling subsections, exceptions, cross-references, historical branches, conflicting provisions, and evidence sufficiency, and always runs — but defaults to the cheapest model tier (§9.7) and only escalates tier when the pre-screen flagged something or another complexity signal crosses threshold. The pre-screen narrows and speeds up the critic's job; it does not replace it, and it cannot itself pass a recipe. Output is a small structured result (`complete`, `missing_sections`, `missing_exceptions`, `conflicts`, `confidence`), not a prose review — it cannot activate execution or bypass deterministic validation.

6. **`extract_calculation_inputs`** *(conditional)* — structured extraction of values explicitly stated in the question, per task. It does not guess unstated values.

7. **`merge_calculation_inputs`** — deterministic. Starts from the authenticated employee record where permitted, then overlays task-specific explicit/hypothetical values. Missing required inputs are recorded rather than guessed.

8. **`validate_recipe`** — deterministic. Verifies the recipe schema, evidence citations, evidence fingerprints, applicability, effective dates, dependencies, permitted operations, required inputs, completeness result, and execution policy. It also assigns the deterministic `risk_tier`. If source sections changed, the recipe is marked `stale` and the agent re-derives it.

9. **`gate_recipe_execution`** — deterministic policy gate. Allows execution only when risk tier, completeness result, worked-example/reference evidence, and lifecycle/execution policy satisfy the configured threshold. High-risk recipes remain `no_execute` pending human promotion.

10. **`execute_calculation_tasks`** *(conditional)* — deterministic rule interpreter or controlled custom handler. Dispatches independent tasks concurrently by default (§13) — sequential only where `depends_on` genuinely requires it. A scenario is simply a task with different hypothetical inputs, so three independent scenarios run concurrently, not one after another. No free-form model arithmetic occurs here.

11. **`persist_recipe`** *(conditional)* — stores a successful new or updated recipe, its source/evidence fingerprint, completeness result, deterministic risk tier, derivation message, version, and execution policy for reuse.

12. **`generate_answer`** — LLM response generation. Uses retrieved legal evidence, calculation results, and their provenance to compose the response. When a result is `estimate`, the answer must clearly label it as an estimate and not as an authoritative pension determination.

14. **`guardrail_check`** — deterministic Python. Validates claim/value provenance and confirms that every calculation result came from a validated recipe and permitted execution path. It also checks cited sections and source fingerprints, and confirms that estimate results are labeled as estimates while authoritative results are only backed by `approved` knowledge.

15. **`handle_guardrail_failure`** *(conditional)* — bounded retry for answer generation. A calculation validation failure is not "fixed" by retrying the language model; it routes to re-verification, re-derivation, or a safe limitation.

16. **`finalize_response`** — sets `final_answer` and persists the session message, including citations, tool calls/task IDs, recipe/formula IDs and versions, evidence fingerprints, knowledge status, risk tier, execution policy, and guardrail outcome.

### 9.3 Flow

```text
plan_request (LLM agent)
        │
        ├──────────────► retrieve_policy ─────────────────────┐
        │                                                     │
        ├──────────────► fetch_employee_record ───────────────┤
        │                                                     │
        └──────────────► retrieve_or_derive_recipe ───────────┤
                                                              ▼
                                                   if newly derived:
                                                              │
                                                              ▼
                                                  verify_recipe_completeness
                                                              │
                                                              ▼
                                                    extract_calculation_inputs
                                                              │
                                                              ▼
                                                    merge_calculation_inputs
                                                              │
                                                              ▼
                                                         validate_recipe
                                                              │
                                                              ▼
                                                       gate_recipe_execution
                                                              │
                                      ┌────────────────────────┴──────────────────┐
                                      │                                           │
                                 executable                              no_execute / stale
                                      │                                           │
                                      ▼                                           ▼
                            execute_calculation_tasks                 re-derive / human review /
                                      │                                safe limitation
                                      ▼
                                 persist_recipe
                                      │
                                      ▼
                                 generate_answer
                                      │
                                      ▼
                                 guardrail_check
                                    │          │
                                  pass        fail
                                    │          │
                                    ▼          ▼
                                finalize    retry / safe fallback
```

The graph supports mixed questions naturally. The same request can retrieve legal evidence, load employee data, reuse an existing recipe, derive a missing calculation recipe, execute several scenarios, and answer everything in one response.

### 9.4 Agent boundaries

The LLM is responsible for:
- understanding natural language;
- decomposing mixed requests;
- selecting existing logical capabilities;
- deciding which scenarios to evaluate;
- deriving a proposed structured calculation recipe from retrieved evidence when no suitable recipe exists;
- identifying likely inputs and source sections;
- composing the final explanation.

The LLM is **not** trusted to:
- invent legal sources;
- alter or delete statutory evidence;
- select internal executable handlers;
- silently mutate a stored recipe without creating a new version;
- bypass recipe/evidence validation;
- fabricate employee data;
- perform authoritative pension arithmetic outside the deterministic engine;
- silently turn an estimate recipe into an authoritative calculation.

### 9.5 Execution policy and risk boundary

The execution policy is a **client-visible trust boundary**, not only an internal registry flag.

```text
knowledge_status   risk_tier   execution_policy      allowed behavior
---------------------------------------------------------------------------
agent_derived      any         no_execute             explain law / no number
validated          low         estimate               estimate may execute
validated          medium      estimate               only after configured evidence gate
validated          high        no_execute             no number; review required
approved            any         authoritative         authoritative execution allowed
stale/retired       any         no_execute             never execute
```

`generate_answer` must explicitly label `estimate` results as estimates. The SSE `done` payload and persisted `tool_calls` must expose `knowledge_status`, `risk_tier`, `execution_policy`, and recipe/formula version so the frontend and audit trail can distinguish an agent-derived estimate from an approved authoritative calculation.

### 9.6 Reusable calculation knowledge

A saved recipe is **cacheable calculation knowledge**, not model memory and not a legal determination.

On reuse:

```text
recipe
  ↓
source fingerprint check
  ↓
effective-date/applicability check
  ↓
input merge
  ↓
deterministic execution
```

If the underlying statute or relevant parameter source has changed, the recipe is invalidated/staled and the agent derives a new version. This allows the system to benefit from previous "hard work" without trusting an old interpretation indefinitely.

### 9.7 Model selection and routing

The MVP should use **basic model routing from day one**, while keeping the routing policy deterministic and bounded. Routing is decided independently for two different calls in the graph — the planner/derivation/answer-generation path, and the completeness critic (§9.2, node 5) — rather than moving them together, since the critic doing a small structured check is a fundamentally cheaper task than deriving a recipe from scratch, regardless of how hard the derivation itself was.

**Default — GPT-5.6 Luna.** Use Luna for the normal planner and answer-generation path, and as the critic's default tier for every recipe (new or not). Luna is OpenAI's fastest, most cost-efficient GPT-5.6 tier, and supports reasoning, structured outputs, and tool use.

**Escalation — GPT-5.6 Terra.** Route materially harder novel recipe derivations to Terra when deterministic complexity signals cross the configured threshold. Separately, escalate the *critic* to Terra when the deterministic pre-screen (§9.2, node 5) flags a candidate omission — the derivation escalating to Terra does not automatically escalate the critic, and vice versa. Terra is GPT-5.6's balanced, everyday-production tier.

**Exceptional/high-risk — GPT-5.6 Sol.** Reserve Sol for exceptional high-risk legal/calculation derivations, unresolved ambiguity after Terra, or evaluation failures showing that lower tiers are insufficient — for either the derivation or the critic call. Sol is GPT-5.6's flagship tier.

A practical starting policy is:

```text
Normal informational / simple calculation
    → GPT-5.6 Luna

Novel or moderate-complexity calculation (derivation)
    → GPT-5.6 Luna
    → escalate to GPT-5.6 Terra when deterministic complexity thresholds are crossed

Completeness critic (independent of the derivation tier above)
    → GPT-5.6 Luna by default
    → escalate to GPT-5.6 Terra only if the deterministic pre-screen flags a
      candidate omission, or another complexity signal crosses threshold

High-risk / complex / unresolved after Terra (either call)
    → GPT-5.6 Sol
```

The router uses deterministic signals already available in the graph: source-section count, branch/exception count, historical/effective-date complexity, cross-Article or reciprocal-service references, custom actuarial handlers, high-risk topics, the pre-screen's own findings, and prior evaluation failures.

Reasoning effort should start conservatively and be tuned from evaluation:
- Luna: `low`/`medium` for planning/derivation; `none`/`low` for answer composition and the default-tier critic.
- Terra: `medium`/`high` for escalated completeness challenges and difficult recipe derivation.
- Sol: `high`/`xhigh` only when the risk gate requires it.

The important optimization is **not** to send every request to Sol. Retrieval, completeness verification, deterministic validation, risk gating, and deterministic execution remain the correctness boundaries.

**A concrete Azure integration constraint, not just a model choice:** on GPT-5.6 and later, Azure OpenAI/Foundry rejects a Chat Completions request that includes function tools unless `reasoning_effort` is explicitly `"none"` — confirmed directly against Microsoft's own documentation. Since this graph calls tools (retrieval, employee lookup, the calculation engine, the formula registry) from reasoning-enabled calls, every tool-calling node in §9.2 needs to be built against **the Responses API**, not Chat Completions, from day one — this isn't an optional modernization, it's what makes tool calls with real reasoning effort work at all on this model family. Worth flagging in Phase 0 as an integration detail, not something to discover mid-Phase-1.

**Embeddings:** use `text-embedding-3-large` for both corpus indexing and query-time vectorization, keeping the embedding model fixed for an index version.

**Cost context:** current direct OpenAI API pricing (post the July 30 / August 21, 2026 price cuts) is roughly $0.20/$1.20 per 1M input/output tokens for Luna, $2/$12 for Terra, and $4/$20 for Sol — this supports Luna as the default for both the main path and the critic, with the stronger tiers reserved for genuine escalation. Actual Azure/Foundry billing must be checked separately for the chosen deployment and region, since Azure's rates don't necessarily track OpenAI's direct API prices.

### 9.8 Performance targets

The original targets here (a flat "time to first token ≤ 2 seconds") weren't actually achievable given §9's fully serial graph — a novel-calculation request has to clear planning, retrieval, derivation, the mandatory critic, deterministic validation, and execution before `generate_answer` produces anything, and that chain alone can exceed 2 seconds even on Luna. §8's `preamble` event is what actually closes that gap, by decoupling *something visible* from *the graph having produced a real answer*. Split accordingly:

| Metric | Target (P95) | What makes it possible |
|---|---|---|
| First visible content (`preamble` event) | ≤ 0.5 sec | Templated string, no LLM call, no graph dependency (§8) |
| First substantive answer token | ≤ 5 sec | Parallelized retrieval (§9.2) + critic-tier decoupling (§9.7) on the derivation path |
| Simple informational complete answer | ≤ 4 sec | No derivation, no critic — retrieval + `generate_answer` only |
| Existing-recipe calculation | ≤ 5 sec | Skips derivation and the critic entirely (§9.3's "if newly derived" gate) |
| Novel calculation | ≤ 7–10 sec | Full derivation → critic → validation → execution chain |
| Mixed/scenario request | ≤ 10 sec | Concurrent dispatch of independent `agent_tasks` (§9.1, §13) — not sequential |
| Deterministic declarative calculation execution | < 100 ms | No model call involved at all |

Do not promise a flat "every query completes in a few seconds" — that's the mistake the original single-number target made. The honest, buildable promise is: **the user sees something within half a second, and the system does deeper validation in parallel**, with the actual answer arriving on the schedule above depending on whether a recipe already exists.

Record p50/p95/p99 separately for: preamble emission, planning, retrieval, recipe derivation/reuse, the critic (pre-screen and LLM portions separately), calculation execution, answer generation, and guardrail processing — not just one end-to-end number, since that's what makes it possible to tell whether a slow response was a slow derivation, a slow critic escalation, or something else entirely.

---
---

## 10. Security & compliance

**Confirmed: limited pilot rollout (open question 1).** This keeps Front Door/WAF, private endpoints, and column-level encryption in Phase 2 as originally planned — nothing needs to be pulled forward. "Limited" is a *scope* decision, not a technical sign-up gate: sign-up is open (§8), so anyone can create an account, but only pre-seeded, email-matched employees get personalized/calculated answers. Basic rate limiting (§8) matters somewhat more given open sign-up than it would under a gated pilot, and is called out explicitly in Phase 4's launch checklist for that reason.

### MVP baseline (non-negotiable even at 2 weeks)
- Entra External ID authentication (new CIAM tenant), open self-service sign-up with email-matched linking (§8); general policy Q&A can remain unauthenticated
- Secrets in Key Vault; TLS everywhere
- Row-level security on `employees` and `sessions` (§7.1)
- Full request/response logging to Log Analytics, correlated by `session_id`/`message_id` (§8)
- Visible disclaimer that answers are informational and final determinations go through the pension board/HR
- Guardrail: no citation without verification; no calculated value without validated recipe/engine execution; legal, user-provided, and employee-record values require their own provenance (§6, §9)
- Basic per-session rate limiting (§8)

### Phase 2 hardening
- Private endpoints/VNet integration for Postgres and Azure AI Search
- Azure Front Door + WAF, column-level encryption for the most sensitive employee fields
- Formal accessibility (WCAG) pass, StateRAMP/Azure Government evaluation if this grows beyond a pilot

---

## 11. Recommended resource group

| # | Resource | Tier / SKU | Purpose | Est. monthly cost | MVP or Phase 2 |
|---|---|---|---|---|---|
| 1 | Resource Group | — | Container for everything | Free | MVP |
| 2 | Azure AI Foundry (model deployments) | Standard, Luna + optional Terra/Sol escalation deployments | Generation | Usage-based | MVP |
| 3 | Azure AI Search | Basic + semantic ranker | Hybrid retrieval | ~$75/mo + per-query ranker charge | MVP |
| 4 | Azure Container Apps | Consumption plan | Backend API (LangGraph + tools) | ~$10–30/mo at pilot traffic | MVP |
| 5 | Azure Static Web App | Free or Standard | Chat frontend | Free–$9/mo | MVP |
| 6 | Azure Database for PostgreSQL – Flexible Server | Burstable B1ms | Schema in §7, managed by Alembic | ~$12–20/mo | MVP |
| 7 | Azure Key Vault | Standard | Secrets | ~$1–3/mo | MVP |
| 8 | Microsoft Entra External ID | New CIAM tenant, open self-service sign-up | Auth | Free — MAU billing starts above 50,000 monthly active users, far beyond pilot scale | MVP |
| 9 | Azure Monitor / Log Analytics | Pay-as-you-go | Compliance audit trail | ~$5–15/mo | MVP |
| 10 | Azure Container Registry | Basic | Store backend image | ~$5/mo | MVP |
| 11 | Foundry Agent Service (Hosted Agent) | — | Managed tracing for the LangGraph graph | Usage-based | Phase 2 |
| 12 | Azure Front Door + WAF | — | Public hardening (deferred — pilot is limited, §10) | ~$35+/mo | Phase 2 |
| 13 | Azure Managed Redis / Cosmos DB | Basic | Session state at scale, cross-session memory | ~$15–40/mo | Phase 2 |
| 14 | Formula engine as its own Container App | Consumption plan | Independent deployment/review boundary | ~$5–15/mo | Phase 2, if governance requires it |
| 15 | Staging search index + review workflow | Second index/slot | Human legal review before promoting law updates | Marginal | Phase 2 |

**MVP baseline: roughly $110–150/month** at pilot traffic, before usage-based model and semantic-ranker charges.

---

## 12. Calculation knowledge coverage: on-demand discovery and progressive reuse

Full coverage of every calculation in the 2,000-page code is **not a prerequisite for the chatbot to be useful**. The full statutory corpus is available to RAG from the beginning; calculation knowledge grows from the questions employees actually ask.

The system therefore distinguishes between **legal coverage** and **calculation execution coverage**:

```text
Entire Pension Code
       │
       ▼
RAG coverage ─────────────────────────────► broad from day one
       │
       ▼
Calculation capability discovery
       │
       ├── existing reusable recipe ──────► validate + reuse
       │
       └── no recipe ─────────────────────► derive from evidence
                                                 │
                                                 ▼
                                          validate + persist
                                                 │
                                                 ▼
                                             execute
```

### Calculation knowledge states

| State | Meaning | Runtime behavior |
|---|---|---|
| `discovered` | Section/question appears calculation-relevant | No execution |
| `agent_derived` | Agent produced an evidence-linked structured recipe | Must pass independent completeness verification + deterministic validation before any execution |
| `validated` | Completeness + deterministic validation passed | Estimate only when risk/evidence gate permits; high-risk defaults to `no_execute` |
| `approved` | Human/legal/actuarial review completed | Eligible for authoritative execution when policy permits |
| `stale` | Source/effective-date fingerprint changed | Do not reuse; re-derive |
| `retired` | Superseded or intentionally disabled | Do not execute |

### MVP seed capabilities

The MVP should seed/validate a small set of common calculations for Article 11 and Article 12, matching the original validation slice:

1. General retirement annuity
2. Early retirement treatment/reduction where applicable
3. COLA/increase calculation where the required parameters are available
4. Refund/separation calculation where the scenario is in scope

These are **seed capabilities, not a claim that they cover every Article 11 path**.

### Novel calculations during the pilot

When an employee asks for something not already covered:

```text
Question
  ↓
Retrieve statute
  ↓
Agent derives calculation recipe
  ↓
Independent completeness check
  ↓
Deterministic validation
  ↓
Risk/execution gate
  ├── valid → persist + execute according to policy
  └── invalid/ambiguous → explain law + no number
```

That recipe is saved so the next similar question can reuse it instead of repeating the entire derivation process.

### Coverage is now demand-driven

The registry becomes the backlog automatically:

- **existing reusable recipe** → no new derivation;
- **stale recipe** → re-derivation candidate;
- **new agent-derived recipe** → new reusable knowledge;
- **repeated high-value novel recipe** → candidate for human promotion/approval;
- **unsupported complex calculation** → tracked as a gap without blocking general legal Q&A.

This avoids requiring complete manual formula encoding before launch while retaining a path toward broader calculation coverage over time.

---
---

## 13. Phase-wise implementation plan

### Agentic calculation scope risk (explicit 2-week constraint)

The revised architecture adds meaningful orchestration scope: the LangGraph agent must decompose mixed questions, find or derive reusable calculation recipes, validate them, execute multiple scenarios, and persist successful recipes. That is more complex than a single retrieve → extract → formula → answer path.

The MVP therefore keeps the dynamic calculation layer bounded:

- **On-demand derivation is in scope:** the agent can derive a new structured calculation recipe when no reusable recipe exists.
- **Recipe reuse is in scope:** similar future questions should reuse the stored recipe after source/applicability validation.
- **Deterministic execution is in scope:** calculations run through the DSL interpreter or controlled custom handlers, never through free-form LLM arithmetic.
- **Automatic persistence is in scope:** successful recipes are stored for reuse; this is knowledge caching, not automatic legal approval.
- **Authoritative promotion is not required for every recipe:** only high-risk/authoritative use cases need human promotion. Ordinary estimate execution follows the configured execution policy.
- **Deep autonomous dependency planning is not a launch requirement:** declared dependencies are resolved deterministically.
- **Concurrent execution is in scope, not deferred:** the §9.8 latency targets are not reachable under fully sequential execution — a mixed/scenario request run task-by-task, and independent retrieval/lookup calls run one-after-another within each task, both add real wall-clock time that has no correctness benefit. This is being built as part of the Phase 1 walking skeleton, not retrofitted later, because it isn't a small patch after the fact: whether a task can run concurrently is a property of how the graph's edges are structured, and restructuring a graph that was built assuming one task at a time is a rework of the graph itself, not an add-on. Concretely: (1) within a task, `retrieve_policy`, `fetch_employee_record`, and the registry-lookup half of `retrieve_or_derive_recipe` (§9.2) run via `asyncio.gather`; (2) across a request's `agent_tasks` (§9.1), any tasks whose `depends_on` is already satisfied dispatch concurrently rather than iterating one at a time. This is added engineering scope inside the two-week window, acknowledged and owned outside this document rather than resolved by relaxing the targets.
- **If the agentic layer threatens the schedule, preserve the recipe contract, the deterministic engine, and the concurrency model above first; simplify planning before removing provenance, execution boundaries, or concurrent dispatch.**

**Time budget inside Phase 1:** Days 3–4 validate parsing/indexing and the rule/recipe contracts; Days 5–6 implement one end-to-end dynamic derivation path, recipe persistence/reuse, mixed-question planning, multi-scenario execution, **and concurrent dispatch for independent tasks and independent retrieval/lookup calls, built into the graph from this pass rather than added afterward.**

**Concrete Day 5 trigger:** demonstrate:

```text
"What would my pension be at 58, 60, and 62?"
        ↓
retrieve legal evidence
        ↓
derive/reuse one calculation recipe
        ↓
run 3 deterministic scenarios
        ↓
persist the reusable recipe
        ↓
answer with provenance for all 3 results
```

If this path is not stable by the end of Day 5, use a simpler planner while retaining the same recipe/engine interfaces.

**Phase 0 — Foundation (Days 1–2)**
- Provision resource group: Postgres, Azure AI Search, Key Vault, Container Registry, Static Web App shell, Entra External ID tenant with an open self-service sign-up user flow; stub the email-match linking endpoint (§8)
- Backend service skeleton with SQLAlchemy models matching §7.1
- Initialize Alembic; migration #1 creates the full base schema plus RLS policies
- Build the synthetic-data seed generator (§7.4)
- CI skeleton: build/push image; migrations run as a distinct pre-deploy step
- **Deliverable:** deployable skeleton, health-check endpoint, one migration applied against real Postgres

**Phase 1 — Walking skeleton: full pipeline against Article 11 + Article 12 only (Days 3–6)**
- Real parser against actual ILGA text for these two Articles, populating the full §5.2 schema including `calculation_relevance`
- Index the resulting records into the real Azure AI Search index
- Run the synthetic-data generator for Phase 1 scope: ~10–20 employees across both Articles and both tiers, with boundary cases from §7.4
- Build the bounded LangGraph agent from §9, including tool planning, recipe lookup, novel recipe derivation, deterministic validation, dynamic calculation execution, mixed-question handling, recipe persistence, and basic what-if scenarios
- Seed/derive the first Article 11 or 12 retirement calculation capability and demonstrate the full `agent_derived → validated` lifecycle
- CLI/Postman harness is sufficient; frontend polish is not what's being validated
- **Deliverable:** a working deployed instance that can answer legal questions and at least one mixed/scenario calculation flow, with reusable calculation knowledge persisted

**Phase 2 — Benchmark & validate (Days 6–7, overlapping the end of Phase 1)**
- ~20–30 question eval set: Article-11-only, Article-12-only, adversarial cross-article questions, mixed legal+calculation questions, novel calculations, and multi-scenario questions
- Record: correct section in top-k, calculation-plan/source-section coverage, recipe validation success, recipe reuse correctness, tool-selection correctness, guardrail catch rate, and formula/result correctness against independently prepared expected values
- **Decision gate:** fix chunking/metadata/retrieval/planning/recipe validation if the bar isn't met before scaling

**Phase 3 — Scale ingestion to the full corpus (Days 7–9)**
- Apply the validated parser/chunker across the remaining Articles, `pension_articles.ingestion_status` moving `not_ingested → validating → ingested` per Article
- Spot-check parsing on a sample from every Article
- Automatically surface calculation-relevant sections using `calculation_relevance`; create candidate/discovered knowledge entries rather than requiring a manual spreadsheet
- Preserve the full legal corpus in RAG even when no calculation recipe exists
- Re-run a subset of Phase 2 questions plus smoke tests against 2–3 additional Articles
- Calculation execution coverage stays intentionally progressive; novel gaps remain discoverable and trackable through the registry

### Calculation knowledge and agent acceptance gates

Before pilot launch:

- The agent can retrieve the relevant statutory evidence for Article 11/12 test cases.
- A novel calculation question can produce a structured recipe or a safe limitation; it never falls through to free-form arithmetic.
- Every reusable recipe has source sections and an evidence fingerprint.
- Every newly derived recipe passes the mandatory independent completeness check before it can become `validated`.
- Every executable recipe passes deterministic schema/applicability/effective-date validation and the risk-based execution gate.
- Every calculated output is persisted with recipe/formula ID, version, knowledge status, risk tier, execution policy, applicable date, inputs, outputs, source sections, evidence fingerprint, and completeness-check metadata.
- A stale recipe cannot execute without re-derivation.
- No high-risk recipe can execute under `estimate` without satisfying the configured promotion/evidence policy.
- Any `estimate` result is explicitly labeled as an estimate, and the execution policy/status is visible in API response metadata.
- At least one model-routing test confirms that normal traffic stays on Luna while a deliberately complex case escalates to Terra/Sol according to deterministic signals.
- At least one mixed-question test combines retrieval + calculation in a single request.
- At least one scenario test executes the same recipe across multiple hypothetical inputs.
- At least one novel-calculation test demonstrates that the system can derive, validate, persist, and reuse a previously unseen recipe.
- At least one concurrency test confirms, via the per-stage p50/p95 breakdown (§9.8), that independent retrieval/lookup calls and independent `agent_tasks` actually overlap in wall-clock time rather than merely being structurally permitted to — a graph that *can* run concurrently but never measurably does hasn't actually earned the §9.8 targets.

**Phase 4 — Harden and launch prep (Days 9–10)**
- Wire Entra External ID sign-up/login (email-match linking confirmed end-to-end) to the real frontend; confirm RLS end-to-end; Key Vault for all secrets; full Log Analytics audit logging with correlation IDs (§8); confirm basic rate limiting is live
- Guardrail tuning based on Phase 2/3 findings
- Disclaimer copy, minimal frontend polish
- Pilot launch: link shared with the intended pilot group; sign-up itself is open

---
---

## 14. Open questions for the team

1. ~~Limited pilot or full public launch?~~ **Answered: limited pilot** (§10).
2. ~~Formula coverage scope beyond Article 11/12?~~ **Answered: the legal corpus is fully ingested progressively; calculation capabilities are discovered and expanded on demand rather than requiring full manual encoding upfront** (§12).
3. ~~Who signs off on formula correctness?~~ **Answered with a risk-based model:** human/legal/actuarial review is required for capabilities promoted to `authoritative`, but is not a prerequisite for every agent-derived estimate recipe (§7.2).
4. ~~Real or synthetic employee data?~~ **Answered: realistic synthetic data** (§7.4).
5. **Still open:** who tracks the General Assembly calendar so the ingestion pipeline is updated the moment — and not before — any pending public-safety Tier 1/DROP bill (§4) actually becomes law? No owner assigned yet; fine to revisit after the pilot is live, but it needs an owner before automated ingestion is built, since that would otherwise risk ingesting a bill as if it were law.
