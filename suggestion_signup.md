
Yes — and this is where I would make an important distinction:

**Anonymous does not have to mean stateless.**

For your current product, I would support a reasonably rich anonymous session, but keep it completely separate from persistent employee data.

## The model I would use

```text
                    USER
                     │
          ┌──────────┴──────────┐
          │                     │
     Anonymous               Logged in
          │                     │
          ▼                     ▼
   Anonymous Session       Employee Identity
          │                     │
    ┌─────┼─────┐              ▼
    │     │     │        Employee Context
    │     │     │
    ▼     ▼     ▼
 Chat   Memory  Scenarios
 History
    │     │     │
    └─────┼─────┘
          ▼
      Chat Agent
          │
          ▼
      Pension RAG
```

The critical difference is **what can persist and for how long**.

---

# Anonymous mode should have 4 kinds of state

### 1. Session / chat history

Absolutely yes.

An anonymous user should be able to have:

> “What is Section 11-130?”

followed by:

> “Does that apply to someone with 21 years of service?”

followed by:

> “Now calculate it assuming a salary of $70,000.”

The chatbot needs the conversation history to understand that **“that”** refers to §11-130.

So anonymous users should have:

```text
session_id
conversation_id
messages
timestamps
```

A session might persist for a limited period, while a conversation contains the actual messages.

---

# 2. Conversational memory

Also yes, but I would make it **session-scoped by default**.

For example, anonymous user says:

> I'm 56.

Then:

> I have 21.5 years of service.

Then:

> My salary is $70,000.

The agent can maintain:

```json
{
  "age": 56,
  "service": 21.5,
  "salary": 70000,
  "source": "user"
}
```

Then:

> Calculate my pension.

works without requiring the user to repeat everything.

But this memory should normally disappear when the anonymous session expires.

---

# 3. Scenario state

This is separate from memory.

Suppose the user says:

> What if I retire at 58?

That should create:

```json
{
  "retirement_age": 58,
  "type": "hypothetical"
}
```

Then:

> What if I retire at 60 instead?

should modify the **scenario**, not the underlying remembered employee facts.

You want:

```text
User facts
   ├── age = 56
   ├── service = 21.5
   └── salary = 70,000

Scenario
   └── retirement_age = 60
```

not:

```text
age = 60
```

because that would corrupt the user's actual context.

---

# 4. Chat history

I would actually let anonymous users see their recent conversations.

Something like:

```text
Recent conversations

• Can I retire at 56?
• Article 11 vs Article 12
• What is salary under §11-116?
• Pension estimate at age 60
```

That is useful even without login.

But there is an important distinction:

### Anonymous conversation history

Can persist temporarily.

### Account history

Can persist long-term and be associated with the user after authentication.

---

# The really interesting part: anonymous → logged-in transition

I would design for this **now**, even though you don't yet know the eventual identity system.

Suppose an anonymous user spends 30 minutes discussing:

```text
Article 11
Age: 56
Service: 21.5
Salary: $70,000
Scenario: retire at 58
```

Then they decide to log in.

You should ideally be able to do:

```text
Anonymous Session
        │
        │ login
        ▼
Authenticated User
        │
        ├── account-linked history
        ├── employee context
        └── existing scenario
```

But don't blindly convert everything into employee facts.

The existing values remain:

```text
source = user_provided
```

while the employee database might say:

```text
service = 20.8
```

So after login the system could identify:

> “Your employee profile shows 20.8 years of service, while you previously entered 21.5 years in this conversation. I will treat 21.5 as a hypothetical/user-provided value unless you tell me otherwise.”

That is a **very strong design**.

---

# What should persist anonymously?

I'd use something like this:

| Data                        | Anonymous  | Logged-in       |
| --------------------------- | ---------- | --------------- |
| Current conversation        | ✅         | ✅              |
| Recent chat history         | ✅         | ✅              |
| Conversational memory       | ✅ session | ✅ longer-lived |
| Scenario assumptions        | ✅         | ✅              |
| User-provided pension facts | ✅ session | ✅              |
| Employee DB facts           | ❌         | ✅              |
| Saved calculation recipes   | Maybe      | ✅              |
| Long-term preferences       | Usually ❌ | ✅              |
| Cross-device history        | ❌         | ✅              |
| Saved profile               | ❌         | ✅              |

The exact retention period is a product/security decision, but this separation is what matters architecturally.

---

# I would NOT implement anonymous “permanent memory” yet

This is where I would be conservative.

For an anonymous user, don't try to maintain:

> “This person is probably Jaffer and historically they told us they have 21.5 years.”

You don't know who they are.

Instead:

```text
Anonymous
    ↓
session_id
    ↓
temporary memory
```

Once authenticated:

```text
User
    ↓
user_id
    ↓
persistent preferences/history
    +
employee context
```

This also avoids accidentally associating anonymous pension information with the wrong real employee.

---

# Your architecture can be very clean with three contexts

I would define these explicitly.

### `EmployeeContext`

Only from the employee source.

```python
EmployeeContext(
    employee_id=...,
    article=11,
    date_of_birth=...,
    service_history=...,
    salary_history=...,
    employment_status=...
)
```

### `ConversationContext`

Things established during the conversation.

```python
ConversationContext(
    user_facts=[...],
    previous_answers=[...],
    unresolved_questions=[...]
)
```

### `ScenarioContext`

Temporary/hypothetical assumptions.

```python
ScenarioContext(
    retirement_date=...,
    hypothetical_service=...,
    hypothetical_salary=...,
    assumptions=[...]
)
```

Then the agent receives:

```text
EmployeeContext       optional
ConversationContext   available
ScenarioContext       optional
             │
             ▼
       Pension Agent
```

This works beautifully for both anonymous and logged-in modes.

---

# There is one more thing I would add: source/authority

Every fact in the conversational state should have provenance.

For example:

```json
{
  "field": "service_years",
  "value": 21.5,
  "source": "user",
  "authority": "scenario",
  "timestamp": "...",
  "supersedes": null
}
```

Whereas:

```json
{
  "field": "service_years",
  "value": 20.8,
  "source": "employee_context",
  "authority": "authoritative"
}
```

And a derived value:

```json
{
  "field": "age",
  "value": 56,
  "source": "derived",
  "derived_from": ["date_of_birth", "current_date"]
}
```

This becomes incredibly useful to your reasoning and audit system.

---

# How I would make the actual UX work

### Anonymous user opens chatbot

```text
┌──────────────────────────────────────────────┐
│ Illinois Pension Assistant                  │
│                                              │
│ No employee profile connected                │
│                                              │
│ Ask about the Illinois Pension Code or       │
│ provide your own information for an estimate.│
│                                              │
│ [ Ask a question...                       ]  │
└──────────────────────────────────────────────┘
```

They can immediately ask legal questions.

If they start giving information, the chatbot uses it within the current conversation.

### Later:

```text
You have provided:
• Article 11
• Age 56
• Service 21.5 years
• Salary $70,000

These are user-provided values and are being used
only for this conversation.
```

That is excellent transparency.

---

# Then logged-in mode

```text
┌──────────────────────────────────────────────┐
│ Illinois Pension Assistant                  │
│                                              │
│ Employee profile connected                  │
│ Article 11 • Active                          │
│                                              │
│ Your pension information is available to     │
│ the assistant for personalized questions.    │
│                                              │
│ [ Ask a question...                       ]  │
└──────────────────────────────────────────────┘
```

Now:

> “Calculate my pension.”

can work immediately from the synthetic employee profile.

---

# And for your current prototype, I'd keep it simple

You don't need to build a massive memory platform.

I'd implement:

```text
Anonymous
    │
    ├── session_id
    ├── conversation history
    ├── short/structured conversation memory
    └── scenario state

Logged in
    │
    ├── user_id
    ├── conversation history
    ├── persistent memory/preferences
    ├── scenario state
    └── synthetic EmployeeContext
```

Both feed the **same agent**.

---

## The most important rule

I would write this into the architecture explicitly:

> **Conversation memory must never silently become employee data, and employee data must never be overwritten by conversational assumptions.**

That's the boundary that will protect you when you eventually replace the synthetic employee database with a real HR/payroll/pension source.

For the scope you're in now, I'd therefore build **anonymous mode with session history + session memory + scenario state**, and **logged-in mode with the exact same capabilities plus pre-seeded employee context and optionally persistent account-level history/preferences**.
