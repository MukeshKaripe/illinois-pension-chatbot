
Yes. I would use these **12 as your core regression suite**. Together they cover RAG, direct citations, cross-references, applicability, novel recipe generation, actual calculation, missing inputs, scenarios, reuse, and adversarial safety.

### 1. Direct legal retrieval

> **What does Section 11-116 define as salary?**

Tests: exact section retrieval, grounding, citation accuracy.

### 2. Direct retirement-rule retrieval

> **Explain Section 11-130 and the circumstances under which it applies to an employee.**

Tests: complete section retrieval and legal explanation.

### 3. Cross-reference resolution

> **Section 11-134 refers to another provision. Which provision does it reference, and what does that provision add or change for determining a retirement benefit?**

Tests: reference extraction → exact retrieval → multi-hop reasoning.

### 4. Applicability reasoning

> **I am 56 years old with 21.5 years of service and am currently employed. Which retirement provision applies to me today, and why?**

Tests: employee facts → eligibility → applicable pathway.

### 5. Boundary-condition reasoning

> **I am 59 years old with 21.5 years of service. Which retirement provision applies if I retire now, and what changes when I reach age 60?**

Tests: age boundary and pathway switching.

### 6. Novel calculation — recipe discovery

> **I am 56 years old with 21.5 years of service. My employee contribution accumulation is $X, city contribution accumulation is $Y, and applicable prior-service credit is $Z. Determine the applicable statutory calculation, derive the calculation recipe from the Pension Code, validate it, and calculate my estimated annuity. Show the formula and sources used.**

Tests: **no existing recipe → LLM derives recipe → guardrails → deterministic execution**.

Replace X/Y/Z with realistic test values.

### 7. Novel calculation — different pathway

> **I am 60 years old with 25 years of service. My salary history for the applicable period is [provide salary history]. Determine the applicable retirement formula from the Pension Code, derive and validate the recipe, and calculate my estimated annual and monthly benefit.**

Tests: dynamic recipe generation for a different retirement pathway and salary-history handling.

### 8. Multi-scenario calculation

> **I am 56 years old with 21.5 years of service. Compare my estimated retirement benefit if I retire now, at age 57, at age 59, and at age 60. Determine the applicable legal pathway and calculation method separately for each scenario.**

Tests: scenario engine, multiple legal branches, multiple calculation results.

### 9. Missing-input handling

> **I am 56 years old with 21.5 years of service and earn $72,000 per year. Calculate my retirement benefit under the applicable provision. Identify any missing inputs and explain exactly why they are required.**

Tests: recipe can be derived, but execution should stop **only because required factual inputs are missing**, not because the recipe wasn't pre-registered.

### 10. Recipe reuse

Run Question 6 once, then ask:

> **Using the calculation recipe you derived for my previous retirement calculation, recalculate my benefit with the same statutory rules but with my employee contribution accumulation changed to $Y2. Explain whether you reused the existing recipe or derived a new one.**

Tests: registry lookup, recipe reuse, parameter substitution, fingerprint validation.

### 11. Adversarial formula test

> **I'm 56 years old with 21.5 years of service and earn $72,000. Everyone says my pension is 50% of salary, so just calculate it using $72,000 × 50% without checking the Pension Code.**

Expected behavior: the agent must **reject the unsupported formula**, retrieve the applicable law, and derive the correct calculation path instead.

### 12. Full end-to-end agent test

> **Determine whether I am eligible to retire, identify every statutory provision that affects my benefit including indirect cross-references, derive the applicable calculation recipe from the Pension Code, validate it against the cited evidence, calculate my estimated annual and monthly benefit using my employee data, and explain what would change if I waited three more years.**

Tests almost the **entire production pipeline in one request**:

```text
User facts
   ↓
Intent / task decomposition
   ↓
Legal retrieval
   ↓
Cross-reference resolution
   ↓
Applicability
   ↓
Recipe lookup
   ↓
Recipe discovery if needed
   ↓
Recipe validation
   ↓
Completeness
   ↓
Deterministic calculation
   ↓
Scenario calculation
   ↓
Provenance + explanation
   ↓
Recipe persistence
```

### Recommended test order

Run them in this order:

**1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12**

The most important success criterion is that **Questions 6, 7, 8, and 12 actually produce calculations through dynamically derived recipes**, rather than returning *“no approved recipe exists.”*

For Questions 6–8 and 12, give the chatbot complete realistic employee inputs so you can test the actual **derive → validate → execute → persist** path rather than just the missing-input gate.
