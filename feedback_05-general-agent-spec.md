I went back to the beginning and evaluated both documents against the **actual product goal you just restated**, not against whether the new design is an improvement over the old one.

My conclusion is:

> **The 2026-09-16 design is substantially better than the 2026-09-12 design, but I would not implement it as the final architecture.**
>
> It has removed much of the old rigidity, but it has replaced a **calculation-first pipeline** with a more sophisticated **register/calculation-centric agent**.
>
> The deeper goal should be: **a general conversational RAG agent whose default capability is understanding and answering questions over the Illinois Pension Code, with calculation as one optional tool among many.**

The new document itself explicitly recognizes the first half of this: “Calculation is one capability it has, not the shape of the thing.” 

But several design decisions later in the document still pull the architecture back toward calculation.

---

# 1. First, let's define the product correctly

This is the most important thing.

Your system is **not** fundamentally:

> “An Illinois pension calculator that can talk naturally.”

It is:

> **“A general-purpose assistant for employees, grounded in the Illinois Pension Code, which happens to have a highly reliable pension-calculation capability.”**

That distinction changes the architecture considerably.

The mental model should be:

```text
                    Illinois Pension Assistant
                              │
                              ▼
                     General Agent / LLM
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
         RAG / Law        Calculation      Future tools
         retrieval          engine         / capabilities
             │                │                │
             ▼                ▼                ▼
          Corpus          Deterministic      Other
          evidence         computation    capabilities
```

Not:

```text
Question
   ↓
What kind of pension task is this?
   ↓
Which register?
   ↓
Which calculation state?
   ↓
How many assumptions?
   ↓
Can calculation happen?
   ↓
Answer
```

The new design is much closer to the first diagram, but it still contains quite a lot of the second.

---

# 2. What the new design gets genuinely right

There is a lot here that I would keep.

The **ledger** is a very good idea. Durable per-subject facts, provenance, supersession, and separation between member facts and hypothetical subjects are exactly the kind of memory a conversational agent needs. 

The **subject abstraction** is also very good.

This:

```text
me
firefighter one
teacher one
hypothetical Article 4 person
baseline
scenario A
scenario B
```

is much more general than hard-coded `member` / `hypothetical_frame` / `scenario_set` concepts. 

The **retrieval graph + outline** is also strong. Simply rerunning vector search isn't enough for a legal corpus; being able to say:

```text
§11-134
   ↓
§11-216
   ↓
exceptions
```

and navigate the Code structurally is much closer to how a legal reasoning system should investigate. 

And the new exhaustion treatment is excellent.

The design correctly noticed that:

> “I didn't find enough evidence” ≠ “the law says X.”

The `referenced_sections` check is much better than an LLM-generated confidence score. That part I would retain.

---

# 3. But the biggest conceptual problem remains

## You have made “register” the new central abstraction

This is where I think the design has gone a little too far.

The document says:

> “Register — what a block claims”

and then defines:

```text
explain
requirements
estimate
illustrate
suppose
recommend
reflect
boundary
```

with different standards. 

This is clever.

But ask yourself:

**Why does a general assistant need to classify every piece of its answer into one of eight epistemic/output registers?**

Consider a user asking:

> “Why does the Code reduce the pension if you retire early?”

That's obviously `explain`.

Then:

> “Does that reduction still apply if I have 30 years of service?”

Still `explain`, but now it requires eligibility analysis, retrieval, exception handling, perhaps comparison.

Then:

> “What about Article 4?”

Still just conversation + RAG.

Then:

> “What would happen if I retired two years later?”

Now it becomes projection + calculation.

Then:

> “Why is my result different from what we calculated earlier?”

Now it is conversation state + prior result + law + calculation provenance.

These aren't fundamentally different **assistant types**.

They're just different **things the agent needs to do to answer the question**.

That's an important distinction.

---

# 4. The register system is still semantic routing — just hidden inside the answer

The old architecture had:

```text
requires_calculation
needs_figure
answers_other_article
frame
clarification_budget
```

The new architecture deletes those. Good. The old document explicitly identified these early semantic decisions as the source of rigidity. 

But now you have:

```text
register = explain / requirements / estimate / illustrate / suppose / ...
```

So some of that decision-making has simply moved.

It is no longer:

> “Does this turn require calculation?”

It becomes:

> “Which claim shape does this block belong to?”

That is more flexible, but it is still a taxonomy that the model must navigate.

And worse, the taxonomy is **calculation-heavy**.

Look at the amount of architectural machinery associated with:

* `estimate`
* `illustrate`
* `suppose`
* `recommend`
* `REQUIREMENTS`
* calculation assumptions
* statutory defaults
* threshold calculations
* ranges
* projections
* stand-ins
* calculation views

versus the basic job:

> “Understand the employee's question and answer from the Pension Code.”

That's backwards relative to the product goal.

---

# 5. `explain` is actually the architecture you really want

There is an interesting clue in the design.

It says `explain` is the default. 

I think the architecture should go one step further:

### Don't make `explain` a register.

Make **general answering** the default behavior.

Then calculation is simply one optional capability.

For example:

```text
User:
"What is the retirement age under Article 11?"
```

Agent:

```text
retrieve → answer
```

---

```text
User:
"Why does Article 11 reduce my benefit if I retire early?"
```

Agent:

```text
retrieve
→ follow referenced provision
→ answer
```

---

```text
User:
"I'm 58 with 27 years of service. Can I retire and what would I receive?"
```

Agent:

```text
retrieve
→ inspect profile
→ lookup recipe
→ calculate
→ answer
```

---

```text
User:
"What if I work two more years?"
```

Agent:

```text
conversation context
→ projection
→ calculate
→ compare
→ answer
```

The **agent loop stays the same**.

Only the tools used change.

That's the generality you're aiming for.

---

# 6. The biggest architectural change I would make

I would make the architecture **capability-first, not register-first**.

Instead of:

```text
turn
 ↓
determine register
 ↓
apply register policy
 ↓
investigate
 ↓
compose
```

I would make it:

```text
                       USER
                        │
                        ▼
                ┌──────────────┐
                │ General LLM  │
                │ Agent        │
                └──────┬───────┘
                       │
            decides what is needed
                       │
          ┌────────────┼─────────────┐
          ▼            ▼             ▼
      RAG/search    profile       calculation
          │            │             │
          ▼            ▼             ▼
      statute       facts         recipe/DSL
          │            │             │
          └────────────┼─────────────┘
                       ▼
                 agent continues
                       │
                       ▼
                     answer
```

The agent isn't deciding:

> “This is an estimate register.”

It's deciding:

> “To answer this, I need the Article, the member's service, and the benefit formula.”

That is a **much more natural abstraction for an expanding general agent**.

And tomorrow when you add:

```text
survivor-benefit tool
document parser
administrative-policy corpus
statute-version tool
forms/application tool
service-reciprocity tool
```

you don't need to create new registers.

You simply add capabilities.

---

# 7. The current register taxonomy also doesn't cover the true query space well

The design claims the claim dimension is a closed set:

> law says X / quantity N / something is missing / about this conversation / Code is silent. 

I don't think that's actually true.

Real questions will include:

```text
"Explain this section."
"Compare these two Articles."
"What does this word mean here?"
"Why does this exception exist?"
"Does subsection (f) override subsection (a)?"
"Walk me through this."
"Summarize Article 11."
"What changed between these two provisions?"
"Is this contradictory?"
"How do these sections interact?"
"What happens first?"
"Give me an example."
"What would happen in my situation?"
"Why did you calculate this?"
"Earlier you said X, now you're saying Y."
"Show me where that comes from."
"What do I need to know before deciding?"
```

A lot of those collapse awkwardly into `explain`, `reflect`, or `requirements`.

That tells me the taxonomy isn't actually helping the general agent much.

---

# 8. I would remove `recommend` entirely from the core architecture for now

The document spends architectural effort defining:

```text
recommend
```

and its constraints. 

But “recommendation” is not central to your original use case.

The assistant needs to **inform employees about the law**, explain consequences, calculate when requested, compare scenarios, etc.

Recommendation can be a future capability.

Making it a first-class answer register now adds conceptual weight for something that's not necessary to achieve your core objective.

Same with some of the other specialized view types.

They can be **capabilities**, not foundational architecture.

---

# 9. The most important issue: latency

This is where I think the new design does not fully satisfy your actual requirement.

The measurements are very useful.

Current:

```text
TTFT p50 = 9.71s
round trips = 3.0
```

Moving search forward:

```text
TTFT p50 = 7.59s
round trips = 2.0
```

And two model round trips alone consumed about 5.96 seconds in the best tested case. 

That tells us something extremely important:

> **Your primary latency problem is not primarily context size, prompt caching, or calculation. It is model round trips.**

The design recognizes this and proposes the fused call. Good.

But I don't think the proposed solution is sufficient.

---

# 10. “One fused call” does not actually solve the general agent latency problem

The design says:

> “The call that reads the statute becomes the call that writes the answer.”

and:

> “if the first delta is a tool call, buffer and iterate; if it is content, stream it through.”



This is clever, but there's a fundamental issue.

Suppose the user asks:

> “Why does my pension get reduced at age 55?”

The model needs the statute before giving the legal answer.

If the first model call says:

```text
tool call → search_statute(...)
```

you still have:

```text
LLM call 1
    ↓
tool
    ↓
LLM call 2
    ↓
answer
```

You haven't actually removed the round trip.

You've merely fused **planning and writing into one conversational model history**.

That's architecturally cleaner, but latency doesn't disappear.

The design itself admits that fresh premise turns remain around 6.5 seconds and uncommon estimates around 6 seconds. 

And first-ever recipe derivation is ~30 seconds.

That's okay operationally for a minority of tasks, but it isn't the same as solving your <5s goal.

---

# 11. I think the missing idea is: **RAG should be the default substrate, not an optional tool call**

This is the biggest change I would recommend.

You said:

> “by default as a rag agent it should be able to accurately answer any type of query from this corpus.”

Exactly.

Therefore, for an ordinary corpus question, retrieval should probably happen **before the agent has to think about tools**.

Not because retrieval is mandatory routing.

Because **RAG is the environment the agent operates inside**.

Think of it like this:

```text
User message
    │
    ├───────────────┐
    │               │
    ▼               ▼
Fast retrieval   Load session state
    │               │
    └───────┬───────┘
            ▼
       First LLM call
            │
            ▼
      Answer directly
            │
       or call tools
            │
            ▼
      Further investigation
```

This is materially different from:

```text
User
 ↓
LLM
 ↓
"Should I search?"
 ↓
search
 ↓
LLM
 ↓
answer
```

The latter inherently burns a model round trip.

---

# 12. This doesn't violate “tools only when needed”

Because **retrieval isn't necessarily the same thing as an agent tool decision**.

For your specific product, the corpus is the environment.

You could have a fast deterministic retrieval layer that runs automatically on every substantive turn:

```text
query
+ recent conversation
+ active subject
+ known Article
        ↓
hybrid search
        ↓
top evidence bundle
```

Then the LLM gets:

```text
User message
Conversation state
Relevant statute sections
Available capabilities
```

and decides:

> “I can answer.”

or:

> “I need more evidence.”

or:

> “I need the calculation tool.”

or:

> “I need to follow §11-216.”

That gives you the **general agent** you want.

---

# 13. This also gives you the cleanest TTFT architecture

For common RAG queries:

```text
request
   │
   ├───────────────┐
   │               │
retrieve       state/profile
~1-2s            ~fast
   │               │
   └──────┬────────┘
          ▼
      one LLM call
          │
          ▼
       tokens
```

Potentially:

```text
<5s TTFT
```

without requiring the model to first decide whether to retrieve.

Then complex requests become:

```text
fast initial retrieval
        ↓
LLM
        ↓
needs more?
    /       \
  no         yes
  │           │
answer      tool call
              ↓
          further LLM turn
```

The **majority** of turns get the fast path.

Complex turns pay for complexity.

That's exactly how I would design for your stated requirement.

---

# 14. Your current design is still over-investing in calculation latency

Look at the new roadmap:

```text
§3 corpus
§4 calculation
§2 loop
§5 deletion
§6 learning
```

and particularly the calculation section:

* candidate recipe sets
* thresholds
* projection
* stand-ins
* ranges
* calculation views
* statutory defaults



These are useful.

But they are **capabilities**.

They should not determine the architecture of the core agent.

I'd invert the priority:

### Core agent

```text
conversation
state
RAG
reasoning
evidence
tool selection
answer generation
```

### Calculation capability

```text
recipe registry
formula DSL
input resolver
projection
thresholds
ranges
provenance
```

Then future capabilities plug into the exact same framework.

---

# 15. Calculation should be special because it needs strictness — not because it's the center of the agent

This is an important distinction.

Calculation absolutely deserves special guardrails.

For example:

```text
LLM:
"What calculation should I perform?"

        ↓

calculation tool

        ↓

deterministic engine

        ↓

verified calculation result

        ↓

LLM explains result
```

The LLM doesn't calculate.

That's excellent.

But it doesn't mean the **entire conversation architecture should be organized around calculation**.

The new design is still too close to:

```text
answer shape
    ↓
calculation provenance
    ↓
calculation suitability
    ↓
calculation register
    ↓
calculation views
```

when most user interactions aren't calculations.

---

# 16. I also don't like the “requirements” register as a fundamental primitive

This line is particularly revealing:

> “requirements — list computed from the recipe's declared inputs and the Article's eligibility section”



What happens when the user asks:

> “What do I need to qualify for retirement?”

Then a recipe-based requirements engine makes sense.

But what about:

> “What do I need to do to transfer service?”

> “What information does this section require?”

> “What conditions have to be satisfied before this exception applies?”

Those are not necessarily formula inputs.

So `requirements` is really a **specific capability**:

> eligibility/input resolution

It doesn't deserve to be a core answer type.

The general agent should be allowed to simply retrieve the relevant law and answer naturally.

---

# 17. The assumption ladder is useful, but the global `assumption_ceiling = 2` worries me

The design says:

```text
no gaps
→ estimate

1–2 fillable value gaps
→ estimate

>2 value gaps
→ illustrate + requirements
```



This is another example of turning a useful safety rule into a broad assistant policy.

Imagine:

> “Assume I work until 60, keep my salary flat, and use 30 years of service.”

That's three hypothetical assumptions, but the user explicitly supplied them.

There is no epistemic problem.

Conversely:

> “Assume I retire in two years.”

may be one assumption but it can radically change the legal applicability.

So the important quantity isn't:

```text
number of assumptions
```

It's:

```text
source
+
confidence/provenance
+
materiality
+
whether the assumption changes legal applicability
```

You already have much better concepts for this.

I would make **assumption ceiling a tool/guardrail policy for calculations**, not a universal conversational principle.

---

# 18. The new “statutory defaults” idea needs a careful boundary

The document says a statutory defaults registry makes:

> “assume a standard number”

answerable with a citation. 

This is fine **only when the statute actually specifies that default**.

It should never become:

```text
missing salary
→ default salary
```

because the Code doesn't contain typical salaries, which the document correctly acknowledges. 

I would therefore rename the concept more narrowly:

```text
statutory_parameters
```

rather than:

```text
statutory_defaults
```

because most of these aren't “defaults” in the conversational sense.

They are:

```text
rates
thresholds
ages
minimums
averaging periods
statutorily defined constants
```

This avoids encouraging the agent to treat statutory constants as generic assumptions.

---

# 19. The biggest missing architectural concept: **fast path vs agentic path**

I think this is the key idea that the design needs now.

The current document thinks mostly in terms of:

```text
loop
```

But your latency objective requires thinking in terms of **two execution modes**.

## Fast path

For ordinary questions:

```text
User
 ↓
cheap state/context preparation
 ↓
fast RAG
 ↓
single model call
 ↓
stream answer
```

Target:

```text
<5s TTFT
```

Examples:

> “What is Article 11?”

> “What is the minimum age?”

> “Does Article 12 cover teachers?”

> “Explain this section.”

> “What does vested mean?”

> “Why is this reduction applied?”

---

## Agentic path

For complex requests:

```text
initial evidence
 ↓
LLM
 ↓
tool
 ↓
more evidence
 ↓
LLM
 ↓
another tool if necessary
 ↓
answer
```

Examples:

> “I'm 58, with 27 years, and I'm thinking of retiring in two years. What happens?”

> “Compare Article 11 and Article 12 for me.”

> “What if I were covered under Article 4?”

> “Calculate my benefit under both Tier 1 and Tier 2.”

The important thing:

**The agent doesn't need to decide which path before retrieval.**

Initial retrieval can happen cheaply.

The model then decides whether additional investigation is necessary.

---

# 20. This would also solve your “tools only when needed” principle

The agent would have tools such as:

```text
search_corpus
get_section
follow_reference
inspect_outline

get_profile
lookup_recipe
compute_benefit
project_values
compare_scenarios
```

But only the first retrieval environment is automatically available.

The LLM decides when it needs the others.

So:

> “What does §11-134 say?”

doesn't call calculation.

> “Why does my result change?”

may call calculation.

> “Tell me about survivor benefits.”

doesn't call calculation.

> “What would my survivor pension be?”

does.

That is much closer to your intended product.

---

# 21. Another issue: `compose` is still carrying too much architectural responsibility

The design says:

> “The call that reads the statute becomes the call that writes the answer.”

That is good.

But the model shouldn't conceptually have a distinct:

```text
investigate
   ↓
compose
```

phase anymore.

That's still a mental split.

The actual agent should be:

```text
reason
 ↓
tool
 ↓
observe
 ↓
reason
 ↓
tool
 ↓
answer
```

with composition being simply the moment the model chooses to output the final response.

The answer isn't a separate cognitive stage.

The LLM should be able to naturally transition:

```text
"I need §11-216"
   ↓
tool
   ↓
"Now I have enough evidence."
   ↓
answer
```

That's simpler.

The new design is already close to this, but I would remove the conceptual importance of `compose` even further.

---

# 22. “One mind, one history” is correct — but I'd make one addition

The new design correctly says compose isn't a second mind and uses the same history. 

Good.

But the model's raw conversation history should **not** become the authoritative state.

You need two things:

```text
Conversation history
        +
structured current state
```

where:

```text
structured state
    > older conversational claims
```

The new ledger already moves in this direction.

That's important because:

> “Actually, I was hired in 2015, not 2012.”

must immediately supersede the old conversational statement.

Your document recognizes this with supersession, which is good. 

I'd just make it explicit that the LLM receives:

```text
CURRENT AUTHORITATIVE SESSION STATE
```

followed by the relevant conversational history, rather than expecting it to infer current truth from the entire transcript.

---

# 23. Another latency problem: context growth

The document's tests show:

```text
688 tokens  → 1.72s
7.9k        → 2.20s
15.8k       → 2.82s
26.3k       → 3.57s
42k         → 4.14s
```

and correctly concludes that removing a round trip matters more. 

I agree.

But this becomes dangerous as the “one mind, one history” model accumulates:

```text
conversation
+
ledger
+
retrieved sections
+
references
+
recipes
+
calculations
+
answer evidence
```

You cannot let the model see the entire accumulated state every turn.

You want:

```text
long-term state
       │
       ▼
state summarizer / selector
       │
       ▼
relevant working context
```

Not:

```text
entire session
```

The ledger being durable does **not** mean every fact must be placed into the prompt every time.

---

# 24. The answer-evidence memory is useful, but be careful not to create a second retrieval system

This section is good:

> enrichment rather than filter. 

Keep that.

I would make it explicitly:

```text
primary retrieval
      +
historical successful evidence hints
```

not:

```text
historical successful evidence
      ↓
preferred answer
```

Otherwise you'll eventually develop:

```text
retrieval corpus
+
answer_evidence corpus
+
recipe registry
+
section graph
```

and the agent starts needing to reason over several competing representations of “what is relevant.”

The primary legal corpus should remain authoritative.

---

# 25. The `recommend` capability reveals another issue

The design says:

> “The member chooses.”

That's correct. 

But this is exactly why **the LLM should primarily be an information/decision-support agent rather than a recommendation engine**.

A natural general assistant can say:

> “Here are the two outcomes and the provision that causes the difference.”

That is enough.

You don't need to make “recommend” a foundational register to achieve that.

---

# 26. I would change the abstraction from “register” to **answer constraints**

This is probably the cleanest compromise.

You don't have to throw away all the good work.

Instead of the LLM declaring:

```text
register = estimate
```

think:

```text
answer constraints:
    figures allowed: yes
    conditional: yes
    record access: no
    evidence required: yes
    calculation required: yes
```

And those constraints are determined by the **actual capabilities/results in the state**, not as the primary routing mechanism.

For example:

```text
User:
"What if I were under Article 4?"
```

Agent state:

```text
subject = hypothetical
article = 4
conditional = true
```

Then the agent decides whether it needs:

```text
retrieval
calculation
comparison
```

No need for:

```text
REGISTER = SUPPOSE
```

The distinction is subtle but important.

**Constraints protect the answer. They should not define the conversation.**

---

# 27. The guardrail strategy should move from “verify the answer type” to “verify claims”

The current design has:

```text
check applies to register
```

For example:

```text
citations → statute registers
figures → calculation registers
coverage → estimate/explain
conditionality → suppose/illustrate
```



I would make this more general:

```text
Every material claim produced by the model must be classifiable as:

STATUTE_CLAIM
USER_FACT
PROFILE_FACT
DERIVED_FACT
CALCULATION_RESULT
ASSUMPTION
HYPOTHETICAL
CONVERSATIONAL_STATEMENT
UNKNOWN
```

Then the verifier asks:

```text
Does this claim have an acceptable provenance?
```

rather than:

```text
Was this sentence written under the correct register?
```

This is much more extensible.

Tomorrow, when you add a disability tool, you don't create six new guardrail mappings.

The disability result is just:

```text
TOOL_RESULT
```

with provenance.

---

# 28. This also makes future capabilities far easier

The document currently says a capability becomes:

1. tool
2. fact class
3. register constraint



I would change that to:

1. **Tool/capability**
2. **Structured result + provenance**
3. **Security/validation contract**

That's all.

Then:

### Calculation

```text
compute_benefit
→ CalculationResult
```

### Survivor benefits

```text
compute_survivor_benefit
→ CalculationResult
```

### Document parsing

```text
extract_benefit_statement
→ DocumentFacts
```

### Another corpus

```text
search_admin_policy
→ Evidence
```

### Statute version comparison

```text
compare_statute_versions
→ Evidence + structured comparison
```

The core agent doesn't change.

That's exactly what you want from an extensible general agent.

---

# 29. I would also remove “recommendation” from the first version and push it into future capabilities

Likewise:

```text
thresholds
ranges
sensitivity
timeline
comparison
```

are useful **calculation presentation capabilities**.

They shouldn't dictate the base answer protocol.

You can keep them in the system, but they should only appear when useful.

The agent can decide:

> “This comparison would materially help the member.”

That keeps the assistant proactive without making every answer conform to a presentation schema.

---

# 30. On proactive behavior: this is where the design can become truly smart

The goal isn't merely:

> “Answer exactly what was asked.”

You want:

> “Understand what the employee is trying to accomplish and provide the useful information necessary to accomplish it.”

For example:

> “Can I retire at 58?”

A rigid bot says:

> “According to §X, minimum age is…”

A smart agent considers:

```text
What is known?
age
service
Article
tier

What is actually being asked?
eligibility

What information determines it?
retirement eligibility provision

Can I answer?
yes

Would calculation help?
probably, if benefit amount is available

Do I need to ask anything?
only if eligibility genuinely depends on a missing fact
```

So it might answer:

> “Under Article 11, 58 may be an eligible retirement age depending on your service and tier. Based on the record I have, you meet X but your tier determines Y. Here's how the rule works…”

That is the kind of intelligence you're describing.

**No explicit “scenario engine” required.**

---

# 31. I think your desired architecture is actually simpler than this document

Ironically, the better general agent you're describing may require **less architecture**, not more.

I'd target this:

```text
                    ┌─────────────────────────┐
                    │ Conversation State      │
                    │ ledger / subjects /     │
                    │ profile / history       │
                    └────────────┬────────────┘
                                 │
                                 ▼
USER ──────► Fast Retrieval Context ──────► GENERAL LLM AGENT
                                 │                │
                                 │                │
                                 │       ┌────────┼─────────┐
                                 │       │        │         │
                                 │       ▼        ▼         ▼
                                 │    retrieve  calculate  future tools
                                 │
                                 └──────────────► answer
                                                   │
                                                   ▼
                                               verifier
                                                   │
                                                   ▼
                                                stream
```

That's the architecture I'd want you to optimize around.

---

# 32. The key execution strategy

I'd define three levels.

### Level 0 — conversational

No retrieval necessary.

```text
"Thanks"
"Okay"
"Yes, that's what I meant"
"Can you explain that more simply?"
```

LLM + state.

---

### Level 1 — ordinary RAG

This should be the **default path**.

```text
user
 ↓
fast retrieval
 ↓
LLM
 ↓
answer
```

One model call.

This is where you need to get the bulk of requests under 5s TTFT.

---

### Level 2 — agentic investigation

When the first evidence isn't enough:

```text
initial RAG
 ↓
LLM
 ↓
search_statute
 ↓
get_section
 ↓
related_sections
 ↓
calculation
 ↓
answer
```

Complex requests can take longer.

That's acceptable because they're actually doing more work.

---

# 33. And this is where your <5s goal should be defined more intelligently

Don't set:

> “Every task must be under 5 seconds.”

That's unrealistic for a legal agent doing multi-step investigation.

Instead:

### P50 / majority experience

```text
ordinary RAG question:
<5s TTFT
```

### Complex agentic question

```text
additional latency permitted
```

### Very expensive task

```text
30s derivation / uncommon calculation
```

But it should be rare and obvious.

The current design already acknowledges that not every turn will be <5 seconds. 

I agree with the principle, but I'd make **the fast-path percentage an explicit product KPI**, not merely projected timings.

For example:

```text
P50 TTFT < 5s
P75 ordinary RAG TTFT < 5s
90% of ordinary RAG turns ≤ X
```

Don't just benchmark the average architecture.

Benchmark **query classes**.

---

# 34. I would change the evaluation suite substantially

The current evaluation suite is heavily oriented around:

* multi-part questions
* retrieval retry
* work-two-more-years
* hypotheticals
* recipe reuse
* thresholds
* projection
* calculation behavior



Those are good tests.

But they're not sufficient for the product you're actually describing.

You need a large **general RAG conversation suite**.

Something like:

```text
GENERAL UNDERSTANDING

"What is Article 11?"
"Explain this section."
"Summarize survivor benefits."
"What's the difference between X and Y?"
"Why does this rule exist?"
"What does this phrase mean?"
"Does subsection A override subsection B?"
```

Then:

```text
CONVERSATIONAL CONTINUITY

"Tell me about Article 11."
"How does that affect me?"
"What if I work two more years?"
"Actually make that three."
"What about Article 4?"
"Go back to the firefighter example."
```

Then:

```text
RETRIEVAL INTELLIGENCE

bad first retrieval
→ refined retrieval
→ reference traversal
→ answer
```

Then:

```text
CALCULATION

information only
information + calculation
calculation + hypothetical
calculation + comparison
missing inputs
```

Then:

```text
SAFETY / GROUNDING

uncited claim
wrong Article
missing referenced section
conflicting member fact
unsupported number
```

And finally:

```text
LATENCY

ordinary RAG
contextual follow-up
calculation
hypothetical
multi-step investigation
```

That would tell you whether you actually built the intended product.

---

# 35. One test is especially important

I would make this a flagship test:

> **“What is this assistant capable of doing?”**

Not literally as a user question, but as a product evaluation.

Give it 100 diverse questions from employees.

Don't categorize them first.

Then measure:

```text
Did it understand?
Did it retrieve the right law?
Did it answer naturally?
Did it ask unnecessarily?
Did it calculate when useful?
Did it avoid calculating when unnecessary?
Did it maintain context?
Did it correctly handle hypotheticals?
Did it complete the user's actual task?
How long until useful first token?
```

This is much closer to your real success criterion than whether the agent picked the correct register.

---

# 36. One potentially dangerous sentence in the latency section

The design says:

> “if the first delta is content, stream it through”

This needs a strong qualification.

For a legal RAG agent, you should **not stream substantive legal conclusions before the evidence is known**.

Otherwise the agent could produce:

```text
"Under Article 11, you're eligible..."
```

then realize it needs another section and discover:

```text
Actually, an exception applies.
```

Now you've already streamed an incorrect legal statement.

TTFT should not be optimized by streaming **ungrounded content**.

Better:

```text
pre-retrieval
→ evidence
→ grounded model output
→ stream immediately
```

or, for complex investigation:

```text
non-substantive progress event
→ investigation
→ grounded answer
```

Progress isn't the same thing as answer content.

That distinction is critical.

---

# 37. I would also be careful with speculative baseline calculation

The design proposes computing the “retire now” baseline concurrently before the agent asks for it. 

This can be useful for latency.

But it is also the clearest example of calculation creeping back into the center of the architecture.

I'd implement it only as an **optional prefetch optimization**:

```text
Does the request plausibly look calculation-related?
       │
       ├── yes → optionally prewarm calculation inputs/result
       │
       └── no → don't
```

But critically:

```text
prefetch ≠ routing
```

The agent still decides what to do.

This would preserve your general-assistant design while exploiting concurrency.

---

# 38. The most important change I'd make to the design document

I'd rewrite the first principle.

Instead of only:

> “Calculation is one capability it has, not the shape of the thing.” 

I'd make the entire architecture revolve around:

> **The assistant is a general conversational RAG agent over the Illinois Pension Code. Retrieval is its default environment; calculation is one capability it may invoke, alongside future capabilities. The model owns understanding, investigation, tool selection and response composition. Deterministic systems own facts, evidence integrity, calculation, validation and security.**

That one paragraph should become the architectural north star.

Every subsequent design decision should be tested against it.

---

# 39. What I would keep / change / remove

| Area                                          | My recommendation                           |
| --------------------------------------------- | ------------------------------------------- |
| General agent loop                            | **Keep**                                    |
| Same mind/history                             | **Keep**                                    |
| Durable ledger                                | **Keep strongly**                           |
| Subjects                                      | **Keep strongly**                           |
| Retrieval graph                               | **Keep**                                    |
| Outline/map                                   | **Keep**                                    |
| Iterative retrieval                           | **Keep**                                    |
| Candidate recipe lookup                       | **Keep**                                    |
| Deterministic calculation                     | **Keep strongly**                           |
| Projection                                    | **Keep**                                    |
| Evidence-gap detection                        | **Keep strongly**                           |
| Provenance                                    | **Keep strongly**                           |
| Answer evidence memory                        | **Keep, carefully**                         |
| Registers                                     | **Simplify substantially**                  |
| `requirements` as core type                   | **Move to capability**                      |
| `recommend`                                   | **Defer**                                   |
| Threshold/range views                         | **Capability/presentation layer**           |
| Assumption ceiling as global policy           | **Rework**                                  |
| One fused model call                          | **Keep as optimization, not core solution** |
| Streaming first model content before evidence | **Do not allow for substantive claims**     |
| Initial fast retrieval                        | **Add**                                     |
| Fast-path/agentic-path distinction            | **Add**                                     |
| TTFT as an explicit product KPI               | **Add**                                     |
| Full-session context in every prompt          | **Avoid**                                   |
| Calculation prefetch                          | **Optional optimization**                   |

---

# 40. The architecture I'd ultimately want

This is the version I believe most closely matches what you've been trying to build:

```text
                         USER
                           │
                           ▼
                ┌─────────────────────┐
                │ Conversation State  │
                │                     │
                │ profile             │
                │ subjects            │
                │ facts               │
                │ corrections         │
                │ active hypothetical│
                │ recent context      │
                └──────────┬──────────┘
                           │
                           ▼
                 ┌───────────────────┐
                 │ FAST RAG PREFETCH │
                 │                   │
                 │ query + context   │
                 │ hybrid retrieval  │
                 │ section graph     │
                 └─────────┬─────────┘
                           │
                           ▼
                 ┌───────────────────┐
                 │   GENERAL AGENT   │
                 │       LLM         │
                 │                   │
                 │ understand        │
                 │ reason            │
                 │ investigate       │
                 │ decide tools      │
                 │ answer            │
                 └─────────┬─────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
           RAG/tools   Calculation   Future tools
              │            │            │
              ▼            ▼            ▼
           Evidence    Deterministic  Capabilities
           graph       engine
              │            │            │
              └────────────┼────────────┘
                           │
                           ▼
                    VERIFIED STATE
                           │
                           ▼
                  GROUNDED RESPONSE
                           │
                           ▼
                       STREAM
```

And the guiding principle becomes:

> **The agent decides what it needs to accomplish the user's goal; capabilities provide the means; deterministic systems constrain what can be claimed.**

---

# 41. My final assessment of the two designs

### 2026-09-12

The old design identified the right philosophical problem:

> the LLM wasn't really reasoning; the pipeline was deciding everything in advance.

That's correctly documented in the old design. 

But it remained too pipeline-oriented and too willing to make early semantic decisions.

### 2026-09-16

The new design is **a major architectural improvement**:

* agentic loop
* iterative retrieval
* subjects
* durable ledger
* evidence graph
* better calculation abstraction
* better provenance
* better failure handling
* much better latency awareness

But it hasn't completely returned to the original product definition.

It has moved from:

```text
CALCULATION-FIRST PIPELINE
```

to:

```text
CALCULATION-CENTRIC GENERAL AGENT
```

What you actually want is:

```text
GENERAL RAG AGENT
        +
CALCULATION CAPABILITY
        +
FUTURE CAPABILITIES
```

That difference is important.

And the **single biggest technical change I'd make now is not another clever register or another guardrail.**

It is:

> **Introduce a fast default RAG path so ordinary corpus questions reach the LLM with evidence already available, allowing a one-model-call answer in the common case. Reserve the agentic tool loop for cases where the initial evidence isn't sufficient or another capability is genuinely needed.**

That attacks the **<5s TTFT problem** and, at the same time, gets the architecture much closer to the general assistant you originally wanted.

The current measurements already point directly at this: moving search earlier dropped TTFT from 9.71s to 7.59s, while the main remaining cost was the additional model round trip. 

So I would **not ask Claude to keep polishing the current register-heavy design yet**. I would send it back one level and ask it to redesign the core around **“general RAG assistant + optional capabilities + fast path/agentic path”**, while preserving the excellent ledger, provenance, deterministic calculation, evidence-gap, and retrieval-graph work already done.


Changed my mind regarding recommendations:
Yes — I would **keep recommendation as a first-class capability**. The important thing is to define what “recommendation” means for a pension-law agent without turning the LLM into an ungrounded financial adviser.

The new design is already pointing in the right direction: it says recommendations should be based on engine-computed alternatives plus the statutory provision causing the difference, while excluding things like life expectancy, tax, investment returns, health, and risk appetite because those aren't in the corpus. 

I would refine that substantially.

## 1. Recommendation should mean “recommend based on explicit objectives,” not “tell the employee what to do”

For this agent, there are really two different things:

### A. Consequence recommendation

> “If your goal is maximizing the monthly pension, retiring at 60 produces a higher benefit than retiring at 58.”

This is a legitimate recommendation because the objective is explicit and the comparison is computed.

### B. Personal life/financial advice

> “You should retire at 58 because that's financially optimal.”

That crosses into reasoning about things outside the Pension Code.

So the architecture should define:

> **Recommendation = selecting or highlighting an option according to an explicit criterion that is supported by corpus facts and/or deterministic capability outputs.**

That is a very useful capability.

---

# 2. The agent should NOT have a hard-coded “recommend” workflow

I would avoid:

```text
register = recommend
    ↓
must calculate 2 options
    ↓
must produce comparison
    ↓
must cite provision
```

because that becomes another rigid pipeline.

Instead:

```text
User asks:
"What should I do?"
        ↓
LLM understands objective
        ↓
LLM identifies candidate options
        ↓
retrieves law
        ↓
calculates consequences where necessary
        ↓
compares
        ↓
recommends / presents options
```

The recommendation is the **result of reasoning**, not the reason the agent enters a predetermined flow.

---

# 3. The most important thing: explicitly model the objective

Suppose the employee says:

> “Should I retire at 58 or 60?”

There is no universal answer.

The agent should first determine what “should” means in context.

It could be:

```text
maximize monthly pension
maximize total pensionable amount
retire as early as possible
avoid early-retirement reduction
reach eligibility sooner
maximize pension while retiring before 60
```

But this doesn't necessarily mean asking:

> “What is your objective?”

Every time.

The LLM should infer it from context when reasonable.

For example:

> “I want to retire as soon as I can without taking a reduction.”

This gives a clear objective:

```text
objective:
earliest retirement
constraint:
no reduction
```

Now recommendation becomes deterministic enough to support.

---

# 4. I would make `RecommendationContext` a small structured object

Not a “recommendation engine.”

Something like:

```json
{
  "objective": {
    "type": "maximize",
    "metric": "monthly_benefit"
  },
  "constraints": [
    {
      "type": "retirement_age",
      "operator": "<=",
      "value": 60
    }
  ],
  "candidate_options": [],
  "evidence": [],
  "computed_outcomes": []
}
```

The key is that the **LLM populates the user's objective**, while code validates what can actually be evaluated.

---

# 5. Separate three layers

This is the architecture I'd recommend.

## Layer 1 — Objective understanding

LLM determines:

> What is the employee trying to optimize?

Examples:

```text
"I want to retire as soon as possible."
→ minimize retirement age

"I want the highest pension."
→ maximize calculated benefit

"I don't want a reduction."
→ avoid reduction

"Which gives me more?"
→ maximize the relevant quantity
```

This is conversational intelligence.

---

## Layer 2 — Consequence computation

Tools determine:

```text
option A → $2,850/month
option B → $3,240/month
```

or:

```text
age 58 → 8% reduction
age 60 → 0% reduction
```

This stays deterministic.

---

## Layer 3 — Recommendation reasoning

The model then says:

> “Based on the objective you gave me, retiring at 60 produces the higher monthly benefit. Retiring at 58 lets you leave two years earlier but applies the reduction under §X.”

That is ideal.

The model is **not inventing the numbers** and **not deciding the employee's life priorities**.

It is connecting:

```text
objective
+
law
+
computed consequences
```

---

# 6. There should be a distinction between “recommend” and “compare”

This is important.

A lot of user questions aren't actually recommendations.

### Comparison

> “What's the difference between retiring at 58 and 60?”

The agent should simply show:

```text
58 → X
60 → Y
difference → Z
```

No recommendation required.

### Recommendation

> “Which should I choose?”

Now the agent needs an objective.

If the objective is known:

```text
goal = maximize monthly benefit
```

it can make a recommendation.

If the objective isn't known, it shouldn't fabricate one.

But it also shouldn't necessarily block:

> “It depends.”

Instead:

> “If your priority is the highest monthly pension, 60 gives you the higher amount. If your priority is leaving work earlier, 58 gets you there sooner but with the applicable reduction.”

That's an **objective-conditioned recommendation**.

Very useful.

---

# 7. This gives you a powerful pattern: conditional recommendations

I would make this central.

Instead of:

> “You should retire at 60.”

say:

> **“If your priority is maximizing your monthly pension, 60 is the better option under the assumptions above. If your priority is retiring earlier, 58 achieves that but results in a lower calculated benefit.”**

This is far more aligned with the role of the assistant.

The employee remains the decision-maker.

---

# 8. The recommendation capability can actually use the calculation engine as a substrate

This is where calculation becomes powerful without becoming calculation-first.

For example:

> “When should I retire?”

Agent:

```text
retrieve retirement rules
        ↓
identify relevant ages
        ↓
generate candidate scenarios
        ↓
compute:
55 → $X
56 → $Y
57 → $Z
58 → $A
59 → $B
60 → $C
        ↓
detect objective
        ↓
recommend / explain tradeoff
```

The calculation engine is merely providing consequences.

The **agent decides what scenarios are useful**.

That is much more general.

---

# 9. Recommendations should be constrained by what the corpus can actually evaluate

This is where the current spec's boundary is good and should remain. 

For example:

### Safe

> “Which retirement age gives me the highest calculated monthly benefit?”

You have:

```text
law
+
formula
+
member data
```

So you can evaluate it.

### Also safe

> “Which option avoids the early-retirement reduction?”

You can determine that from the Code.

### Not safely answerable from the Pension Code alone

> “Should I retire now given my mortgage?”

> “Should I retire because the market is bad?”

> “Would it be better for my family?”

> “Should I take the pension or invest elsewhere?”

Those require information outside the corpus.

The agent should not pretend the pension corpus is sufficient.

But that doesn't mean it must refuse.

It can say:

> “From the Pension Code alone, option A produces the higher monthly benefit. Whether it is better for you overall depends on factors outside this corpus, such as…”

That's exactly the general-assistant behavior you want.

---

# 10. Recommendation should be able to operate without calculation too

This is another reason I wouldn't make `recommend` synonymous with `compute`.

Example:

> “Which benefit option is available to me?”

Maybe the answer is entirely legal:

```text
Option A is available if...
Option B is not available because...
```

No arithmetic necessary.

Or:

> “Which retirement provision should I look at for someone in my situation?”

That's retrieval + reasoning.

So recommendation should be:

```text
reasoning capability
```

which **may invoke calculation**, not:

```text
calculation capability with a different output label
```

---

# 11. Candidate generation should belong to the agent

This is important.

Don't make the code generate:

```text
candidate_options = [58, 60]
```

unless the domain itself explicitly defines those choices.

The LLM can reason:

> “The meaningful alternatives here appear to be retiring at 58 versus 60 because the Code treats those ages differently.”

Then tools can validate and calculate.

So:

```text
LLM:
candidate hypotheses

Code:
validates / computes consequences
```

That division is consistent with your overall architecture.

---

# 12. Recommendation needs an evidence chain

Every recommendation should conceptually have:

```text
OBJECTIVE
   ↓
CANDIDATE OPTIONS
   ↓
LEGAL CONDITIONS
   ↓
COMPUTED CONSEQUENCES
   ↓
COMPARISON
   ↓
RECOMMENDATION
```

For example:

```text
Objective:
maximize monthly pension

Option A:
retire at 58
→ §11-134
→ 8% reduction
→ $2,850

Option B:
retire at 60
→ §11-134
→ no reduction
→ $3,240

Conclusion:
60 maximizes monthly pension among these options.
```

Now the recommendation isn't an LLM opinion.

It is a **derived conclusion over verified evidence**.

---

# 13. This is how I'd modify the current `recommend` register

I wouldn't delete it.

I'd change its semantics from:

> “a consequence the Code creates”

to something closer to:

> **“a decision-oriented response that compares supported alternatives against an explicit or inferable objective.”**

And the requirements become:

```text
1. objective identified
2. candidate options identified
3. relevant legal conditions established
4. required consequences computed or retrieved
5. recommendation follows directly from those results
6. unsupported external considerations are not introduced as facts
```

Notice something important:

**Not every recommendation needs two calculations.**

Two computed alternatives are needed only when the recommendation depends on numerical comparison.

---

# 14. I'd introduce `objective` as conversational state, not `recommend` as routing state

This fits beautifully with the architecture you're moving toward.

The conversation ledger could contain:

```json
{
  "objective": {
    "value": "maximize_monthly_benefit",
    "source": "user_inferred",
    "confidence": "high"
  }
}
```

or:

```json
{
  "objective": {
    "value": "retire_earliest_without_reduction",
    "source": "user_stated"
  }
}
```

Then subsequent turns become natural.

User:

> “What about 57?”

Agent already knows:

```text
current subject
+
retirement decision
+
objective
```

So it doesn't need to rediscover the recommendation context.

---

# 15. This also makes recommendations conversational

Example:

**User**

> I'm 58. Should I retire now?

**Agent**

> What matters to you here is whether you want to leave now or maximize the monthly benefit.

Then maybe it can infer from context.

**User**

> I mainly want the highest pension.

Now:

```text
objective = maximize monthly pension
```

**User**

> What if I wait two years?

Agent:

```text
project age → 60
project service
calculate
compare
recommend
```

**User**

> What if I wait three instead?

No restart.

```text
objective survives
subject survives
baseline survives
scenario changes
```

This is exactly the kind of conversational intelligence your architecture is trying to achieve.

---

# 16. One more powerful capability: recommendation under constraints

This is probably going to be a very common use case.

Examples:

> “What's the earliest I can retire without a reduction?”

```text
objective:
minimize age

constraint:
reduction = 0
```

> “What's the earliest I can retire and still get at least $3,000/month?”

```text
objective:
minimize age

constraint:
monthly_benefit >= 3000
```

> “I want to retire before 60 but get as much as possible.”

```text
objective:
maximize benefit

constraint:
age < 60
```

This turns the pension assistant into a genuinely useful decision-support system without turning it into a financial adviser.

---

# 17. This is where your deterministic threshold capability becomes extremely valuable

The new design's threshold engine is actually a perfect substrate for recommendation.

Suppose:

```text
58 → $2,850
59 → $3,020
60 → $3,240
```

The agent can detect:

```text
first age ≥ $3,000 = 59
```

or:

```text
first age with zero reduction = 60
```

The **agent doesn't need a hard-coded recommendation algorithm**.

It can ask the tools:

```text
calculate scenarios
find thresholds
```

and reason over their structured output.

---

# 18. The recommendation result itself should be structured

I'd add something like:

```json
{
  "recommendation": {
    "objective": "maximize_monthly_benefit",
    "recommended_option": "retire_at_60",
    "basis": [
      "higher_calculated_monthly_benefit"
    ],
    "alternatives": [
      {
        "option": "retire_at_58",
        "benefit": 2850,
        "tradeoff": "earlier retirement"
      },
      {
        "option": "retire_at_60",
        "benefit": 3240,
        "tradeoff": "later retirement"
      }
    ],
    "limitations": [
      "Does not consider taxes or non-pension financial factors"
    ]
  }
}
```

The model can turn this into natural language.

But the recommendation's **decisive facts exist structurally**, not only inside prose.

That's important for UI, auditing, and verification.

---

# 19. The guardrail should verify “recommendation consistency”

Rather than asking:

> “Was this written in the recommend register?”

verify:

```text
recommended option
       ↓
actually exists in candidate results?
       ↓
objective exists?
       ↓
objective supports the comparison?
       ↓
supporting figures exist?
       ↓
legal evidence exists?
```

Example:

LLM says:

> “I recommend 58 because it gives you the highest pension.”

Guardrail sees:

```text
58 = $2,850
60 = $3,240
```

and rejects the recommendation.

That's an excellent deterministic guardrail.

---

# 20. So I would keep recommendation—but make it a **decision-support capability**

The architecture becomes:

```text
                    GENERAL AGENT
                         │
                 understands intent
                         │
          ┌──────────────┼───────────────┐
          ▼              ▼               ▼
       explain        calculate       recommend
          │              │               │
          │              ▼               │
          │         deterministic       │
          │            engine            │
          │              │               │
          └──────────────┼───────────────┘
                         ▼
                       answer
```

And recommendation itself:

```text
recommend
   │
   ├── understand objective
   ├── identify constraints
   ├── generate candidate options
   ├── retrieve law
   ├── invoke calculations where required
   ├── compare consequences
   └── produce objective-conditioned recommendation
```

The crucial point is that **the agent chooses whether recommendation requires RAG, calculation, comparison, thresholds, or none of them**.

---

## The one sentence I'd send back to Claude

I would tell Claude:

> **Keep `recommend` as a first-class capability because decision support is a core use case, but don't make it a calculation-specific register or fixed workflow. Treat recommendation as an objective-conditioned conclusion over verified legal evidence and, where relevant, deterministic tool results. The LLM may infer or explicitly capture the user's objective, generate candidate options, and explain trade-offs; code must verify that recommended options, figures, constraints, and supporting provisions actually exist and that the conclusion follows from them. Recommendations must distinguish corpus-supported consequences from external personal/financial considerations, and when the objective is unknown, present conditional recommendations rather than inventing the user's preference.**

That would preserve the common/useful recommendation behavior while keeping the broader architecture **general-agent-first rather than calculation-first**.
