
Yes. I read Claude's new **`2026-09-12-intelligent-agent-design.md`** against your original feedback and the existing `CLAUDE.md`.

My overall assessment:

> **This is a very strong direction and is substantially closer to what you described. I would approve the architectural direction, but I would not implement it exactly as written yet.**

I'd give it roughly **8/10 architecturally**.

The biggest improvement is that Claude has correctly identified the root problem: the issue isn't that you need more scenarios, more regexes, or more deterministic gates. The issue is that **semantic decisions are being made once, too early, and then cannot be revised.**

That is exactly what you were trying to articulate.

However, there are **5 things I'd push back on before Claude starts implementing it.**

---

# 1. The core architectural change is exactly right

This is the best part:

```text
triage
  ↓
investigate ⇄ run_tools
  ↓
compose
```

instead of:

```text
understand
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
```

Claude explicitly proposes turning the old stages into capabilities/tools rather than mandatory nodes.

That is **exactly the direction I would take.**

And I particularly like this decision:

> `check_coverage` becomes necessary without being mandatory.

That's an excellent agent architecture principle.

It means:

```text
LLM:
"I need to establish whether this Article applies."

→ calls check_coverage()
```

rather than:

```text
System:
"Every question MUST run coverage."
```

That's the difference between a tool-using agent and a workflow.

---

# 2. Claude correctly found something important I didn't know from the original description

Claude inspected the actual implementation and discovered that several things you thought were missing **already exist**:

| Your concern                | Current implementation            |
| --------------------------- | --------------------------------- |
| Employee fetched every turn | `pinned_record` already exists  |
| Semantic recipe identity    | already exists                    |
| Recipe derivation           | already exists                    |
| Hypothetical context        | already exists                    |
| Assumption ledger           | `WorkSheet` + assumption policy |
| Derived recipes reusable    | already supported                 |

This is excellent because it means you **don't need to rebuild those systems**.

So your problem isn't necessarily missing infrastructure.

It's more:

> **The existing capabilities aren't being orchestrated intelligently.**

That's a much smaller and cleaner problem.

---

# 3. The retrieval redesign is very good

I strongly agree with this part.

Claude identifies the current problem as:

> `search_queries` are generated before seeing statute and never revised.

And the proposed solution:

```text
search
 ↓
read results
 ↓
reason
 ↓
better search
 ↓
read results
 ↓
reason
 ↓
related sections
 ↓
answer
```

is exactly what you asked for.

I especially like that Claude **didn't create a separate deterministic `transform_query` node**.

It says query rewriting should happen naturally inside the same agent loop.

I agree.

For example:

> "Can I retire early without losing too much?"

The model initially searches:

```text
early retirement
```

gets poor results.

It then sees the headings and thinks:

```text
Ah — the Code uses "annuity reduction"
and "withdrawal prior to age 60."
```

Then searches those terms.

That's genuine retrieval reasoning.

---

# 4. I REALLY like the "Code as graph + map" addition

This is more important than it may initially appear.

The existing system apparently only follows references one way and one hop. Claude proposes a proper:

```text
§11-134
   ↓ cites
§11-216

and

§11-216
   ↓ cited_by
§11-134
```

graph.

This is extremely useful for legal reasoning.

Because a rule can say:

> "Subject to Section X..."

and Section X may contain the exception that changes the answer.

A pure semantic/vector search system can easily miss that.

So:

```text
semantic retrieval
+
statutory graph traversal
```

is much stronger than vector search alone.

I would keep this.

---

# 5. The calculation changes are also very good

Claude found a subtle problem in the existing registry:

> it currently chooses a single recipe based on registry sort order.

That's dangerous.

The proposed change:

```text
lookup
 ↓
return all candidates
 ↓
evaluate applicability
 ↓
calculate applicable alternatives
 ↓
rank results
```

is much better.

For example, if the Code provides multiple pension calculation routes, the system shouldn't arbitrarily pick whichever recipe happens to sort first.

It should reason:

```text
Candidate A → applicable
Candidate B → applicable
Candidate C → not applicable

A = $32,127
B = $30,940

→ A is the better applicable benefit
```

That is much closer to an actual pension assistant.

---

# 6. The projection/stand-in distinction is excellent

This is another thing I think Claude got right.

It introduces:

```text
projected
stand_in
```

as explicit input origins.

That's very useful.

Consider:

> "What would my pension be if I worked two more years?"

You aren't making up the member's age.

You're projecting:

```text
current age + 2
current service + 2
```

That's fundamentally different from:

> "What would my pension be under Article 4?"

where the member's Article 11 facts are being used as **stand-in values** for a hypothetical Article 4 calculation.

Those should absolutely remain distinct.

---

# 7. BUT — there is one thing I don't completely agree with

This sentence concerns me:

> "The loop is bounded at 6 iterations with sub-budgets (≤3 searches, ≤1 derivation per benefit)."

I understand why they want bounds.

You absolutely need bounds.

But I don't think **6 agent turns should become a semantic constraint**.

Imagine a genuinely difficult question:

> "Compare my retirement options at 58, 60 and 62, considering the early retirement reduction, survivor benefit option, and the change in benefit if I work two additional years."

The agent might reasonably need:

```text
1. understand
2. retrieve retirement rule
3. retrieve reduction
4. retrieve survivor option
5. find formulas
6. derive recipe
7. resolve inputs
8. calculate
9. compare
```

Six may be insufficient.

I'd change the concept from:

> **6 iterations**

to:

> **bounded execution budget**

where the budget is based on **tool calls / token budget / time / recursion depth**, not simply "six thoughts."

For example:

```text
max_tool_calls = 12
max_searches = 5
max_recipe_derivations = 2
max_calculations = 10
max_wall_time = X
```

Then the agent can spend its budget intelligently.

---

# 8. More importantly: "one mind, one history" needs one addition

I like:

> "The model that read §12-118 and noticed the record disagreed is the model that writes the sentence about it."

That's good.

But don't let **"one history" become "one giant undifferentiated context."**

You need structured state alongside the message history.

Something like:

```text
Conversation History
        +
Structured Agent State
        +
Evidence State
        +
Calculation State
        +
Hypothetical State
        +
Assumption State
```

Because eventually the context window becomes huge.

The model shouldn't have to rediscover:

```text
age = 56
service = 25
article = 11
hypothetical = Article 4
assumption salary = 90k
recipe = X
```

from 40 previous messages.

The current `pinned_record`, `WorkSheet`, `hypothetical_frame`, calculation card, etc. should remain structured state.

So I'd tell Claude:

> **"One mind, one history" should mean one reasoning thread, not that all state exists only in raw message history.**

---

# 9. The biggest thing I think is still missing: an explicit notion of "goal"

This is where I would challenge the spec.

Claude has removed:

```text
requires_calculation
needs_figure
formula_types_needed
```

which is good.

But I don't see enough emphasis on maintaining a rich:

```text
USER_GOAL
```

throughout the loop.

The agent needs to know:

> **What am I actually trying to accomplish for the user?**

For example:

> "I'm thinking of retiring at 60. Am I eligible and how much will I get?"

The goal shouldn't become:

```text
needs_figure = true
```

Instead:

```json
{
  "goal": "evaluate retirement at age 60",
  "subgoals": [
    "determine eligibility",
    "calculate benefit",
    "explain relevant assumptions"
  ]
}
```

Then the agent can keep asking:

> Have I actually satisfied the goal?

This is much more important than classification.

I'd explicitly add this to the spec.

---

# 10. There's also a subtle risk with "the model answers from what it found"

Claude says:

> "Exhaustion is not an error: the model answers from what it found and says what is still open."

This is **mostly right**, but I'd modify it for a legal/pension assistant.

There are two very different situations:

### Situation A

The model has enough evidence but hasn't fully completed the investigation.

Then:

> Answer from evidence.

Fine.

### Situation B

The model needs a missing statutory provision to establish the answer.

Then:

> **Do not answer as though the incomplete evidence is sufficient.**

For example:

```text
I found §11-134, but it refers to a reduction
defined elsewhere and I haven't established that provision yet.
```

That's different from:

```text
Based on what I found, your pension is...
```

So I'd introduce:

```text
ANSWER_CONFIDENCE / EVIDENCE_SUFFICIENCY
```

or something similar.

Not just:

```text
loop exhausted → answer
```

The agent needs to determine whether exhaustion means:

```text
enough evidence
```

or:

```text
insufficient evidence
```

---

# 11. I disagree slightly with "registry trusted by default"

This is probably the biggest safety concern in the entire spec.

Claude says:

> "The registry is trusted by default. The admin's power is to correct and retire, not to admit."

I understand the reasoning.

And I agree that you don't want:

```text
derive recipe
→ wait for admin
→ user gets nothing
```

That would kill the agent's usefulness.

But I would distinguish **reusable** from **authoritative**.

Something like:

```text
DERIVED
    ↓
validated
    ↓
usable for estimate
    ↓
reusable
```

but:

```text
AUTHORITATIVE
```

should require stronger criteria.

Claude actually hints at this distinction elsewhere with:

> `approved`, `estimate`, `authoritative`.

I'd make that explicit.

### Example

New question:

> "How much would my pension be?"

No recipe exists.

Agent derives:

```text
recipe v1
source: §11-134
status: agent_derived
```

Validator confirms:

```text
structure valid
evidence complete
DSL valid
```

Then:

```text
execute → estimate
persist → reusable
```

That's fine.

But don't let:

```text
agent_derived
```

silently become:

```text
authoritative legal interpretation
```

The UI/answer should preserve that distinction.

---

# 12. The "learning" section needs careful boundaries

I like the `answer_evidence` idea.

But I would **not call it learning** in the LLM sense.

It's more like:

> **verified retrieval memory**

That's safer.

The system can remember:

```text
"Questions about early retirement reduction
were successfully answered using §11-134 and §11-216."
```

But it shouldn't learn:

```text
"§11-134 means X because the model said so."
```

unless that knowledge is backed by validated evidence.

Claude seems to understand this, since only verified answers enter the table. That's good.

I'd just make the terminology clearer.

---

# 13. The evaluation strategy is excellent

This may actually be one of my favorite parts of the spec.

Claude isn't saying:

> "Let's implement the new agent and see if it feels smarter."

It's creating tests specifically designed so:

```text
old pipeline → FAIL
new loop → PASS
```

Examples include:

* multi-part question
* failed first retrieval → successful second retrieval
* "work two more years"
* police officer hypothetical
* question requiring no tool
* recipe reuse
* thresholds
* projection
* section graph

That's exactly how I'd validate this architecture.

And the cutover criteria are sensible.

---

# 14. One test category I would add

I would add a dedicated:

## `goal_completion` suite

Because otherwise you can make the agent "agentic" without making it genuinely helpful.

Examples:

### Case 1

> "Can I retire at 58 and how much would I get?"

Expected:

```text
eligibility + calculation + explanation
```

Not:

```text
eligibility only
```

---

### Case 2

> "What if I work two more years?"

Expected:

```text
understand "two more years"
→ project age/service
→ calculate
→ compare with existing context
```

---

### Case 3

> "What about Article 4?"

Expected:

```text
switch question context
→ don't contaminate with Article 11
```

---

### Case 4

> "Actually make that three years."

Expected:

```text
modify existing scenario
→ don't start from scratch
```

---

### Case 5

> "What about my wife?"

Expected:

```text
recognize survivor-benefit continuation of current discussion
```

rather than:

```text
"What would you like to know about your wife?"
```

This is where you'll really measure whether it **feels intelligent**.

---

# 15. I'd also add a "don't ask unnecessarily" evaluation

This is one of your original complaints and I don't think the spec emphasizes it enough.

Test:

> "If I work two more years..."

with the profile containing:

```text
age = 56
service = 25
salary = ...
```

Expected:

```text
NO clarification
```

Then:

> "Assume I get a 3% raise."

Expected:

```text
update assumption
→ calculate
```

Then:

> "Actually I'm 57."

Expected:

```text
update fact
→ recalculate
```

This would be a great conversational regression suite.

---

# 16. The stance/coverage redesign is excellent

This part is particularly strong:

```text
"I am a park employee"
        ↓
asserted → verify

"If I were a park employee"
        ↓
stipulated → accept

"I think I'm a park employee"
        ↓
doubted → clarify
```

Claude has preserved that distinction while removing the rigid gate.

That's exactly the right direction.

---

# 17. I would NOT let Claude delete the old pipeline immediately

Fortunately, the spec already agrees with this.

The phased approach:

```text
Phase 1 → corpus
Phase 2 → calculation
Phase 3 → loop
Phase 4 → remove rigid blocks
Phase 5 → learning/evals
Phase 6 → cutover
```

is sensible.

And keeping:

```text
agent_mode
```

during migration is the right call.

I'd actually go further:

```text
agent_mode = pipeline | loop
```

and run **the same evaluation suite against both**.

That gives you a direct regression comparison.

---

# My overall verdict

I'd summarize the spec like this:

### 🟢 Very good / keep

**1. One tool-calling agent loop**

Excellent.

**2. Deterministic systems become tools**

Exactly right.

**3. Iterative retrieval**

Essential.

**4. Remove early semantic gates**

Correct.

**5. Persistent conversation state**

Correct.

**6. Hypothetical context**

Correct.

**7. Recipe reuse/derivation**

Correct.

**8. Statutory graph**

Very good addition.

**9. Projection / stand-in**

Excellent.

**10. Evaluation-first migration**

Excellent.

---

### 🟡 Modify before implementation

**1. Replace "6 iterations" with a flexible execution budget.**

**2. Add an explicit `user_goal` / `subgoals` representation.**

**3. Add evidence sufficiency / answerability logic.**

**4. Don't equate "loop exhausted" with "safe to answer."**

**5. Separate `agent_derived/reusable/estimate` from truly authoritative knowledge.**

**6. Keep structured state separate from raw conversation history.**

**7. Add goal-completion and unnecessary-clarification eval suites.**

---

# The most important conceptual correction

If I had to give Claude **one additional instruction before it implements this**, it would be:

> **Do not measure success by whether the agent successfully calls tools. Measure success by whether it accomplishes the user's underlying goal with the minimum necessary friction while preserving correctness.**

Because there's a danger here.

Claude could implement:

```text
LLM
 ↓
tool
 ↓
LLM
 ↓
tool
 ↓
LLM
 ↓
answer
```

and technically have an "agentic loop."

But it could still feel bad.

The actual objective is:

```text
                   USER GOAL
                       ↓
            ┌────────────────────┐
            │ What does the user │
            │ actually want?     │
            └─────────┬──────────┘
                      ↓
               What do I know?
                      ↓
             What can I infer?
                      ↓
             What must I verify?
                      ↓
              What tool is needed?
                      ↓
             Do I have enough?
              ↙             ↘
            YES              NO
             ↓                ↓
          answer       retrieve/reason
                              ↓
                         update state
                              ↓
                         ask again:
                     "Am I done yet?"
                              ↓
                           answer
```

**That is the architecture I think you're actually after.**

And honestly, Claude's spec is now **quite close** to it. The original system was a deterministic pipeline with an LLM inside it; this spec is moving toward an **LLM-centered agent with deterministic infrastructure underneath it**. That is the right architectural inversion.

The one thing I'd do **before letting Claude code** is send it a short review containing the 7 modifications above—especially **goal state, evidence sufficiency, execution budgets, and authoritative-vs-derived recipe status**. Those are the places where this otherwise very good spec could still produce a system that is technically "agentic" but not consistently intelligent or safe.
