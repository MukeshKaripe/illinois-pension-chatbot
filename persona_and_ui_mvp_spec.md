# Pension Chatbot MVP — Persona System & Chat-Centric UI Specification

**Document status:** MVP product/UX specification  
**Scope:** Authenticated persona/profile experience, anonymous chat experience, chat UI, sessions, scenarios, evidence, calculations, and Article-specific profile configuration  
**Current corpus status:** Most of the 24 Illinois Pension Code Articles are ingested. Articles **1A, 22A, 22B, and 22C still need to be ingested**. The UI and backend design must support these Articles without requiring a frontend redesign when they become available.

---

## 1. Purpose of This Document

This document defines how the MVP should look, feel, and behave around:

- Pension employee personas/profiles
- Article-specific persona schemas
- System-provided synthetic persona presets
- User-created profiles
- Primary/default profile selection during signup
- Persona/profile switching
- Session boundaries
- Temporary scenarios and hypothetical changes
- Authenticated vs anonymous behavior
- Chat-first UI inspired by polished applications such as ChatGPT and Claude
- Legal evidence presentation
- Calculation presentation and provenance
- Profile/session/version integrity
- Responsive behavior
- Loading, missing-input, ambiguity, and error states

The central product principle is:

> **The application should feel like a polished AI chat product first, while pension-specific complexity appears contextually around the conversation.**

The chatbot should not feel like a traditional pension portal with a chatbot bolted onto it.

The pension-specific differentiator is the intelligence underneath the chat:

> **RAG → legal reasoning → applicability evaluation → dynamic calculation recipe → validation/guardrails → deterministic execution → evidence-backed answer.**

The UI should expose the useful results of that process without exposing internal chain-of-thought.

---

# 2. Product Philosophy

## 2.1 Chat is the primary product

The main screen should be a familiar, spacious chat interface.

Users should immediately understand:

- where to type a question
- what profile is currently active
- what conversation they are in
- how to start a new chat
- how to inspect sources and calculations

The user should not need to understand the underlying agent architecture.

The interface should resemble the interaction conventions users already understand from mature AI products:

- left sidebar for conversations
- prominent New Chat action
- large central conversation area
- persistent composer
- contextual profile selector
- collapsible evidence/calculation panels
- progressive disclosure of technical/legal detail

## 2.2 Pension intelligence should appear contextually

Do not permanently expose every pension concept in the interface.

For example, scenarios are important but do not need a permanent dashboard section. A scenario can appear naturally after the user asks:

> “What if I retire at 59?”

The UI can then show a small scenario indicator above the composer and a structured scenario result in the conversation.

## 2.3 Profile data is context, not chat content

A user's synthetic employee profile should be available to the assistant without the user repeatedly typing facts into the chat.

For example:

```text
Article 11 · Standard Active Employee
Age 56 · 21.5 years service
```

The profile should be visible but visually quiet.

## 2.4 Scenario changes are temporary by default

Changing one value for a hypothetical question should not automatically create another saved persona/profile.

For example:

> “What if I retire at 59?”

should create a temporary scenario, not a new saved profile.

A user should explicitly choose to save a customized profile as a reusable profile.

## 2.5 Article is a legal boundary

A profile is tied to one Article.

The Article should be treated as immutable for a saved profile.

A user should not edit:

```text
Article 11 → Article 12
```

inside the normal profile editor.

To work with another Article, the user creates another profile.

This prevents accidental mixing of legal regimes and makes session provenance much cleaner.

---

# 3. Core Concepts

The product should distinguish four concepts clearly.

```text
ARTICLE
   ↓
PERSONA TEMPLATE
   ↓
PERSONA / EMPLOYEE PROFILE
   ↓
SESSION
   ↓
SCENARIO
```

## 3.1 Article

The statutory/legal domain.

Examples include:

- Article 11
- Article 12
- Article 1A
- Article 22A
- Article 22B
- Article 22C
- other supported Articles

The current application has most Articles ingested. Article 1A and Articles 22A, 22B, and 22C remain pending ingestion.

Importantly:

> **Article ingestion status is not the same thing as calculation/persona support status.**

An Article can be retrievable through RAG while still having incomplete calculation or persona support.

## 3.2 Persona Template

A system-defined starting point for a particular Article.

Examples conceptually:

```text
Article 11
├── Standard Active Employee
├── Near Retirement
├── Long-Service Employee
└── Custom
```

The exact list of templates should be derived from meaningful statutory/calc scenarios, not arbitrary age bands.

Do not create templates such as:

```text
Young Employee
Middle-Age Employee
Old Employee
```

unless those classifications have a real product/legal purpose.

Templates are convenience presets.

They are not legal rules.

## 3.3 Persona / Employee Profile

A concrete, reusable synthetic employee configuration created from a template or from another profile.

Example:

```text
Name: Standard Active Employee
Article: 11
Date of Birth: ...
Employment Status: Active
Service: 21.5 years
Current Salary: $69,956
Salary History: Available
Employee Contributions: $180,000
City Contributions: $360,000
```

The user does not have to maintain one manually after every hypothetical change.

## 3.4 Session

A conversation associated with one profile/version.

A session contains:

- messages
- conversation memory
- legal conclusions established during the conversation
- scenario state
- calculations
- source references
- profile/version reference or snapshot

A session should preserve the profile version used when it was created.

## 3.5 Scenario

A temporary set of hypothetical overrides applied to a profile inside a session.

Examples:

```text
Retire at age 59
Salary = $80,000
Service = 24.5 years
Retire at age 60
```

A scenario should not mutate the base profile.

---

# 4. Why This Model Is Preferred

The model solves several problems simultaneously.

## 4.1 Avoids repetitive data entry

The user selects a realistic synthetic employee profile once and can immediately ask questions.

## 4.2 Prevents persona explosion

Without scenarios, every hypothetical value change could become another saved profile.

Example of what should NOT happen:

```text
Profile 1: Age 56
Profile 2: Age 57
Profile 3: Age 58
Profile 4: Age 59
Profile 5: Age 60
```

Instead:

```text
Base Profile: Age 56
   ├── Scenario: Age 57
   ├── Scenario: Age 59
   └── Scenario: Age 60
```

## 4.3 Protects legal context

A profile cannot silently move between Articles.

## 4.4 Protects historical conversations

If a profile changes later, old sessions should remain reproducible.

## 4.5 Supports future real employee integrations

The current synthetic profile provider can later be replaced by a real employee/pension provider without redesigning the chat experience.

---

# 5. Anonymous vs Authenticated MVP

## 5.1 Anonymous mode

Anonymous mode is intentionally limited.

Anonymous users get:

- temporary chat
- conversation memory for the current session
- temporary facts supplied by the user
- temporary scenarios
- legal answers
- calculations when sufficient information is available
- evidence/source display

Anonymous users do NOT get:

- saved chat sessions
- saved personas
- profile management
- persistent memory across sessions
- account-linked employee data

Anonymous mode should feel like:

> **“Try the pension assistant.”**

## 5.2 Authenticated mode

Authenticated users get:

- primary/default profile
- reusable profiles
- Article-specific profile presets
- saved sessions
- session restoration
- profile/version history
- scenarios inside sessions
- persistent account-level data
- profile switching
- profile creation
- profile editing

Authenticated mode should feel like:

> **“Your pension workspace.”**

## 5.3 Anonymous session behavior

The anonymous session can still have conversational memory.

Anonymous does not mean stateless during the conversation.

For example:

```text
User: I am 56.
Assistant: ...

User: I have 21.5 years of service.
Assistant: ...

User: What if I retire at 59?
```

The assistant can use the earlier temporary facts.

When the anonymous session ends, those facts and the conversation are not persisted as account history.

---

# 6. Article-Specific Persona Architecture

A major design decision is:

> **Do not hard-code one universal persona form containing every field from every Article.**

Different Articles may require different facts.

The backend should therefore expose an Article-specific profile schema.

Conceptually:

```json
{
  "article": "11",
  "profile_schema": {
    "required": [],
    "optional": [],
    "derived": [],
    "advanced": [],
    "scenario_allowed": [],
    "validation_rules": []
  }
}
```

The frontend renders the form from that schema.

This means adding support for a new Article should not require rebuilding the frontend form manually.

---

# 7. Profile Field Categories

Each Article's schema should classify fields into several groups.

## 7.1 Common fields

Fields that can potentially apply across many Articles:

### Personal / identity

- profile display name
- date of birth
- age (derived from DOB where possible)

### Employment

- employment status
- hire/start date
- retirement/withdrawal/separation date where relevant

### Service

- creditable service
- service history
- prior service where relevant

### Compensation

- current salary
- salary history

These are candidate common fields, not automatically required by every Article.

## 7.2 Article-specific fields

An Article may introduce fields that are meaningful only for that Article or a subset of its benefit pathways.

Examples may include:

- specialized service categories
- contribution balances
- election indicators
- disability-related status
- reciprocal service data
- special eligibility flags
- benefit-specific dates
- historical/tier indicators

Only show fields when the selected Article/profile schema requires or supports them.

## 7.3 Required fields

Needed for normal supported calculations or eligibility evaluations.

## 7.4 Optional fields

Useful but not necessary for all questions.

## 7.5 Advanced fields

Useful for complex calculations but not necessary for a basic chat experience.

Hide these under an “Advanced information” section unless relevant.

## 7.6 Derived fields

The system calculates these instead of asking the user to type them.

Examples:

- age derived from DOB
- service duration derived from service dates
- highest/average salary derived from salary history
- reduction period derived from relevant dates
- other deterministic derived values

## 7.7 Scenario-only fields

Facts that should usually be introduced as hypothetical overrides rather than permanent profile fields.

Examples:

- hypothetical retirement date
- hypothetical future salary
- hypothetical extra service

---

# 8. Profile Field Validation

Validation should occur at multiple levels.

## Level 1 — Data type validation

Examples:

```text
Age → integer
Service → decimal
Salary → numeric/currency
Date → valid date
```

## Level 2 — Range validation

Examples conceptually:

```text
Age within reasonable bounds
Service non-negative
Salary non-negative
```

## Level 3 — Logical validation

Examples:

```text
Service cannot exceed plausible working life
Retirement date cannot precede employment start date
```

## Level 4 — Article-specific validation

The Article profile schema can add rules relevant to that Article.

## Level 5 — Legal applicability

This is separate from form validation.

Example:

```text
Age = 56
Service = 21.5
```

may be a perfectly valid profile while causing multiple statutory pathways to require evaluation.

The profile editor must not declare a benefit provision “applicable” merely because the values pass form validation.

The legal engine determines applicability.

---

# 9. Suggested Persona Data Model

Conceptually:

## Article

```text
Article
-------------------------
id
number
name
availability_status
ingestion_status
retrieval_status
calculation_status
persona_status
profile_schema
supported_features
```

## PersonaTemplate

```text
PersonaTemplate
-------------------------
id
article_id
name
description
default_values
allowed_fields
validation_rules
status
```

## Persona

```text
Persona
-------------------------
id
user_id
article_id
template_id
name
profile_data
is_primary
current_version
created_at
updated_at
```

## PersonaVersion

```text
PersonaVersion
-------------------------
id
persona_id
version_number
profile_data
created_at
created_by
```

## Session

```text
Session
-------------------------
id
user_id
persona_id
persona_version
persona_snapshot
created_at
updated_at
status
```

## Scenario

```text
Scenario
-------------------------
id
session_id
name
overrides
derived_values
results
created_at
```

The exact schema can evolve, but these conceptual boundaries should remain.

---

# 10. Persona Versioning

Versioning is recommended even for the MVP if implementation cost is reasonable.

Example:

```text
Article 11 — Standard Employee

v1
Age 56
Service 21.5

v2
Age 57
Service 22.5
```

Old sessions must continue to point at the old version.

A session should never unexpectedly change because the current profile was edited later.

### Example

```text
Session A → Persona v1
Session B → Persona v2
```

This improves:

- reproducibility
- debugging
- auditability
- calculation comparison
- regression testing

---

# 11. Primary Persona

During signup, the user selects one profile as the default.

This becomes the **Primary Profile**.

Example:

```text
Primary Profile
Article 11 · Standard Active Employee
Age 56 · 21.5 years service
```

When the user clicks **New Chat**, the primary profile is automatically loaded.

The user should not need to re-select it every time.

---

# 12. When Is Something a New Persona?

This is one of the most important product rules.

## Not a new persona

A temporary hypothetical change:

```text
What if I retire at 59?
What if I work one more year?
What if my salary is $80,000?
What if I have 25 years of service?
```

These are scenarios.

## New profile/persona

A reusable, persistent employee profile:

```text
Create another employee example
Create a separate Article 11 profile
Create an Article 12 profile
Create a second long-term planning case
Save this customized configuration for repeated use
```

The user must explicitly choose **Save as New Profile** for a modified profile to become persistent.

---

# 13. Changing the Article

Article should be immutable for a saved profile.

Do not provide a generic selector like:

```text
Article: [Article 11 ▼]
```

inside the normal profile editor.

Instead show:

```text
Article 11
Locked for this profile.

To work with another Article, create a new profile.
```

To create an Article 12 profile from an Article 11 profile:

```text
Create Profile
      ↓
Choose Article 12
      ↓
Choose template / copy existing profile
      ↓
Map compatible fields
      ↓
Do not copy incompatible Article-specific fields
      ↓
Validate
      ↓
Create new profile
```

The application should explicitly inform the user when fields could not be copied because they do not apply to the target Article.

---

# 14. Creating a Custom Profile

Custom creation should not begin from an empty form by default.

Offer:

```text
Create Profile

○ Start from recommended preset
○ Copy an existing profile
○ Start blank
```

Recommended preset should be the primary choice.

## Recommended flow

```text
Choose Article
      ↓
Choose starting profile
      ↓
Review
      ↓
Edit allowed fields
      ↓
Validate
      ↓
Choose:
  [Use for this chat]
  [Save as new profile]
```

This separates temporary customization from persistent profile creation.

---

# 15. Profile Presets

The purpose of presets is to reduce repetitive data entry because the project currently does not have real employee data.

Presets should be **carefully engineered synthetic profiles**, not random values.

A useful initial target is a set of representative profiles covering legally meaningful scenarios and edge cases.

Examples conceptually:

### Article 11

```text
Standard Active Employee
Near Retirement
Long-Service Employee
Complex / Multi-Path Case
```

### Article 12

```text
Standard Employee
Near Retirement
Long-Service Case
Complex Case
```

Do not create final presets until the relevant Article has sufficient legal/calculation support.

The same synthetic profiles should eventually be useful for:

- product demos
- user testing
- regression tests
- automated calculation tests
- legal reasoning tests

---

# 16. Persona Creation UI

## Screen: Create Profile

```text
┌────────────────────────────────────────────────────────────┐
│ Create Profile                                              │
│                                                            │
│ Step 1 — Choose Pension Article                            │
│                                                            │
│ Available                                                  │
│                                                            │
│ [ Article 11 ]   [ Article 12 ]   [ Article 2 ]           │
│ [ ... ]                                                     │
│                                                            │
│ Coming Soon                                                │
│                                                            │
│ [ Article 1A ]                                             │
│ [ Article 22A ]                                           │
│ [ Article 22B ]                                           │
│ [ Article 22C ]                                           │
│                                                            │
│                                           [Continue →]     │
└────────────────────────────────────────────────────────────┘
```

Articles not fully supported should not appear selectable.

A “Coming Soon” state can indicate corpus/support status without implying that a usable pension calculation system already exists for that Article.

---

# 17. Profile Template Selection UI

```text
┌────────────────────────────────────────────────────────────┐
│ Article 11                                                 │
│ Choose a starting profile                                  │
│                                                            │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ Standard Active Employee                               │ │
│ │ Age 56 · 21.5 years service                            │ │
│ │ Salary history available                               │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                            │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ Near Retirement                                        │ │
│ │ Age 59 · 28 years service                              │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                            │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ Long-Service Employee                                  │ │
│ │ Representative long-service case                       │ │
│ └────────────────────────────────────────────────────────┘ │
│                                                            │
│ [← Back]                                      [Continue]  │
└────────────────────────────────────────────────────────────┘
```

---

# 18. Profile Review UI

```text
┌────────────────────────────────────────────────────────────┐
│ Review Your Profile                                         │
│                                                            │
│ Article 11                                                 │
│ Standard Active Employee                                   │
│                                                            │
│ PERSONAL                                                   │
│ Date of Birth                 ...                           │
│ Age                           56        Derived             │
│ Employment Status             Active                        │
│                                                            │
│ SERVICE                                                     │
│ Creditable Service            21.5 years                    │
│ Prior Service                 0                             │
│                                                            │
│ COMPENSATION                                                 │
│ Current Salary                $69,956                       │
│ Salary History                Available                     │
│                                                            │
│ CONTRIBUTIONS                                               │
│ Employee Contributions        $180,000                      │
│ City Contributions            $360,000                      │
│                                                            │
│ [Edit]                                      [Use Profile]  │
└────────────────────────────────────────────────────────────┘
```

Use compact provenance indicators when useful:

```text
PROFILE
DERIVED
USER
SCENARIO
```

---

# 19. Profile Management UI

The user-facing terminology should preferably be **Profiles** or **Employee Profiles** rather than “Personas”.

“Persona” can remain the internal technical term.

## My Profiles

```text
┌─────────────────────────────────────────────────────────────┐
│ My Profiles                                                │
│                                                             │
│ ★ PRIMARY                                                   │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Article 11                                              │ │
│ │ Standard Active Employee                                │ │
│ │ Age 56 · 21.5 years service                             │ │
│ │                                                         │ │
│ │ [Start Chat] [Edit]                                     │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ OTHER PROFILES                                             │
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Article 11 · Near Retirement                            │ │
│ │ Age 59 · 28 years                                       │ │
│ │ [Start Chat] [Edit]                                     │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Article 12 · Standard Employee                          │ │
│ │ Age 58 · 25 years                                       │ │
│ │ [Start Chat] [Edit]                                     │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ [+ Create Profile]                                         │
└─────────────────────────────────────────────────────────────┘
```

---

# 20. Profile Editing

The profile editor should render fields dynamically from the selected Article schema.

```text
┌────────────────────────────────────────────────────────────┐
│ Edit Profile                                                │
│                                                            │
│ Article 11 🔒                                              │
│ Standard Active Employee                                   │
│                                                            │
│ PERSONAL                                                   │
│ Date of Birth                 [ ... ]                      │
│ Employment Status             [ Active ▼ ]                 │
│                                                            │
│ SERVICE                                                     │
│ Creditable Service            [ 21.5 ] years               │
│ Prior Service                 [ 0 ] years                  │
│                                                            │
│ COMPENSATION                                                 │
│ Current Salary                [ $69,956 ]                   │
│ Salary History                [ Available ]                │
│                                                            │
│ ADVANCED INFORMATION                                        │
│ [ Show more ]                                             │
│                                                            │
│ [Cancel]                                    [Save Changes] │
└────────────────────────────────────────────────────────────┘
```

The form should not expose fields that are irrelevant to the Article.

---

# 21. Saving Profile Changes

Saving edits should not mutate old conversations.

Display a small confirmation:

> Saving these changes will create a new profile version. Existing conversations will remain unchanged. New conversations will use the updated version.

Possible actions:

```text
[Cancel]
[Save Changes]
```

The saved change can optionally be used immediately by offering:

```text
[Save & Start New Chat]
```

---

# 22. Main Application UI

The application should use a modern three-region layout on desktop:

```text
┌─────────────────────────────────────────────────────────────────────┐
│ TOP BAR                                                             │
├──────────────────┬───────────────────────────────────┬──────────────┤
│ CHAT SIDEBAR     │ MAIN CHAT                         │ CONTEXT      │
│                  │                                   │ PANEL        │
│ + New Chat       │ Messages                         │ Collapsible  │
│                  │                                   │              │
│ Today            │ Rich answers                     │ Evidence     │
│ Chat 1           │ Calculations                     │ Calculation  │
│ Chat 2           │ Scenarios                        │ Profile      │
│ Chat 3           │                                   │              │
│                  │                                   │              │
│ Yesterday        │                                   │              │
│ Chat 4           │                                   │              │
│                  │                                   │              │
│ My Profiles      │                                   │              │
│ Sessions         │                                   │              │
│ Settings         │                                   │              │
└──────────────────┴───────────────────────────────────┴──────────────┘
│                         COMPOSER                                  │
└────────────────────────────────────────────────────────────────────┘
```

The right panel should be hidden by default unless it has useful context.

The interface should not feel like three dashboards.

The center chat remains dominant.

---

# 23. Main Chat: Default State

The empty authenticated chat should be similar in spirit to ChatGPT/Claude.

```text
┌───────────────────────────────────────────────────────────────┐
│ Illinois Pension Assistant       Article 11 · Profile ▼      │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│                                                               │
│                     How can I help?                           │
│                                                               │
│        Ask about your pension in plain English.              │
│                                                               │
│   ┌────────────────────────────────────────────────────────┐  │
│   │ Ask about eligibility, benefits, calculations...      │  │
│   │                                                  ➤     │  │
│   └────────────────────────────────────────────────────────┘  │
│                                                               │
│   Try asking                                                 │
│                                                               │
│   “Can I retire at 56?”                                     │
│   “What does Section 11-130 mean?”                         │
│   “What would my pension be at age 60?”                    │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

Avoid a giant profile form on the home screen.

The user should be able to ask a question immediately.

---

# 24. Top Bar

Recommended structure:

```text
┌───────────────────────────────────────────────────────────────┐
│ ☰  Illinois Pension Assistant   Article 11 · Standard Employee │
│                                      Profile ▼       Jaffer ▼ │
└───────────────────────────────────────────────────────────────┘
```

Functions:

- sidebar toggle
- product logo/name
- current profile selector
- account menu

The current Article/profile should be small and secondary.

---

# 25. Profile Selector

Clicking the active profile opens:

```text
┌───────────────────────────────────────┐
│ CURRENT PROFILE                       │
│                                       │
│ Article 11                            │
│ Standard Active Employee              │
│ Age 56 · 21.5 years                   │
│                                       │
│ View Profile                          │
│                                       │
│ SWITCH PROFILE                        │
│                                       │
│ ● Standard Active Employee            │
│   Article 11 · Primary                │
│                                       │
│ ○ Near Retirement                     │
│   Article 11 · Age 59                 │
│                                       │
│ ○ Standard Employee                   │
│   Article 12 · Age 58                 │
│                                       │
│ ───────────────────────────────────── │
│ + Create Profile                      │
│ Manage Profiles                       │
└───────────────────────────────────────┘
```

---

# 26. Profile Switching

Selecting another saved profile should never silently continue the current conversation.

Show:

```text
Switch Profile?

Your current conversation is tied to:
Article 11 · Standard Active Employee

Switching profiles will start a new conversation.
Your current conversation will remain unchanged.

[Cancel]       [Start New Chat]
```

This behavior protects legal context and session reproducibility.

---

# 27. Chat Sidebar

The sidebar should follow familiar AI chat conventions.

```text
┌───────────────────────┐
│ + New Chat             │
│                       │
│ Today                 │
│ Retirement eligibility│
│ Pension estimate      │
│ Understanding §11-130 │
│                       │
│ Yesterday             │
│ Salary definition     │
│ Service credit rules  │
│                       │
│ ────────────────────  │
│ My Profiles            │
│ Sessions               │
│ Settings               │
└───────────────────────┘
```

Chat titles can be generated automatically from the conversation.

Do not require users to manually name sessions.

A secondary line can show context:

```text
Pension estimate at age 59
Article 11 · Scenario
```

---

# 28. Sessions Page

The Sessions page can provide a more searchable/history-oriented view than the sidebar.

```text
Sessions

Today
────────────────────────────────────────────
Retirement eligibility
Article 11 · Standard Active Employee

Pension estimate at age 59
Article 11 · Scenario

Yesterday
────────────────────────────────────────────
Understanding §11-130
Article 11 · Standard Active Employee
```

Clicking a session restores:

- messages
- profile snapshot/version
- scenario state
- source references
- calculation results

It must not automatically replace the current profile with the historical profile.

---

# 29. Session vs Profile

The UI should make the distinction clear.

### Profile

> Who/what is the employee case?

Example:

```text
Article 11
Age 56
Service 21.5
Salary history
```

### Session

> What conversation are we having?

Example:

```text
“Retirement eligibility”
```

### Scenario

> What are we temporarily changing?

Example:

```text
Retire at 59
```

This distinction should be preserved in both the data model and UI.

---

# 30. Scenario UX

Scenarios should be created naturally from conversation.

User:

> “What if I retire at 59?”

The assistant can create:

```text
Scenario created
Retirement age: 59

Base profile remains unchanged.
```

Above the composer:

```text
Article 11 · Standard Employee
Scenario: Retire at 59   ×
```

The user can dismiss the scenario:

```text
Scenario: Retire at 59   ×
```

The base profile remains unchanged.

---

# 31. Scenario Chaining

Multiple hypothetical changes can coexist inside a scenario.

Example:

```text
Base Profile
Age = 56
Service = 21.5
Salary = $70,000

Scenario
Age = 59
Service = 24.5
Salary = $80,000
```

The scenario should explicitly show which fields were overridden.

```text
Scenario: Retire at 59

Changed from profile
Age           56 → 59
Service       21.5 → 24.5
Salary        $70,000 → $80,000
```

This is more transparent than silently replacing values.

---

# 32. Saving a Scenario as a New Profile

Users should have an explicit action:

```text
Save as New Profile
```

Example:

```text
Scenario: Retire at 59

[Reset Scenario]
[Save as New Profile]
```

If saved:

```text
New profile created:
Article 11 · Retire at 59

[Continue Chat]
```

This prevents every scenario from becoming a persistent profile.

---

# 33. New Chat Behavior

For the primary profile:

```text
+ New Chat
      ↓
Fresh session
      ↓
Primary profile automatically loaded
```

No wizard should appear.

For selecting a different profile:

```text
New Chat ▼

Primary Profile
Article 11 · Standard Employee

Other Profiles
Article 11 · Near Retirement
Article 12 · Standard Employee

+ Create Profile
```

---

# 34. Chat Composer

The composer should feel polished and familiar.

```text
┌─────────────────────────────────────────────────────────────┐
│ Ask anything about your pension...                          │
│                                                             │
│ +                                               ● Send       │
└─────────────────────────────────────────────────────────────┘
```

When a scenario is active:

```text
Scenario: Retire at 59 ×

┌─────────────────────────────────────────────────────────────┐
│ Ask a follow-up question...                                 │
│                                                   ● Send     │
└─────────────────────────────────────────────────────────────┘
```

The composer should support:

- text entry
- multiline input
- send
- cancel generation
- keyboard shortcuts
- pasted long questions
- graceful mobile behavior

---

# 35. Suggested Prompt Chips

On the empty chat screen, contextual starter prompts can be shown.

Examples:

```text
Understand eligibility
“Can I retire at 56?”

Explore the law
“What does §11-130 mean?”

Calculate
“Estimate my pension at age 60.”

Compare
“Compare retiring at 56, 57, 59 and 60.”
```

These should disappear or become less prominent once the conversation begins.

---

# 36. Normal Legal Answer UI

For a standard legal question:

```text
§11-116 — Definition of Salary

Section 11-116 defines salary for pension purposes and
addresses compensation payable for normal full-time service,
subject to the exclusions and related rules stated in the law.

Related provisions
§11-217
§11-170.1

[View Sources]
```

Do not turn every answer into a large card.

Use cards selectively.

---

# 37. Evidence UI

Legal trust is a primary product feature.

Source details should open in a side panel or drawer rather than navigating away from chat.

```text
┌─────────────────────────────────────────────┐
│ Evidence                                 ×  │
├─────────────────────────────────────────────┤
│ §11-116                                     │
│ Definition of Salary                        │
│                                             │
│ Relevant statutory text                     │
│                                             │
│ [Retrieved provision text]                  │
│                                             │
│ Source                                      │
│ Illinois Pension Code · Article 11          │
│                                             │
│ Related provisions                          │
│ §11-217                                     │
│ §11-170.1                                   │
│                                             │
│ [Open Full Provision]                       │
└─────────────────────────────────────────────┘
```

The source panel should preserve the conversation behind it.

---

# 38. Inline Legal Citations

Citations should be integrated naturally into answers.

Example:

> Section 11-116 defines salary for pension purposes. **§11-116** [View]

Clicking `[View]` opens the evidence panel.

This is preferable to a bibliography dump at the bottom of every response.

---

# 39. Calculation Answer UI

Calculation results should be visually distinct from general prose.

```text
┌─────────────────────────────────────────────────────────┐
│ Pension Estimate                                        │
│                                                         │
│ Estimated annual pension                                │
│ $32,126.78                                              │
│                                                         │
│ Estimated monthly pension                               │
│ $2,677.23                                               │
│                                                         │
│ Legal basis                                              │
│ §11-134(a)                                              │
│                                                         │
│ Key inputs                                               │
│ Age                       56                             │
│ Service                   21.5 years                    │
│ FAS                       $69,956.40                     │
│ Reduction                 11%                            │
│                                                         │
│ [View Calculation]  [View Sources]                      │
└─────────────────────────────────────────────────────────┘
```

Use terminology such as “Estimated” when the result is a scenario/estimate rather than an official pension determination.

---

# 40. Calculation Detail Panel

Clicking **View Calculation** should open a side panel.

```text
┌──────────────────────────────────────────────┐
│ Calculation                               ×  │
├──────────────────────────────────────────────┤
│ Legal basis                                  │
│ §11-134(a)                                   │
│                                              │
│ Inputs                                       │
│ Age                    56                    │
│ Service                21.5 years            │
│ FAS                    $69,956.40            │
│                                              │
│ Formula                                      │
│ FAS × service × rate                        │
│                                              │
│ $69,956.40                                   │
│ × 21.5                                       │
│ × 2.40%                                      │
│ ─────────────                                │
│ $36,097.50                                   │
│                                              │
│ Reduction                                    │
│ 11%                                          │
│                                              │
│ Final estimate                               │
│ $32,126.78                                   │
│                                              │
│ Validation                                   │
│ ✓ Applicable provision                       │
│ ✓ Required inputs                            │
│ ✓ Cross-references                           │
│ ✓ Formula validation                         │
│ ✓ Deterministic execution                   │
└──────────────────────────────────────────────┘
```

The values shown here must come from the actual calculation engine, not manually formatted LLM text.

---

# 41. Calculation Recipe Presentation

The system can expose a user-safe summary of the calculation recipe.

Do not expose hidden model reasoning.

Show:

```text
Calculation method

1. Identify applicable provision
2. Determine required salary basis
3. Determine service basis
4. Apply statutory rate/formula
5. Apply applicable reduction
6. Apply caps/floors if required
7. Produce deterministic result
```

This communicates reliability without exposing chain-of-thought.

---

# 42. Recipe Validation Indicators

For a calculation, a small section can display:

```text
Calculation checks

✓ Legal provision identified
✓ Applicability satisfied
✓ Required inputs available
✓ Cross-references resolved
✓ Formula validated
✓ Deterministic calculation executed
```

For more complex or uncertain cases:

```text
⚠ Additional statutory condition requires evaluation
```

The UI should avoid claiming certainty when the legal engine has not established it.

---

# 43. Input Provenance in Calculations

Important inputs should carry provenance metadata.

Example:

```text
Age
56
PROFILE

Service
21.5 years
PROFILE

Retirement date
July 1, 2026
SCENARIO

FAS
$69,956.40
DERIVED
```

Conceptual provenance categories:

- PROFILE — base employee profile
- USER — directly provided by user
- SCENARIO — hypothetical override
- DERIVED — computed from other data
- LAW — statutory rule

This aligns UI behavior with the backend's evidence/provenance model.

---

# 44. Missing Input UI

The assistant should never invent missing facts merely to complete a calculation.

Example:

```text
I can determine the applicable retirement provisions, but I
cannot complete a reliable estimate yet.

Missing information:

• Salary history needed to determine the applicable salary basis
• Exact withdrawal date needed to determine the reduction period

[View Required Information]
```

The user should still be able to continue asking legal questions.

---

# 45. Ambiguous Input UI

Example:

> “I am 56.”

If a precise reduction period is required:

```text
I can evaluate the age-related eligibility, but I cannot
calculate the precise reduction from age alone.

I need either:
• the withdrawal/retirement date, or
• the exact age at withdrawal.
```

Do not infer a precise number such as “44 months” from a rounded age unless the underlying dates actually establish it.

---

# 46. Competing Provision UI

This is especially important for Article 11 cases where multiple statutory pathways may need to be evaluated.

Example:

```text
Multiple provisions require evaluation

§11-130
Age/service conditions appear relevant.

§11-134
Additional retirement/minimum-annuity conditions appear relevant.

I need to determine which provision controls, is alternative,
or requires an election before calculating a benefit.

[View Provisions]
```

Do not let the UI imply that the system can simply select whichever formula produces the larger pension.

A legal election must be represented as an election when the statute actually provides one.

---

# 47. Scenario Comparison UI

When the user asks for multiple retirement ages:

```text
Retirement comparison

                 Age 56   Age 57   Age 59   Age 60
────────────────────────────────────────────────────
Service          ...      ...      ...      ...
Annual Benefit   ...      ...      ...      ...
Monthly Benefit  ...      ...      ...      ...
Reduction        ...      ...      ...      ...
```

Below the table:

```text
Assumptions

• Each scenario applies the stated retirement age.
• Service changes according to the scenario assumption.
• Salary/FAS assumptions are shown explicitly.
• Each scenario is evaluated against applicable provisions.
```

Do not hide future assumptions.

---

# 48. Profile vs Scenario Editing Rules

Recommended behavior:

| User action | Result |
|---|---|
| Ask hypothetical question | Temporary scenario |
| Change one value for a question | Scenario override |
| Reset hypothetical change | Scenario removed |
| Save customized configuration | New saved profile |
| Edit an existing saved profile | New profile version |
| Change Article | Create new profile |
| Switch profile | Start new session |
| Start New Chat | New session using active/default profile |

---

# 49. Profile Switcher and Session Integrity

Every session should know exactly which profile/version it used.

Example:

```text
Session #184
Profile: Article 11 · Standard Employee
Profile Version: v3
Scenario: Retire at 59
```

If the user later changes the profile to v4, Session #184 remains associated with v3.

This protects against historical answer drift.

---

# 50. Session Restoration

When a user reopens an old session:

```text
Session:
“Pension estimate at age 59”

Profile used:
Article 11 · Near Retirement
Version: v1

Scenario:
Retire at age 59
```

The assistant should restore the historical context.

The user can then choose **Continue with current profile** as a new session if they want to work from the latest profile.

Do not silently merge historical and current profile states.

---

# 51. Main Chat Response Types

The UI should support several response forms.

## Normal answer

Conversational text with inline citations.

## Legal explanation

Structured:

```text
Provision
Explanation
Related provisions
Sources
```

## Eligibility result

Structured:

```text
Status
Conditions satisfied
Conditions not established
Relevant provisions
```

## Calculation result

Structured estimate card + calculation details + sources.

## Scenario comparison

Table + assumptions + conclusion.

## Missing information

What is known + what is missing + why it matters.

---

# 52. Do Not Expose Internal Chain-of-Thought

The system may use internal agent reasoning, but the UI should expose only:

- legal basis
- relevant provisions
- concise explanation of why they matter
- calculation recipe summary
- assumptions
- validation status
- source evidence

Do not show:

```text
Internal reasoning:
First I considered X, then Y, then rejected Z...
```

This is unnecessary for the user and makes the interface noisy.

---

# 53. Loading States

Use human-readable high-level stages.

Examples:

```text
Reviewing the applicable Pension Code…
```

Then:

```text
Checking statutory applicability…
```

Then:

```text
Preparing the calculation…
```

Do not display internal node names, framework errors, retriever objects, or raw tool execution details.

---

# 54. Error States

Technical failures should be translated into user-facing language.

Bad:

```text
LangGraph node failed
Pydantic ValidationError
Retriever returned None
```

Good:

```text
I couldn't complete this calculation reliably.

The required legal conditions or inputs could not be established,
so no estimate was produced.
```

---

# 55. Anonymous Landing / Chat Experience

Anonymous users should immediately enter a polished chat interface.

```text
┌──────────────────────────────────────────────────────────────┐
│ Illinois Pension Assistant                    Sign In | Sign Up│
├──────────────────────────────────────────────────────────────┤
│                                                              │
│                   How can I help?                            │
│                                                              │
│ Ask about eligibility, pension rules, or calculations.       │
│                                                              │
│ ┌────────────────────────────────────────────────────────┐   │
│ │ Ask a question...                                     │   │
│ └────────────────────────────────────────────────────────┘   │
│                                                              │
│ Try:                                                         │
│ “What does Section 11-116 define as salary?”                │
│ “What does Section 11-130 apply to?”                        │
│                                                              │
│ This is a temporary session. Sign up to save profiles        │
│ and conversations.                                           │
└──────────────────────────────────────────────────────────────┘
```

Anonymous mode should not display “Choose Persona”.

---

# 56. Signup Flow

Recommended signup:

```text
Create Account
      ↓
Choose Article
      ↓
Choose Starting Profile
      ↓
Review Profile
      ↓
Set as Primary Profile
      ↓
Start Chat
```

Keep account signup minimal.

The user should not be forced to manually enter authoritative employee/pension facts because the current product uses synthetic profiles.

---

# 57. Profile Setup During Signup

After account creation:

```text
Let's set up your pension profile

Which pension Article applies to you?

[Article 11 ▼]

Choose a starting profile

○ Standard Active Employee
○ Near Retirement
○ Long-Service Employee
○ Custom

[Continue]
```

Then show a review before creating the primary profile.

---

# 58. Article Availability UI

The application should distinguish:

```text
Ingested
Retrievable
Supported for reasoning
Supported for calculation
Profile-ready
```

These capabilities should be configurable rather than inferred entirely from ingestion.

For example:

```text
Article 11
✓ Available
✓ Profile presets
✓ Calculation

Article 22A
Coming soon
```

Once Article 22A is ingested and sufficiently supported, it becomes selectable without redesigning the profile system.

---

# 59. Article Profile Schema Configuration

The backend should own the Article schema.

Conceptual API:

```text
GET /articles
GET /articles/{article_id}
GET /articles/{article_id}/profile-schema
GET /persona-templates?article=11
```

The frontend should not contain a massive hand-coded Article-to-fields switch.

Instead:

```text
Article configuration
       ↓
Profile schema
       ↓
Frontend form renderer
       ↓
Validation
```

This makes the application extensible across the remaining Articles.

---

# 60. Recommended Article Configuration Shape

Conceptually:

```json
{
  "article": "11",
  "display_name": "Article 11",
  "availability": "available",
  "profile_schema": {
    "sections": [
      {
        "name": "Personal",
        "fields": []
      },
      {
        "name": "Employment",
        "fields": []
      },
      {
        "name": "Service",
        "fields": []
      },
      {
        "name": "Compensation",
        "fields": []
      },
      {
        "name": "Contributions",
        "fields": []
      }
    ]
  }
}
```

The actual fields must be defined from the legal/calculation requirements of that Article.

Do not blindly expose every concept appearing in the statutory text.

---

# 61. Persona Fields Should Be Driven by Use Cases

For each Article, identify:

```text
Eligibility inputs
Calculation inputs
Service inputs
Salary inputs
Contribution inputs
Employment/status inputs
Election inputs
Cross-reference inputs
Historical/tier inputs
```

Then classify each field as:

```text
Required
Optional
Advanced
Derived
Scenario-only
```

This gives the product a principled way to decide what belongs in a profile.

---

# 62. Avoid Overloading the User With Legal Fields

The statute may contain many concepts.

The profile should not become a 50-field government form.

Use progressive disclosure:

```text
Basic Information
Service
Compensation
Advanced Information
```

Only show advanced fields when:

- the Article supports them
- the user chooses to expand them
- a calculation actually needs them

---

# 63. User-Provided Facts vs Profile Facts

The backend should distinguish provenance.

Example:

```text
Profile:
Service = 21.5
Source = synthetic_profile
Authority = profile
```

If the user says:

> “For this scenario, use 23 years.”

do not overwrite the base profile.

Represent:

```text
Scenario:
Service = 23
Source = user_scenario
Authority = hypothetical
```

This allows both the base profile and hypothetical scenario to coexist safely.

---

# 64. Persona Does Not Store Pension Results

Do not store:

```text
Annual Pension = $32,126.78
```

as a permanent persona field.

That is an output derived from:

```text
Profile + Scenario + Law + Recipe + Calculation Engine
```

Store calculation results with the session/scenario/calculation record instead.

This prevents stale pension results from becoming part of the employee profile.

---

# 65. Legal Engine Relationship to Persona

The profile supplies facts.

It does not decide which law applies.

Correct architecture:

```text
Profile Facts
      ↓
RAG / Retrieval
      ↓
Candidate Provisions
      ↓
Applicability Evaluation
      ↓
Cross-reference Resolution
      ↓
Dynamic Recipe Derivation
      ↓
Recipe Validation / Guardrails
      ↓
Deterministic Calculation
      ↓
Answer + Evidence + Provenance
```

Do not create profile templates that effectively encode statutory formulas.

---

# 66. Competing Provisions and UI Trust

The UI must support cases where multiple legal pathways need evaluation.

The assistant should be able to say:

```text
I found multiple potentially relevant statutory pathways.

I am evaluating their conditions before calculating the benefit.
```

This is especially important for pension law because the correct result may depend on:

- age
- service
- employment status
- withdrawal date
- statutory elections
- historical/tier rules
- cross-referenced provisions
- salary definitions
- service-credit rules

The UI should not oversimplify these into “pick the biggest number”.

---

# 67. Profile Switching Warning

The warning must be clear but not annoying.

Recommended copy:

> **Switching profiles will start a new conversation.** Your current conversation and calculations will remain unchanged.

Actions:

```text
[Cancel] [Start New Chat]
```

---

# 68. Profile Editor Warning

Recommended copy:

> **Saving these changes creates a new profile version.** Existing conversations continue using the version they were created with.

This explains versioning without exposing backend mechanics.

---

# 69. Mobile UI

The desktop experience can be:

```text
Sidebar | Chat | Context Panel
```

Mobile should become:

```text
┌───────────────────────┐
│ ☰  Article 11    ▼   │
├───────────────────────┤
│                       │
│ Chat                  │
│                       │
│ Messages              │
│                       │
│                       │
│                       │
│ Composer              │
└───────────────────────┘
```

Evidence and calculation details should appear as bottom sheets or full-screen drawers.

Do not attempt to maintain a three-column desktop layout on mobile.

---

# 70. Visual Design Direction

The visual language should be:

- clean
- premium
- calm
- highly readable
- low visual noise
- spacious
- typography-driven
- subtle use of borders and surfaces
- strong hierarchy for important numeric results

Take interaction inspiration from ChatGPT and Claude rather than trying to invent a new chat metaphor.

The pension-specific differentiation should come from the content and intelligence, not decorative UI.

Avoid excessive:

- gradients
- floating cards everywhere
- badges for every field
- giant dashboard widgets
- overuse of colors
- persistent legal technical panels

---

# 71. Card Usage

Not every assistant response should be a card.

Use cards for:

- pension estimates
- eligibility summaries
- scenario comparisons
- important validation summaries

Use normal conversational typography for:

- explanations
- definitions
- legal interpretation
- follow-up responses

This makes the important information visually meaningful.

---

# 72. Context Panel Behavior

The right-side panel should be contextual.

Potential panel modes:

```text
PROFILE
EVIDENCE
CALCULATION
SCENARIO
```

Default state:

```text
Hidden / collapsed
```

Open when the user clicks:

- View Sources
- View Calculation
- View Profile
- View Scenario

The panel should never compete with the main chat.

---

# 73. Complete Desktop Interaction Example

```text
┌───────────────────────────────────────────────────────────────────┐
│ ☰  Illinois Pension Assistant   Article 11 · Standard Employee ▼ │
│                                                        Jaffer ▼  │
├───────────────────┬───────────────────────────────────────────────┤
│ + New Chat        │                                               │
│                   │ User                                           │
│ Today             │ I'm 56 with 21.5 years of service.             │
│ Retirement...     │ Which retirement provision applies?            │
│ Pension estimate  │                                               │
│                   │ Assistant                                      │
│ Yesterday         │ I found multiple provisions that require      │
│ §11-130           │ evaluation, including §11-130 and §11-134.    │
│                   │                                               │
│ ───────────────   │ [View provisions]                             │
│ My Profiles       │                                               │
│ Sessions          │ User                                           │
│ Settings          │ Calculate it.                                 │
│                   │                                               │
│                   │ Assistant                                      │
│                   │ ┌───────────────────────────────────────────┐ │
│                   │ │ Estimated Annual Pension                 │ │
│                   │ │ $32,126.78                                │ │
│                   │ │                                           │ │
│                   │ │ §11-134(a)                                │ │
│                   │ │                                           │ │
│                   │ │ [View Calculation] [View Sources]         │ │
│                   │ └───────────────────────────────────────────┘ │
│                   │                                               │
│                   │ Scenario: Retire at 59                       │
│                   │                                               │
│                   │ ┌───────────────────────────────────────────┐ │
│                   │ │ Ask a follow-up question...             │ │
│                   │ └───────────────────────────────────────────┘ │
└───────────────────┴───────────────────────────────────────────────┘
```

When sources are opened, the context panel appears without navigating away.

---

# 74. What the User Should Feel

The intended experience is:

1. **Immediate** — I can ask something without configuring a pension system.
2. **Familiar** — It behaves like a polished AI assistant.
3. **Context-aware** — It already knows my selected synthetic profile.
4. **Safe** — It does not invent missing pension facts.
5. **Transparent** — It can show which statutory provisions support the answer.
6. **Useful** — It can actually calculate instead of refusing everything.
7. **Controlled** — Hypothetical changes do not silently alter my profile.
8. **Reproducible** — Old conversations remain tied to their historical profile version.

---

# 75. Recommended MVP Navigation

Authenticated navigation should be limited to:

```text
New Chat
My Profiles
Sessions
Settings
```

The profile selector is in the chat header.

The chat itself is always the central experience.

---

# 76. Recommended MVP Screen Inventory

| Screen / State | MVP |
|---|---:|
| Landing page | Yes |
| Anonymous chat | Yes |
| Login | Yes |
| Signup | Yes |
| Primary profile setup | Yes |
| Main chat | Yes |
| Chat sidebar | Yes |
| Profile selector | Yes |
| My Profiles | Yes |
| Create Profile | Yes |
| Edit Profile | Yes |
| Switch Profile confirmation | Yes |
| Sessions | Yes |
| Session restore | Yes |
| Scenario state | Yes |
| Evidence drawer | Yes |
| Calculation drawer | Yes |
| Scenario comparison | Yes |
| Missing-input state | Yes |
| Ambiguity state | Yes |
| Calculation validation state | Yes |
| Settings | Basic |
| Advanced admin/editor tooling | No |

---

# 77. Features Explicitly Out of MVP Scope

Do not include initially:

- Anonymous saved sessions
- Anonymous persistent personas
- Anonymous persistent memory
- Real HRIS/pension-system integration
- Employer dashboards
- Advisor dashboards
- Multi-user shared profiles
- Social/community features
- User-authored legal formulas
- User editing of statutory rules
- Persona marketplace
- Complex notification system
- Long-term cross-account memory beyond the authenticated product needs

These can be added later without changing the core chat/profile/session model.

---

# 78. Article Support Roadmap

Current corpus state:

```text
Most of 24 Articles → ingested
1A                  → pending
22A                 → pending
22B                 → pending
22C                 → pending
```

The system should not hard-code these pending Articles into the persona implementation.

Instead:

```text
Article Registry
      ↓
Capability flags
      ↓
Profile Schema
      ↓
Persona Templates
      ↓
UI availability
```

When an Article is ready, enable it through configuration.

---

# 79. Product Architecture Summary

The intended architecture is:

```text
                         ┌─────────────────────┐
                         │        USER         │
                         └──────────┬──────────┘
                                    │
                          ┌─────────┴─────────┐
                          │                   │
                     Anonymous          Authenticated
                          │                   │
                    Temporary Session    Saved Profiles
                          │                   │
                          │             Primary Profile
                          │                   │
                          └──────────┬────────┘
                                     │
                                   CHAT
                                     │
                      ┌──────────────┼──────────────┐
                      │              │              │
                   Profile        Scenario       Session
                   Context        Overrides       State
                      │              │              │
                      └──────────────┼──────────────┘
                                     │
                               RAG / RETRIEVAL
                                     │
                           Candidate Legal Rules
                                     │
                         Applicability Evaluation
                                     │
                         Cross-reference Resolution
                                     │
                           Dynamic Recipe Derivation
                                     │
                           Guardrails / Validation
                                     │
                       Deterministic Calculation Engine
                                     │
                     ┌───────────────┼───────────────┐
                     │               │               │
                   Answer         Evidence       Calculation
                     │               │               │
                     └───────────────┼───────────────┘
                                     │
                                     ▼
                                   CHAT UI
```

---

# 80. Key Non-Negotiable Rules

## Rule 1 — Chat remains the primary interface

Do not turn the MVP into a pension dashboard.

## Rule 2 — Profiles provide facts, not legal rules

Do not encode statutory formulas into persona presets.

## Rule 3 — Article is immutable on a profile

Create a new profile for a different Article.

## Rule 4 — Hypothetical changes are scenarios by default

Do not save every edit.

## Rule 5 — Explicit user action is required to save a customized profile

Use “Save as New Profile”.

## Rule 6 — Switching profiles starts a new session

Do not mix conversations across profiles.

## Rule 7 — Profile edits create new versions

Old conversations remain reproducible.

## Rule 8 — Calculated outputs are not persona fields

Pension results belong to calculation/scenario/session records.

## Rule 9 — Missing information must be explicit

Never invent required pension facts.

## Rule 10 — Multiple legal pathways must remain visible until applicability is resolved

Do not pick a formula simply because it is easy to calculate or produces the largest result.

## Rule 11 — User-facing explanations are not chain-of-thought

Expose evidence, legal basis, assumptions, and calculation steps, not private internal reasoning.

## Rule 12 — Article configuration drives the profile UI

Do not hard-code a universal form for all Articles.

---

# 81. Recommended Implementation Priority

## Phase 1 — Chat Foundation

Build:

- ChatGPT/Claude-like layout
- Sidebar
- New Chat
- Composer
- Streaming responses
- Markdown rendering
- basic session persistence for authenticated users

## Phase 2 — Primary Profile

Build:

- signup
- Article selection
- synthetic preset selection
- profile review
- primary profile
- header profile selector

## Phase 3 — Profile Management

Build:

- My Profiles
- create profile
- copy profile
- edit profile
- profile validation
- profile versioning
- Article-specific schemas

## Phase 4 — Scenarios

Build:

- temporary overrides
- scenario indicator
- reset scenario
- scenario comparison
- Save as New Profile

## Phase 5 — Legal Trust UX

Build:

- inline citations
- evidence drawer
- calculation drawer
- provenance
- validation indicators
- missing-input UI
- ambiguity UI

## Phase 6 — Expansion

As Articles become fully supported:

- enable Article
- define profile schema
- define synthetic templates
- add Article-specific validation
- add Article-specific regression fixtures

---

# 82. Final UX Principle

The product should follow this mental model:

```text
                    CHAT
                     │
          “Just ask your question.”
                     │
                     ▼
                 PROFILE
          “We already know the
           synthetic employee
               context.”
                     │
                     ▼
                SCENARIO
          “Let's temporarily change
              something and see.”
                     │
                     ▼
                PENSION LAW
          “Which provisions actually
               apply here?”
                     │
                     ▼
               CALCULATION
          “Apply the validated recipe
           deterministically.”
                     │
                     ▼
                  EVIDENCE
          “Show me where this came from.”
```

The user should experience this as one continuous conversation, not as separate modules.

The sophistication belongs underneath the interface.

The interface should remain simple.

---

# 83. Final Recommended Product Positioning

The MVP should essentially be:

> **A ChatGPT/Claude-style pension assistant grounded in the Illinois Pension Code, with reusable synthetic employee profiles and scenario-based retirement analysis.**

The user selects their profile once during signup, then interacts primarily through chat.

Profiles are persistent reusable employee cases.

Scenarios are temporary hypothetical changes.

Changing an Article means creating another profile.

Changing a profile creates a new version rather than rewriting old conversations.

Anonymous mode stays intentionally lightweight: temporary session, no saved chat, no persona system.

Legal evidence and calculation detail remain available through contextual panels so that the main interface stays clean.

This provides a strong MVP foundation while remaining compatible with the future state where synthetic profiles are replaced or supplemented by real pension/employee data sources.
