
Yes. After reading the current `CLAUDE.md`, I think your feedback is pointing at a **fundamental architectural issue**, not a collection of small bugs.

The system has become much better engineered in terms of **correctness, provenance, deterministic calculation, coverage safety, and guardrails**. But it is still architected primarily as a **pipeline that happens to use an LLM**, whereas what you actually want is an **LLM-driven conversational agent that has deterministic capabilities available to it**.

That distinction is the key.

The current design explicitly says:

> “The LLM agent plans, retrieves, and writes prose”

but the actual graph is still essentially:

`understand → coverage → retrieve → recipe → inputs → validate → calculate → answer`

and that explains almost every symptom you're seeing.

---

# 1. The biggest problem: the agent is a pipeline, not an agent

Your desired behavior is closer to:

```text
                 ┌──────────────────────┐
                 │   User's message     │
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │ Understand intent    │
                 │ + conversation       │
                 │ + context            │
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │ What would actually  │
                 │ help answer this?    │
                 └──────────┬───────────┘
                            ↓
                ┌───────────┴───────────┐
                ↓                       ↓
         Need information?        Need calculation?
                ↓                       ↓
          Retrieve law             Find recipe
                ↓                       ↓
         Evaluate evidence        Existing recipe?
                ↓                  /          \
         Need more retrieval?    yes           no
          /          \             ↓            ↓
        yes           no        use it      derive recipe
         ↓             ↓                       ↓
      search again   continue              validate
                                              ↓
                                           persist
                                              ↓
                                         execute
                └────────────┬───────────────┘
                             ↓
                  ┌─────────────────────┐
                  │ Do I have enough to │
                  │ answer helpfully?   │
                  └──────────┬──────────┘
                             ↓
                         Answer
```

And importantly, **the arrows are not fixed**.

The model should be able to decide:

> "I know enough. Answer directly."

or:

> "I need the member's service years."

or:

> "The user didn't explicitly say their age, but their profile says 56 and 'two years from now' clearly means 58."

or:

> "The first retrieval wasn't relevant. I understand what they're asking now, so I'll search using a better representation."

or:

> "This is a hypothetical Article 4 question. I don't need to force the user's actual Article 11 profile into this."

That is what is currently missing.

---

# 2. Deterministic ≠ deterministic conversation

I think this is the most important thing to communicate to Claude.

The existing architecture correctly established:

> **LLM plans, code calculates.**

Keep that.

But it seems to have evolved into something closer to:

> **Code decides the conversation; LLM fills in the gaps.**

That's the part I would change.

You **do not** want to remove the deterministic components.

You want to change their role.

### Current mental model

```text
LLM
 ↓
fixed pipeline
 ↓
deterministic services
 ↓
answer
```

### Desired mental model

```text
                    LLM Agent
                       │
       ┌───────────────┼────────────────┐
       │               │                │
    Retrieval       Calculator       Context
       │               │                │
       ↓               ↓                ↓
   Azure Search    Recipe Registry   User Profile
                   DSL Interpreter   Conversation
                   Validators        Assumptions
```

The deterministic pieces become **tools/capabilities available to the agent**, rather than mandatory stages every question must pass through.

This aligns directly with your point #4: tools should be used **when needed**, not always.

---

# 3. Your current `understand_turn` is still too much of a gate

The CLAUDE file says the model decides:

> whether a figure is wanted, benefit, revised values, scenarios, etc.

That's a good improvement over regex.

But there is still a conceptual problem:

### You're asking the model to classify the conversation before the conversation has actually been reasoned through.

A smarter architecture should not primarily ask:

```text
"What type of turn is this?"
```

It should ask:

```text
"What is the user trying to accomplish,
and what do I need to do to satisfy that?"
```

Those are very different questions.

For example:

> "If I work another two years, what would my pension look like?"

Current thinking:

```text
figure wanted = yes
retirement age = current age + 2
benefit = pension
scenario = ...
→ calculation pipeline
```

Better agent reasoning:

```text
User wants to understand the financial impact
of working two additional years.

I have:
- current member context
- current age
- service
- salary history
- applicable Article

I need:
- retirement age = current age + 2

I already have the applicable formula.

Therefore:
→ calculate
→ explain the difference from current scenario
```

The second is **goal-oriented**, not classification-oriented.

---

# 4. Introduce an explicit "Agent Decision / Next Action" layer

This is probably the biggest architectural addition I would recommend.

Not a giant deterministic "scenario engine."

Instead:

## `AgentOrchestrator`

Its job is to repeatedly answer:

> **"Given everything I currently know, what should I do next to answer the user?"**

Possible actions:

```text
ANSWER
ASK_CLARIFICATION
RETRIEVE
RETRIEVE_REPHRASED
LOOKUP_RECIPE
DERIVE_RECIPE
RESOLVE_INPUTS
CALCULATE
COMPARE
USE_PROFILE
USE_CONVERSATION_CONTEXT
HANDLE_HYPOTHETICAL
```

Potentially:

```text
SEARCH_COVERAGE
SEARCH_CROSS_CUTTING_LAW
VERIFY_EVIDENCE
```

But don't make these hardcoded stages.

The agent chooses among them.

### Example

User:

> "How much would I get if I retired at 60?"

Agent state:

```text
intent:
  calculate retirement benefit

known:
  age = 57
  service = 24
  salary history = available
  article = 11
  retirement age = 60

needed:
  formula
```

Agent:

```text
LOOKUP_RECIPE
```

Recipe exists:

```text
CALCULATE
```

Done.

---

Another:

> "What happens to my pension if I work until 62?"

Agent realizes:

```text
This is a scenario comparison.

I already know:
age = 57
current service = 24
salary history = ...
existing retirement scenario = ...

Need:
service at 62
salary assumption / existing projection rules

Retrieve/derive only what is actually necessary.
```

It doesn't need to blindly execute every graph node.

---

# 5. Retrieval needs to become an iterative reasoning loop

Your second point is absolutely correct.

Currently:

```text
query
 ↓
retrieve
 ↓
chunks aren't useful
 ↓
"I don't have enough information"
```

That isn't good enough for an intelligent legal assistant.

The agent should have:

```text
retrieve
 ↓
inspect results
 ↓
reassess what the user actually needs
 ↓
generate better search representation
 ↓
retrieve again
 ↓
inspect
 ↓
repeat if necessary
```

For example:

User:

> "Can I retire early without losing too much?"

First query:

```text
early retirement pension reduction
```

Maybe poor results.

The agent understands that "losing too much" probably means:

* early retirement eligibility
* reduction factor
* age threshold
* years before normal retirement
* possibly Article-specific reduction

So it can reformulate:

```text
Article 11 early retirement reduction age eligibility
```

Then perhaps:

```text
Article 11 11-167 early retirement annuity reduction
```

Then perhaps retrieve the referenced section.

### Give retrieval a budget

You don't want infinite agent loops.

For example:

```text
max retrieval iterations = 3
```

But **3 is a safety limit, not a prescribed number of searches**.

The agent might stop after one.

Or use all three.

This is exactly how an intelligent system should use deterministic retrieval infrastructure.

---

# 6. Retrieval should be based on the agent's evolving understanding

This is a subtle but important improvement.

Don't merely rewrite the user's sentence.

Create a **search plan**.

For example:

User:

> "What happens if I leave next year?"

The search representation might become:

```json
{
  "concepts": [
    "termination of service",
    "retirement eligibility",
    "deferred benefit",
    "vesting",
    "refund",
    "annuity eligibility"
  ],
  "member_context": {
    "article": 11,
    "age": 57,
    "service": 24
  },
  "cross_references": true
}
```

Then generate several targeted retrieval queries.

That is much more powerful than:

```text
rewrite("What happens if I leave next year?")
```

The agent should reason about **what legal concepts must be answered**.

---

# 7. The conversation state needs to become much richer

Your current checkpoint is useful, but it appears heavily focused on operational state:

* scenarios
* pinned record
* inputs
* coverage
* recipes
* calculation card

What you're missing is a proper **conversation memory/state model**.

Think of it as:

```text
ConversationState
│
├── user_goal
│
├── current_question
│
├── known_facts
│
├── user_provided_facts
│
├── profile_facts
│
├── inferred_facts
│
├── assumptions
│
├── corrections
│
├── active_hypothetical
│
├── active_scenario
│
├── previously_answered
│
├── unresolved_questions
│
├── relevant_articles
│
├── retrieved_evidence
│
├── candidate_recipes
│
└── calculation_results
```

This makes conversations much more natural.

For example:

**User:**

> I'm 56 and have 25 years of service.

Agent records:

```text
age = 56
source = user
confidence = explicit
```

Then:

> What if I wait three years?

Agent doesn't ask:

> "What is your age?"

It knows:

```text
age = 56
retirement_age = 59
```

Then:

> What if I do another five?

It knows the conversation.

Then:

> Actually, make that four.

It updates the relevant scenario.

That's conversational intelligence.

---

# 8. Your employee-record caching issue is especially interesting

There is actually an inconsistency in the current `CLAUDE.md`.

The graph says:

```text
understand_turn ∥ fetch_employee_record
```

on the turn flow.

But later it says:

> “a conversation keeps the record it loaded on its first turn (`pinned_record`) ... later turns skip a database round trip.”

So I would tell Claude to **inspect the actual implementation rather than assuming this is already solved**.

Conceptually, the desired behavior is:

```text
First turn
   ↓
load profile
   ↓
normalize into ConversationState
   ↓
pin snapshot
   ↓
subsequent turns
   ↓
use state
```

Not:

```text
every turn
 ↓
fetch DB
 ↓
fetch DB
 ↓
fetch DB
```

And this is actually better than a generic cache.

### Don't make Redis your first answer.

The conversation checkpoint itself can act as the cache.

For example:

```json
{
  "member_context": {
    "employee_id": "...",
    "record_version": 7,
    "article": 11,
    "age": 56,
    "service": 25,
    "salary_history": [...]
  }
}
```

Then only refresh if:

* new conversation
* profile changed
* explicit refresh
* system requires current data

That gives you consistency too.

---

# 9. Tool selection should be intelligent

This is another major distinction.

Don't do:

```text
Every request:
    fetch employee
    assess coverage
    retrieve policy
    resolve recipes
    ...
```

Instead:

```text
Agent asks:
"What information do I need?"
```

### Simple question

> "What is Article 11?"

Should probably be:

```text
retrieve → answer
```

Not:

```text
fetch member
coverage
resolve recipes
inputs
...
```

### Conversational question

> "Does my wife get anything if I die?"

Maybe:

```text
use member context
retrieve survivor/death provisions
answer
```

No calculation.

### Calculation

> "How much would I get at 60?"

```text
member context
→ retrieve/lookup recipe
→ resolve inputs
→ calculate
→ answer
```

### Hypothetical

> "What if I were a Chicago police officer?"

```text
detect hypothetical premise
→ switch reasoning context
→ retrieve relevant Article
→ answer conditionally
```

The actual employee record shouldn't contaminate it.

---

# 10. Hypothetical questions need a first-class concept

Your point #8 is extremely important.

The agent shouldn't think:

> "This person is Article 11, therefore everything must be Article 11."

That's a **context error**.

There should be a distinction between:

```text
MEMBER_CONTEXT
```

and

```text
QUESTION_CONTEXT
```

For example:

```text
Member context:
    Article 11

Question context:
    "If I were an Article 4 police employee..."
```

Then:

```text
question_context.article = 4
question_context.is_hypothetical = true
```

The answer:

> "If you were covered by Article 4, then..."

And critically, the agent should be able to continue that hypothetical across turns.

User:

> "How much would I get?"

The agent understands:

> "How much would I get under that Article 4 hypothetical?"

rather than snapping back to Article 11.

---

# 11. Don't confuse "follow the user's flow" with "trust everything the user says"

This is where your existing safety architecture is valuable.

You don't need to weaken it.

Instead:

### User says:

> "I am a police officer."

That's a factual claim.

Need verification.

### User says:

> "If I were a police officer..."

That's hypothetical.

Don't challenge it.

### User says:

> "Assume I'm 60 and have 30 years of service."

That's an explicit scenario.

Use it.

### User says:

> "I think I'm covered under Article 4."

That's uncertainty.

Clarification may be appropriate.

So the agent needs **epistemic distinctions**, not just hard gates:

```text
FACT
USER_ASSERTED
PROFILE_FACT
ASSUMPTION
HYPOTHETICAL
INFERRED
UNCERTAIN
CORRECTED
```

That will make it much more natural without sacrificing legal correctness.

---

# 12. Your recipe architecture should become a real "knowledge compiler"

Your point #7 is, in my opinion, one of the most important pieces.

The intended architecture is actually very good:

```text
Statute
   ↓
LLM reasoning
   ↓
Recipe
   ↓
Validation
   ↓
Registry
   ↓
Deterministic execution
```

The problem is that the agent needs to treat the recipe registry as **persistent calculation knowledge**, not merely a lookup table.

The desired behavior:

```text
User asks calculation
        ↓
Identify canonical calculation concept
        ↓
Search formula_registry
        ↓
Recipe found?
   ┌────┴────┐
  YES       NO
   ↓         ↓
validate   retrieve statute
   ↓         ↓
execute    derive recipe
             ↓
          validate
             ↓
          persist
             ↓
          execute
```

Exactly as you described.

---

# 13. But recipe lookup needs semantic identity

You don't want:

```text
"What is my pension?"
```

and

```text
"How much annuity do I receive?"
```

to be treated as different formulas.

The registry needs a canonical identity such as:

```text
article
benefit_type
calculation_type
eligibility_conditions
inputs
effective_date
dependencies
```

For example:

```json
{
  "article": "11",
  "benefit": "retirement_annuity",
  "calculation": "standard_annuity",
  "tier": 1
}
```

Then the user can phrase the same intent 50 different ways.

The agent finds the same recipe.

---

# 14. Recipe derivation needs versioning and evidence

I would strongly recommend that Claude not simply "save whatever the LLM generated."

A derived recipe should look conceptually like:

```text
Recipe
├── canonical identity
├── rule specification
├── source sections
├── source excerpts / evidence references
├── assumptions
├── applicable Article
├── tier
├── required inputs
├── dependencies
├── risk tier
├── knowledge status
├── execution policy
├── created_at
├── derived_by
└── version
```

Your existing lifecycle is already moving in this direction:

`discovered → agent_derived → validated → approved → stale/retired`.

That's good.

The missing part is making this lifecycle an actual **agent behavior**:

> "I don't have the knowledge → derive it → validate it → persist it → use it."

rather than:

> "No recipe → continue pipeline."

---

# 15. Recipe derivation should be allowed to fail intelligently

Suppose the agent finds:

```text
§11-134
§11-136
§11-167
```

and realizes the calculation requires three rules.

It should be able to reason:

```text
This calculation depends on:
A → eligibility
B → accrual
C → reduction

Retrieve B.
Retrieve C.
Combine them.
Construct recipe.
Validate.
```

That's much closer to how a human expert would research the Code.

---

# 16. The agent should distinguish "I don't know" from "I haven't searched enough"

This is probably another hidden source of the current behavior.

There are at least four states:

```text
1. I know the answer.
2. I have evidence but need to reason over it.
3. I don't currently have enough evidence and should search more.
4. The Code genuinely doesn't establish the answer.
```

Your current system seems too eager to move from:

```text
3
```

to:

```text
4
```

That is exactly why you get:

> "I don't have enough information."

too early.

The agent should only say that after exhausting reasonable retrieval/reasoning paths.

---

# 17. Clarifications should be a last resort, not a workflow step

Your existing clarification budget of 3 is technically sensible, but I wouldn't make "clarification budget" the main conversational mechanism.

Think in terms of:

### Missing information classification

```text
Can I safely infer it?
        ↓
      YES → infer + disclose

Can I use profile data?
        ↓
      YES → use it

Can I derive it from conversation?
        ↓
      YES → derive

Can I answer conditionally?
        ↓
      YES → answer with assumption

Can I retrieve it?
        ↓
      YES → retrieve

Only then:
        ↓
ASK
```

This produces much better conversations.

---

# 18. Example of the difference

### Current bot behavior

User:

> "If I work two more years, how much will I get?"

Bot:

> What will your salary be?

User:

> Probably around $90k.

Bot:

> What is your current age?

User:

Bot:

> What is your current service?

User:

> 25 years.

This feels like a form.

---

### Intelligent assistant

User:

> If I work two more years, how much will I get?

Assistant internally:

```text
Age = 56 from profile
Service = 25 from profile
Two years = age 58, service 27
Salary projection = unknown
```

It may know a reasonable projection from existing salary history.

Then:

> Assuming your salary continues at roughly your recent level, retiring at 58 would give you approximately X. I used 27 years of service and projected your salary from your existing history. If your salary changes materially, the figure will change.

That is exactly the behavior you're asking for.

---

# 19. Mixed information + calculation queries should be completely normal

You previously raised this issue as well.

A user might say:

> "I'm thinking of retiring at 58. Am I eligible and how much would I get?"

That's not:

```text
information OR calculation
```

It's:

```text
information
+
eligibility determination
+
calculation
+
explanation
```

The agent should decompose the goal naturally:

```text
1. Determine applicable retirement rule.
2. Determine eligibility at 58.
3. If eligible:
      calculate.
4. Explain both.
```

This is another reason why a fixed `figure_wanted` flag isn't enough.

You need **goal decomposition**.

---

# 20. Think "task graph", not "agent graph"

This is an important terminology change I'd suggest to Claude.

Instead of:

```text
fixed LangGraph:
A → B → C → D
```

use LangGraph to support:

```text
                  User Goal
                      ↓
                Agent Planner
                      ↓
              Current Knowledge
                      ↓
            ┌─────────┴─────────┐
            ↓                   ↓
       Next action          Need more reasoning?
            ↓                   ↓
          Tool              loop back
            ↓
        Tool result
            ↓
        update state
            ↓
        next action
```

LangGraph is actually well suited to this because it supports stateful cycles.

The key is to **use the graph for controlled agentic loops rather than encode the entire reasoning process as a predetermined DAG.**

---

# 21. A better high-level architecture

I'd propose something like this:

```text
                         ┌─────────────────────┐
                         │       USER          │
                         └──────────┬──────────┘
                                    ↓
                         ┌─────────────────────┐
                         │ Conversation Agent  │
                         │                     │
                         │ Understand goal     │
                         │ Use context         │
                         │ Plan next action    │
                         │ Decide when done    │
                         └──────────┬──────────┘
                                    │
                         ┌──────────┴──────────┐
                         │                     │
                         ↓                     ↓
                  Context Manager       Evidence Manager
                         │                     │
              ┌──────────┼───────┐      ┌─────┼─────────┐
              ↓          ↓       ↓      ↓     ↓         ↓
           Profile   Conversation Scenario Search  Cross refs
                                    │
                                    ↓
                              Evidence Store
                                    │
                                    ↓
                           ┌────────────────┐
                           │ Recipe Manager │
                           └───────┬────────┘
                                   │
                         ┌─────────┴─────────┐
                         ↓                   ↓
                  Existing recipe       Derive recipe
                         │                   │
                         └─────────┬─────────┘
                                   ↓
                              Validator
                                   ↓
                              Calculator
                                   ↓
                             Result + Provenance
                                   ↓
                            Answer Composer
                                   ↓
                           Guardrail / Verify
                                   ↓
                                  USER
```

And the agent can loop:

```text
Agent
 ↓
Retrieve
 ↓
Agent
 ↓
Retrieve
 ↓
Agent
 ↓
Recipe
 ↓
Agent
 ↓
Calculate
 ↓
Agent
 ↓
Answer
```

---

# 22. One thing I would NOT change

Do **not** take your desire for ChatGPT-like intelligence to mean:

> "Let the LLM do everything."

That would be a mistake for a pension/legal system.

Keep these deterministic:

### Deterministic

* arithmetic
* DSL execution
* input validation
* provenance
* citation validation
* security
* RLS
* authorization
* hard safety boundaries
* recipe structural validation
* evidence traceability

### LLM-driven

* understanding
* intent
* goal decomposition
* deciding whether tools are needed
* deciding which tools
* interpreting conversational context
* query reformulation
* retrieval strategy
* reasoning over retrieved law
* identifying missing knowledge
* deciding whether an assumption is reasonable
* deciding whether to ask
* deciding whether something is hypothetical
* deciding what the user probably means
* deciding when the answer is complete
* composing the answer

That separation gives you both **intelligence and reliability**.

---

# 23. Your existing coverage system needs to become contextual rather than dominant

Your coverage work is actually sophisticated.

The distinction between:

* asserted
* stipulated
* doubted

is exactly the kind of thing the agent needs.

But coverage should answer:

> **"What legal context applies to this particular question?"**

not:

> **"What Article is this user?"**

Those aren't always the same.

For example:

```text
Member Article = 11

Question:
"What happens under Article 12?"

→ answer Article 12

Question:
"If I were an Article 12 member?"

→ hypothetical Article 12

Question:
"What applies to me?"

→ member Article 11

Question:
"What happens to my reciprocal service under Article 20?"

→ Article 11 + Article 20
```

That's a much more flexible mental model.

---

# 24. The agent needs "context scopes"

I would explicitly introduce this concept.

For example:

```text
GLOBAL_CONTEXT
    Illinois Pension Code

MEMBER_CONTEXT
    actual employee/profile

QUESTION_CONTEXT
    what the current question is about

SCENARIO_CONTEXT
    hypothetical or future scenario

EVIDENCE_CONTEXT
    statutes retrieved for this task

CALCULATION_CONTEXT
    inputs + recipe currently being executed
```

Then the agent can switch context without losing the user's actual identity.

This solves a lot of your Article 4/11/12 hypothetical problem elegantly.

---

# 25. "Helpful next step" should be implicit, not annoying

Your point #5 is also important.

You don't want the assistant constantly saying:

> Would you like me to calculate that?

Instead, it should recognize obvious continuations.

User:

> "I can retire at 60, right?"

Answer:

> "Yes. Under your current profile, you meet the eligibility requirement at 60. Based on your current service, that would give you approximately X."

If the calculation is obvious and safe, **do it**.

User:

> "What happens if I wait until 62?"

Answer:

> "At 62, you'd have two additional years of service. Using the same salary assumption, that would change the estimate from X to Y."

That's what "helpful" means here.

Not endlessly asking permission to help.

---

# 26. There should be an "answer sufficiency" decision

Before asking a question, the agent should effectively ask itself:

```text
Can I provide a useful answer right now?
```

Possible outcomes:

### Exact answer

```text
Yes → answer
```

### Useful answer with assumption

```text
Yes → answer + state assumption
```

### Conditional answer

```text
Yes → "If X, then Y"
```

### Partial answer

```text
Yes → explain known portion + missing part
```

### Clarification required

```text
No → ask one focused question
```

That should be a core agent policy.

---

# 27. I'd also add an "assumption ledger"

This would be extremely useful.

Example:

```json
{
  "assumptions": [
    {
      "field": "retirement_age",
      "value": 58,
      "basis": "user said 'two more years'",
      "confidence": "high"
    },
    {
      "field": "future_salary",
      "value": 90000,
      "basis": "recent salary trend",
      "confidence": "medium"
    }
  ]
}
```

Then the final response can naturally say:

> "I assumed your salary remains around your recent level."

And if the user says:

> "Actually I'll be making $105k."

the agent updates that assumption and recalculates.

This is much more conversational than repeatedly collecting fields.

---

# 28. I would explicitly tell Claude to audit for "rigidity"

Don't ask Claude merely to "make it smarter."

That's too vague.

Ask it to perform a **rigidity audit**.

For every node ask:

```text
1. Does this node HAVE to run?
2. Can the agent decide whether to invoke it?
3. Does this node make a semantic decision?
4. Should that decision belong to the LLM?
5. Does this node unnecessarily ask the user for information?
6. Does this node unnecessarily constrain hypothetical questions?
7. Does failure here terminate the entire attempt prematurely?
8. Can the agent retry/reason/retrieve?
9. Is this a capability or a mandatory pipeline stage?
```

That will produce much better architectural changes.

---

# 29. I would give Claude this exact brief

Here is how I would articulate the entire requirement to Claude Code.

# Goal: Evolve the Pension Assistant from a rigid pipeline into an intelligent conversational agent

The current system has strong deterministic foundations: legal retrieval, provenance, coverage safety, recipe validation, deterministic calculation, guardrails, RLS, and persistent formula knowledge.

Do NOT remove those safeguards.

The problem is that the current architecture still behaves like a predetermined pipeline:

understand → coverage → retrieve → recipe → inputs → validate → calculate → answer

The desired product is fundamentally different:

> An intelligent conversational assistant similar in behavior to ChatGPT/Claude, but specialized in the Illinois Pension Code.

The assistant should understand what the user is trying to accomplish, decide what information or tools it needs, reason over the available evidence, retrieve additional evidence when necessary, make safe assumptions when possible, ask questions only when genuinely necessary, use deterministic capabilities where authoritative behavior is required, and continue until it has the best possible answer.

The LLM should control reasoning and task progression.
Deterministic code should control authoritative operations.

---

## 1. Preserve the core invariant

Keep:

> LLM plans/reasons; deterministic code calculates.

The LLM must never independently produce authoritative pension arithmetic.

However, do NOT interpret this as:

> deterministic code decides the entire conversation.

Instead:

> The LLM decides when and how deterministic capabilities are used.

Deterministic components should behave as capabilities/tools available to the agent.

---

## 2. Replace the fixed pipeline mindset

Audit the current LangGraph and identify where the graph forces every request through stages that are not actually necessary.

For every node, determine:

* Does this node need to run for every question?
* Can the LLM decide whether it is needed?
* Is this node a capability or a mandatory stage?
* Can the agent skip it?
* Can the agent revisit it?
* Can failure cause the agent to retry with a different strategy?

The target should be an agentic loop rather than a fixed DAG.

Conceptually:

User
→ understand goal/context
→ decide next action
→ use capability
→ inspect result
→ update state
→ decide next action
→ repeat until sufficient
→ answer

The graph should support cycles.

---

## 3. Introduce an explicit agent decision layer

Create a conceptual orchestration layer whose responsibility is:

> Given the user's goal and everything currently known, what should the agent do next?

Potential actions include:

* ANSWER
* RETRIEVE
* RETRIEVE_REPHRASED
* LOOKUP_RECIPE
* DERIVE_RECIPE
* RESOLVE_INPUTS
* CALCULATE
* COMPARE
* USE_PROFILE
* USE_CONVERSATION_CONTEXT
* HANDLE_HYPOTHETICAL
* ASK_CLARIFICATION

This is not intended to become another giant deterministic scenario engine.

The LLM should choose the appropriate next action, while deterministic tools execute the requested operation safely.

---

## 4. Optimize for user goal, not intent classification

Do not make the primary task:

> "Classify this turn."

Make the primary task:

> "Understand what the user wants and determine how to satisfy it."

The agent should be able to decompose a request naturally.

Example:

"If I retire at 58, am I eligible and how much would I receive?"

This contains:

1. eligibility determination
2. calculation
3. explanation

It should not be forced into either "information" or "calculation."

---

## 5. Make conversation state a first-class knowledge layer

The conversation state should retain:

* current user goal
* current question
* known facts
* profile facts
* user-provided facts
* inferred facts
* assumptions
* corrections
* active scenarios
* active hypothetical context
* relevant Articles
* retrieved evidence
* candidate recipes
* unresolved information
* prior calculation results

Each fact should ideally retain provenance such as:

* profile
* user explicitly stated
* inferred
* assumed
* retrieved from statute
* calculated

The agent should use this state before asking the user for information again.

---

## 6. Cache/pin member context

Investigate the current `pinned_record` behavior carefully.

The desired behavior is:

First relevant turn:
→ load employee/profile
→ normalize required information
→ pin it into conversation state

Subsequent turns:
→ use the pinned context
→ do not fetch the database again unless necessary

Refresh only when appropriate:

* new conversation
* profile changed
* explicit refresh
* data freshness requirement

Do not make employee retrieval a mandatory per-turn tool.

---

## 7. Tools must be invoked only when necessary

Examples:

"What is Article 11?"
→ retrieve statute
→ answer

"Does my wife receive anything if I die?"
→ retrieve survivor/death provisions
→ answer

"How much would I receive at 60?"
→ use member context
→ locate/derive recipe
→ resolve inputs
→ deterministic calculation
→ answer

"Tell me about Article 4."
→ retrieve Article 4
→ do not force member's Article 11 context into the question

Avoid running every available node for every message.

---

## 8. Retrieval must be iterative

A failed first retrieval must NOT immediately produce:

"I don't have enough information."

Instead:

retrieve
→ inspect relevance
→ understand what is missing
→ reformulate search strategy
→ retrieve again
→ inspect
→ follow cross-references
→ repeat within a bounded budget

The agent should be able to generate better search representations based on its understanding of the question.

Do not merely rewrite the user's sentence.

For example:

"What happens if I leave next year?"

may require retrieval around:

* termination of service
* vesting
* deferred benefits
* refund
* retirement eligibility
* applicable Article

The retrieval subsystem should support semantic query expansion and targeted searches.

Set a bounded retrieval budget, but treat the budget as a safety limit rather than a fixed workflow.

---

## 9. Distinguish "not found" from "not enough search"

The agent must distinguish:

1. I know the answer.
2. I have evidence and need to reason over it.
3. I have not found sufficient evidence yet and should search more.
4. The available law genuinely does not establish the answer.

Do not jump from (3) directly to (4).

---

## 10. Clarification should be the last resort

Before asking the user for information, reason in this order:

1. Is it already in conversation state?
2. Is it available in the member profile?
3. Can it be derived from something the user said?
4. Can it be safely inferred?
5. Can the question be answered conditionally?
6. Can a reasonable assumption be made and disclosed?
7. Can the missing information be retrieved from the Code?
8. Only then ask the user.

For example:

If the user says "wait two more years" and their current age is known, derive the new age rather than asking for it.

If a future salary is unknown but an estimate is useful, use a defensible assumption and disclose it.

Do not turn the assistant into a form.

---

## 11. Add explicit assumption handling

Maintain assumptions in conversation state.

Each assumption should have:

* field
* value
* basis
* confidence
* whether it affects authoritative calculation
* whether user confirmation is required

The assistant should be able to say:

"Assuming your salary remains around your recent level..."

rather than blocking the conversation.

If the user later corrects the assumption, update state and recalculate.

---

## 12. Treat hypothetical questions as first-class

Separate:

MEMBER_CONTEXT

from:

QUESTION_CONTEXT

and:

SCENARIO_CONTEXT

A member may belong to Article 11 while asking:

"If I were a police employee under Article 4, how would this work?"

The answer should follow the user's hypothetical premise:

"If you were covered by Article 4..."

Do not force the user's actual Article 11 record into the hypothetical.

The hypothetical context should persist across turns when appropriate.

Example:

User:
"If I were under Article 4..."

Assistant:
answers Article 4 conditionally.

User:
"How much would I get?"

Assistant:
understands that this refers to the active Article 4 hypothetical unless the user changes context.

---

## 13. Preserve the asserted/stipulated/doubted distinction

The existing distinction is valuable:

* "I am a police employee" → asserted fact; verify
* "If I were a police employee" → stipulated hypothetical; accept premise
* "I think I'm a police employee" → uncertainty; clarify/verify

Do not remove this safety behavior.

Generalize it into a richer epistemic model:

* PROFILE_FACT
* USER_ASSERTED
* USER_STIPULATED
* HYPOTHETICAL
* INFERRED
* ASSUMED
* UNCERTAIN
* CORRECTED
* STATUTORY_FACT

---

## 14. Make the formula registry persistent calculation knowledge

For every calculation request:

1. Identify the canonical calculation concept.
2. Search the formula registry.
3. If a valid applicable recipe exists, reuse it.
4. If no recipe exists:

   * retrieve the necessary statute
   * reason over the relevant provisions
   * derive a recipe
   * validate the recipe structurally and against evidence
   * persist the recipe
   * execute it if execution policy permits
5. Future equivalent questions should reuse the persisted recipe.

The LLM should not reinvent the same calculation on every turn.

---

## 15. Give recipes semantic identity

Recipe lookup should not depend on exact wording.

Equivalent questions such as:

* "How much pension do I get?"
* "What's my annuity?"
* "What would my retirement benefit be?"
* "How much would I receive each month?"

should resolve to the same canonical calculation concept when the Article/tier/benefit context matches.

Recipe identity should consider concepts such as:

* Article
* tier
* benefit
* calculation type
* eligibility
* required inputs
* dependencies
* effective-date/version information

---

## 16. Recipe derivation must be evidence-backed

A derived recipe should retain:

* canonical identity
* rule specification
* source sections
* evidence references
* assumptions
* applicable Article
* tier
* required inputs
* dependencies
* risk tier
* knowledge status
* execution policy
* version

Do not allow an unvalidated LLM-generated formula to silently become authoritative.

Use the existing lifecycle:

discovered
→ agent_derived
→ validated
→ approved
→ stale/retired

but make the agent actively use this lifecycle.

---

## 17. Support mixed information + calculation naturally

The user can ask:

"What happens if I retire at 58, am I eligible, and how much would I get?"

The agent should decompose this automatically.

It should not force the user to ask:

1. Am I eligible?
2. What is the formula?
3. What is my benefit?

The assistant should solve the complete task.

---

## 18. The agent should proactively be helpful

Do not ask permission for obvious next steps.

If the user asks:

"Can I retire at 60?"

and the assistant can safely determine eligibility and calculate the approximate benefit, it should do so when useful.

Likewise, when comparing scenarios, it should naturally explain the important difference rather than merely returning numbers.

The assistant should optimize for:

> "What answer would actually help this user?"

not:

> "What minimum response satisfies this node?"

---

## 19. Answer sufficiency should be explicit

Before asking a clarification, the agent should determine whether it can already produce:

* an exact answer
* an answer with disclosed assumptions
* a conditional answer
* a useful partial answer

Only ask when none of those provide a responsible answer.

---

## 20. Preserve all existing deterministic safety

Do not weaken:

* citation validation
* provenance checks
* RLS
* authorization
* calculation validation
* deterministic arithmetic
* recipe execution policies
* risk assessment
* evidence requirements
* coverage conflict handling
* guardrails

The goal is NOT "less deterministic."

The goal is:

> deterministic where correctness requires determinism, intelligent where conversation/reasoning requires intelligence.

---

## 21. Perform a "rigidity audit" before implementation

Audit the existing codebase and identify every place where the system currently:

* forces a node to execute
* asks a question prematurely
* assumes the employee record is always authoritative
* terminates after a failed retrieval
* prevents retrieval retry
* prevents hypothetical reasoning
* forces one Article onto every question
* forces one scenario interpretation
* treats missing information as a blocker
* re-fetches information already available in state
* derives the same recipe repeatedly
* makes tool invocation decisions deterministically when the LLM could safely decide

For each occurrence, classify it as:

A. Must remain deterministic
B. Should become an agent decision
C. Should become an optional capability/tool
D. Should be removed
E. Needs a state/context redesign

Do not implement blindly. First produce the audit.

---

# Target behavior

The final assistant should feel like:

"Tell me what you're trying to accomplish. I'll understand it, use what I already know, research the Illinois Pension Code when needed, make reasonable assumptions when safe, explain those assumptions, calculate authoritative figures through the deterministic engine, remember what we've established, handle hypothetical scenarios naturally, and keep working when the first search doesn't find the answer."

It should NOT feel like:

"Please provide field A.
Please provide field B.
Please provide field C.
I searched once and didn't find it.
I cannot answer.
Please ask another question."

The goal is an intelligent assistant with deterministic legal/calculation infrastructure underneath it—not a deterministic workflow with an LLM attached.

---

# 30. One architectural principle I'd put at the top of the project

I'd actually put this in `CLAUDE.md` prominently:

> **The agent owns the conversation. The deterministic services own correctness.**

Or even:

> **The LLM decides what needs to happen; deterministic systems decide what is allowed to happen and execute authoritative operations.**

That captures the balance you're looking for.

---

# 31. What I think is actually missing today

If I reduce everything down, I see **8 architectural gaps**:

| Gap           | Current system               | Desired system                            |
| ------------- | ---------------------------- | ----------------------------------------- |
| Conversation  | Turn classification          | Goal-oriented reasoning                   |
| Graph         | Mostly fixed pipeline        | Agentic loop                              |
| Tools         | Pipeline stages              | Agent-selected capabilities               |
| Retrieval     | Mostly one-shot              | Iterative evidence search                 |
| Context       | Profile/scenario state       | Rich conversational knowledge state       |
| Missing info  | Ask user                     | Infer → assume → retrieve → ask        |
| Recipes       | Registry + resolution        | Persistent calculation knowledge/compiler |
| Hypotheticals | Coverage-sensitive exception | First-class question context              |

And there's a ninth one underneath all of them:

### **The system lacks a persistent distinction between "what is true about the member" and "what the user is currently asking me to reason about."**

That is why your hypothetical example, cross-Article questions, scenario corrections, and conversational follow-ups are so important.

---

## The most important change

I **wouldn't start by rewriting the whole graph**.

I'd first have Claude inspect the current implementation and produce a map like:

```text
CURRENT
────────

User
 ↓
triage
 ↓
understand
 ↓
fetch employee
 ↓
coverage
 ↓
retrieve
 ↓
recipe
 ↓
inputs
 ↓
calculate
 ↓
answer


TARGET
──────

User
 ↓
Conversation/Goal Understanding
 ↓
Agent Decision Loop
 │
 ├── use conversation context
 ├── use member context
 ├── retrieve
 ├── retry retrieval
 ├── lookup recipe
 ├── derive recipe
 ├── resolve inputs
 ├── calculate
 ├── compare
 ├── handle hypothetical
 └── ask clarification
 │
 └───────────────┐
                 ↓
          Answer Sufficiency
                 │
        ┌────────┴────────┐
        ↓                 ↓
      done             continue
        ↓                 │
      answer ←────────────┘
```

**Then refactor toward that incrementally.**

Because your current deterministic infrastructure is actually valuable. The mistake would be throwing it away. The real job is to put an **intelligent orchestration layer above it**, rather than continuing to add more deterministic rules to the existing pipeline.

And one particularly important thing for Claude: **don't let it solve the current rigidity by creating a giant deterministic "scenario engine" with 50+ scenario types.** That would recreate the exact problem you're trying to escape. The LLM should understand the user's scenario; the code should provide safe primitives for facts, retrieval, recipes, validation, and calculation.
