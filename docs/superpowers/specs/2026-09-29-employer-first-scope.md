# Employer-First Assistant — Scope

**Date:** 2026-09-29
**Status:** Draft. Scope as stated by the product owner, checked against the current system and the client's PBMS databases. Decisions D1–D4 answered 2026-09-29 (§7). §8.1 sent to the client; the rest of §8 is open.
**Purpose:** Say what changes when the assistant's user becomes an employer rather than a member, what already exists, what is missing, and in what order to build it. Each phase in §5 gets its own spec before any code.

**Current focus: MEABF (Article 8) only.** Until the client answers §8, all data work targets the MEABF database (`PbsMeabDev`) and its employees and employers. **The statute corpus is the client's document plus what it references, from our side** (below) — not all Articles. The other three funds follow once the questions are answered.

### Statute corpus for the MEABF focus

- **Article 8 from the client's document**, *Illinois Pension Law MEABF.docx*. Compared word for word with our ILGA `article_8.txt` (2026-09-29): 99.7% identical, **every word of the client's text is in ours**, and ours additionally carries one pending-amendment variant of §8-230.1 ("before amendment by P.A. 104-457", 267 words) plus footnote asterisks. The newest Public Act the client's copy cites is 104-392; ours cites 104-457. Our ILGA copies (downloaded 2026-09-08) are therefore at least as current as the client's.
- **Articles 1 and 20 in full, from our ILGA copies.** Article 8 cites §1-103.1, §1-109, §1-113 and — the one that matters most — **§1-160, which governs MEABF's Tier 2 and Tier 3** (about 48,000 current members); and §20-122 for reciprocal service. Both are cross-cutting Articles, and §1-160 itself leans on the rest of Article 1, so the whole Article is ingested rather than the cited sections alone; the per-turn boundary (§4.4) still decides what may be read.
- **Only the cited sections of other funds' Articles, from our ILGA copies:** §2-117.1, §5-233.1, §5-234, §7-139, §9-121.1, §9-121.11, §9-128.1, §12-127.1, §14-108.2a, §14-108.2b, §18-112, §19-101 (12 sections across 8 Articles). One hop, not whole Articles: ingesting another fund's entire Article would put its law into every MEABF search, which is what the Article boundary exists to prevent.
- **Other Acts, never ingested:** Article 8 also cites §3-101 *of the Code of Civil Procedure*, §21-30 *of the Property Tax Code* and §34-60.1 *of the School Code*. Same numbers, different laws — 40 ILCS 5/3-101 is the police fund. An earlier version of this list counted §3-101 and §21-30 as Pension Code sections (14 across 10 Articles); the final review of the ingestion branch caught it, and ingestion now reads the citation's own words to tell them apart.
- Question 34 becomes a confirmation: that the client is content with ILGA's public text for Articles 1, 20 and the cited sections.

## 1. Summary

The assistant today is built for **one member asking about themselves, with one record per conversation**: they sign up, pick a persona, and every answer is addressed to "you". The new scope is **an employer asking about many employees, whose records come from the client's pension administration system (PBMS), inside a hard boundary around one Article of the Pension Code, with nothing kept after the chat closes.**

The core carries over intact: the agent loop, the statute retrieval, the deterministic calculation engine and the post-generation verification. What changes is who is speaking, whose record is in play, where the records come from, and where the boundaries are enforced. Three items reshape the core rather than extend it — many employees per conversation (§4.2), the Article boundary (§4.4), and the latency goal (§6).

## 2. The scope as stated

1. **Employer-first.** The employer interacts with the assistant, not the employee.
2. **The employer can access any information about their employees and ask about any of them.**
3. **Single-session chat with short-term memory.** When the chat closes, its history is gone. Persistent history is out of scope for now and may be revisited.
4. **Article boundary.** An employer and their employees belong to a particular Article; the employer may use only that Article and the Articles/sections it references, and cannot ask outside it. *Example:* an MEABF employer sees only MEABF employees and asks only about Article 8 and what Article 8 references. — **Assumption**, pending the client's answer on who the employers are (§8.1).
5. **Hypotheticals (estimates) and recommendations are allowed**, but only within the boundary of the user's Article and data.
6. **A general intelligent assistant with a calculation capability**, used wherever the agent judges it useful or when asked.

**Goal:** time to first token under 3 seconds; under 7 seconds for reasoning-heavy turns, with good perceived latency; no regression elsewhere.

**Open question raised with the scope:** two employees with the same name — resolved in §4.2.

## 3. What we are working with

### 3.1 The current system (carries over)

- Agent loop: `triage → fetch_employee_record → investigate → compose`, six tools, budgets (`agent/graph.py`, `agent/loop.py`, `agent/tools.py`).
- Calculation: recipes in `formula_registry`, executed only by the DSL interpreter; the model never does the arithmetic.
- Retrieval over 4,766 indexed statute documents, Articles 1–24, filtered per Article with 1 and 20 cross-cutting.
- Verification: every cited section must have been retrieved; every figure must trace to a calculation, the statute, the record, or the user's messages.
- Latency (in-region, 2026-09-17): **median time to first answer token 6.12s**, of which the `investigate` loop is 4.4–4.9s.

### 3.2 The client's data (read 2026-09-29, structure only — no member rows)

SQL Server 2022 on an Azure VM (Central US). Four PBMS databases, one per fund:

| Database | Fund | Article | Members (rows) |
|---|---|---|---|
| `PbmsLabfDev` | Laborers' (LABF) | 11 | 47,288 |
| `PbmsCeabDev` | County (CEABF) | 9 | 157,008 |
| `PbsMeabDev` | Municipal (MEABF) | 8 | 267,494 |
| `PBMSMWRDDev` | Water Reclamation District | 13 | 8,413 |

- **Everything the member record needs is present**, with one gap: birth date (`T_Person_Info`), hire/termination date, status (19 codes), tier (`Benefit_Tier_Cd`), salary per pay period (`T_Pen_Ded_Detail.Ansal`, 1956–2025), reciprocal service (`T_Reciprocal*`). **The gap: LABF stores no single service-years figure** — PBMS derives it per estimate from pay periods and hours; CEABF (`Regular_Svc`) and MWRD (`Emp_Service`) do store one.
- **LABF, CEABF and MWRD share one layout; MEABF is a different version** (different table and column names throughout) and needs its own mapping.
- **LABF has a Tier 3** (from 2017-07-01). Our schema accepts tiers 1 and 2 only, and recipes are keyed by tier.
- **`T_Intf_Estimate` holds PBMS's own pension estimates** (21,297 for LABF: final average salary, service, discount, member and spouse pension). Not a source for our figures — a check on them (§5, Phase 6).
- **`T_Employer` is not the employer.** It lists pension systems (for reciprocal service). The employing body is `Emp_Type_Cd` (City, Board of Education, Park District, retirement boards, Union) plus department (`T_Depts`, 1,592 in LABF).
- **No employer-facing sign-in exists in any PBMS database** — only internal staff users (`T_Security_Users`; MEABF uses Active Directory). The other five databases on the server (`Cas*`, `LmccDbDev`, `safetydriven`) are a separate union-dues/contractor system with its own ASP.NET Identity sign-in.
- Sensitive columns are present (`T_Person_Info.Ssn`, bank, tax, beneficiaries). The databases are named "Dev" but the volumes look like production.

### 3.3 MEABF in detail (the current focus)

Read 2026-09-29: structure, lookup codes and aggregate counts only.

- **Employers are explicit.** `T_Employees.ER_Type_Cd` → `T_Employer_Types`. Employee rows per employer: Chicago Board of Education 125,565; City of Chicago 114,675; Chicago Housing Authority 54; Public Building Commission 74; MEABF's own staff 251; unions 63; plus inactive or blank codes (`O` 3,318, `D` 479, Aldermanic Plan 11). Departments (`T_Departments`, 7,227) and titles (`T_Titles`, 9,649) each belong to one employer — Board of Education 5,949 departments, City 1,214. **This is the natural scoping key for an employer user.**
- **Member record mapping:**
  - Birth date: `T_Person_Info.Birth_Date` (missing on 17,922 rows).
  - Hire date: `T_Employees.Hire_Date` — **missing on 49% of rows (132,016), including 4,633 of 44,138 active members.** `First_Payroll_Date` is present for every active member and is the likely fallback (§8.7).
  - Status: `Employee_Status_Cd` (22 codes) plus a sub-status. Active 44,138; Inactive 24,462; **"Debit 1"–"Debit 9" 140,470**; **blank 58,089.**
  - Tier: `Tier_Type_Cd` — **Tier 0 (5,045), Tier 1 (212,415), Tier 2 (16,237), Tier 3 (32,237)**; blank on 1,560.
  - Separation and retirement dates are stored directly (`Seperation_Date`, `Retirement_Date`).
  - Salary: per pay period in `T_Pen_Ded_Detail` (46.7 million rows: `Ann_Sal`, `Gross`, `Capped_Salary`, `YTD_Annual_Salary`).
  - Service and salary per year: `T_SerSalByYear` (regular, paid and unpaid service, best/predominant salary) — **but only for 8,688 people.** PBMS's estimates (`T_Estimate_Detail`: `MEAB_Svc`, `FAS_Yearly`, `Recip_Svc`; 15,794 estimates) cover more.
- **One person, several employee rows:** 267,494 rows for 207,446 people; 57,942 people have more than one row (rehires, several employers). Which row is "current" needs a rule (§8.7).
- **Tier rules (`T_Benefit_Settings_By_Tier`, 2025):** Tier 2 — minimum age 62 with 10 years, unreduced at 67; Tier 3 — minimum age 60 with 10 years, unreduced at 65; both best 8 of the last 10 years, salary cap $127,283.01, 0.5% reduction per month early, spouse 66.67%. Tier 1 — best 4 of the last 10.
- **Names in this copy are masked** (question 32): 312 distinct last names across 44,131 active members, so the apparent same-name frequency (34,383 active members sharing a name with a colleague) is an artefact. Disambiguation (§4.2) stays designed for real collisions, but cannot be tested on this data.
- Sentinel years appear (payroll years up to 2099; service/salary years to 2028) and must be filtered.

## 4. The scope, item by item

### 4.1 Employer-first

- **Carries over:** the loop, calculation and verification are indifferent to who is typing.
- **Changes:** prompts and `compose`'s blocks address "you" and "your record" → third person about the employee under discussion. `coverage.classify_self_reference` reads only first-person frames ("I am a park employee"). Verification's provenance class "the member's own messages" becomes "the user's statements about this employee".
- **Removed:** signup, personas (`personas.py`), profiles, `accounts`, anonymous sessions, the persona picker and profile UI.

### 4.2 Any employee, any information

- **Finding the employee — a `find_employee` tool.** The model calls it; the lookup is deterministic, searches only employees in the user's scope (never `T_Person_Info` directly, which also holds beneficiaries and dependants), and returns candidates with distinguishing attributes.
- **Same name (the open question):**
  - One match → proceed, and name who was picked in the answer ("Maria Lopez, Streets & Sanitation, hired 2004") so a wrong pick is visible.
  - Several → ask, listing department, title, hire year and status. **Never SSN or birth date in a clarification.** Employee number disambiguates cleanly.
  - Once resolved it sticks: "she"/"her" means that employee until the user names someone else — the existing rule that a user must not have to say something twice.
  - Optionally an employee search in the UI, so the choice can be made before typing.
- **Several employees in one conversation.** Today a session binds one member (`sessions.employee_id`, `pinned_record`), and stated values, corrections (`input_overrides`) and scenarios assume that single member. They become keyed per employee, with a "current subject". Touches `state.py`, `facts.py`, `tools.py`, scenarios and the calculation card.
- **"Any information" needs an allow-list.** The agent should see only the fields pension questions need; SSN, bank and tax data never reach it (§8.3).
- **Where the data comes from — PBMS, read live.** Measured against `PbsMeabDev` on 2026-09-29 (server-side time; round trip from the test machine was ~245ms and excluded):
  - one member's record (employee + person): **~5ms**; their salary per year, aggregated from pay periods: **130–360ms** — the paths are indexed (person → employee rows → yearly master → pay-period detail);
  - a name search (no index on names): **~140ms**;
  - a count of active City members by tier: **~140ms**;
  - salary per year for **all** active City members, 2015–2025 — the raw input to "who can retire this year?", before any service or eligibility is worked out: **timed out after 120s.**

  So **every turn reads PBMS live**, through a fixed set of parameterised queries (never model-written SQL), fetched in parallel with the statute search so they add little to the turn. Data is always current, no copy of member records is kept on our side, and there is no sync job. The cleaning rules (current row, hire-date fallback, status mapping, sentinel years) live in that one query layer. The one query shape that does not work live — across all employees — is out of scope (D1). The client's server is small (2 CPUs, 15 GB, shared with eight other databases) and operational, so the query layer only ever runs light, indexed, single-employee reads.
- **Questions across employees are out of scope for now (D1, revised).** "Which of my employees can retire this year?" needs every employee's salary, service and eligibility; fetching just the salary data for active City members ran past 2 minutes, before any eligibility was worked. It would need a nightly precompute by the calculation engine and a `query_employees` tool — deferred until the client confirms they want it (§8.6, question 22). Until then the assistant answers about one employee at a time, and a set question is declined plainly.
- **Same names are frequent in MEABF** (§3.3): disambiguation is on the main path, so the lookup must return distinguishing attributes cheaply and the UI employee search earns its place.

### 4.3 Single session, short-term memory

- The agent still needs memory within a chat (the Postgres checkpointer). **Proposed:** keep it, and delete the conversation's checkpoints and messages when the chat closes or after an idle timeout. An in-process memory store breaks on a second replica or a restart.
- `GET /sessions`, cross-visit history and saved scenarios go.
- **Separate from chat history: an access audit** — which user viewed which employee's data, and when. Usually required for personal data; it holds no conversation content (§8.5).

### 4.4 The Article boundary

- **Partly exists:** retrieval is already filtered to the governing Article plus 1 and 20, and the index records each section's `referenced_sections`, so "the Article and what it references" can be computed ahead of time as an allowed set per Article.
- **Changes — enforced in code at four points, not in prompt text** (the design rule: a paragraph in a prompt is a warning, not a mechanism):
  1. the search filter;
  2. `get_section`, which today fetches any section unfiltered by design;
  3. citation verification, which rejects a citation outside the set;
  4. `triage`, which declines a question about another Article before any model call.
- Today naming another fund's Article *moves* the filter ("read someone else's law"); that behaviour is reversed. The coverage layer (`check_coverage`, clarification) largely retires: the Article comes from the employee's fund, not from what anyone says.
- **References (D2): one hop by default, further only when the question needs it — and "needs it" decided by what was actually read, not by the model's say-so.** The allowed set for a turn starts as the employee's Article, the cross-cutting Articles (1, 20) and the sections the Article cites directly. A section two or more hops away becomes allowed **only when a section already read in this turn cites it**. Reachability through evidence is something code can check; "the query requires it" as a flag the model sets is the kind of guard this system has learned not to trust.
- **The boundary follows the employee under discussion (D2).** For the MEABF focus that is always Article 8. A question with no employee in it (a general statute question) takes the user's own fund.
- **The assumption may not hold.** In Chicago one employer spans several funds — the City employs members of MEABF (8), LABF (11), Police (5) and Fire (6); Board of Education and Park District appear as employer types *inside* LABF's data. If the employer users are organisations like these, the boundary should follow **the employee under discussion**, and a user's allowed Articles are those of the funds their employees belong to. §8.1 settles it.

### 4.5 Hypotheticals and recommendations within the boundary

- **Exists:** scenarios, assumptions, recommendations, the priced "cliff ahead".
- **Changes:** a premise naming another Article ("if she were a park employee…") is adopted today and becomes declined. Small in code; it reverses a deliberate earlier decision.

### 4.6 General assistant with calculation

- **Exists:** the current design.
- **New:** Tiers 0 and 3 (MEABF has both; schema `CHECK (tier IN (1,2))`, `derive_tier`, recipe keys, and recipes for each); Article 13 promoted from `experimental` when MWRD comes into scope; the engine checked against PBMS's own estimates.

### 4.7 Missing or unclear data: ask the user, or ask the client

**The mechanism exists.** When a calculation needs a value the record does not have, `compute_benefit` returns it as a missing input — never a plausible substitute — and the answer names it and asks. What the user states is kept for the rest of the conversation (never asked twice), recorded as the input's origin (`input_sources`), shown on the calculation card, and accepted by verification as provenance from "the user's own messages". A figure resting on a stated value says so ("based on the 22 years of service you gave"), never presenting itself as the fund's official figure. A stated value that contradicts the record overrides it with a note. The existing limits apply: `clarification_budget` (3 questions per conversation) and `assumption_ceiling` (2 assumed values per figure).

**It fits a fact about one employee that the employer knows or can check** — and not a gap in what the data *means*:

| Gap | Resolved by | Why |
|---|---|---|
| Hire date missing (q.27) | **the user**, in chat | HR usually knows it; the engine derives tier from it. |
| One employee's status ("she retired in 2023") | **the user** | A fact about that person. |
| A missing or doubtful salary year | **the user** | Same. |
| Service credit not stored (q.29) | **the user, as a fallback** | See below. |
| What "Debit 1–9" and blank statuses mean (q.25–26) | **the client, once, in code** | Internal PBMS codes an employer has no reason to know; asking would stop every conversation about 140,470 people at the same question. |
| Tier (q.28) | **never asked; taken from the record** | It selects the formula and turns on first participation in *any* reciprocal system, which neither the user nor a hire date reliably shows — ~5,500 MEABF records carry a tier their hire date would contradict. Only when the record has no tier is it derived from dates (`derive_tier`), and the answer says so. |
| Current row, employer ownership, who sees whom (q.30, 31, 33) | **the client, in code** | Access rules; nothing a user types may decide which employees they can see. |

**Service credit (q.29) moves from blocker to dependency.** Without an authoritative figure, the assistant asks for service on most calculations. That works, at two costs: figures are only as good as the employer's number — legal service credit includes purchased service, leaves, transfers and reciprocal service, and "about 22 years" against the fund's 21.4 can change the pension or eligibility — and most calculation turns gain a step. Still worth asking the client; no longer required before building.

**Consequence:** the MEABF read layer is buildable now. Unmapped codes and missing values surface as "not available", missing values the user can supply become questions, and only code meanings and access rules wait on the client. Active Tier 1 and Tier 2 members can be tested end to end meanwhile.

## 5. Phases

Each phase gets its own spec. Phase 1 blocks almost everything; 2 and 3 can overlap once 1's data model is fixed.

**Phase 0 — Answers and environment.** Client answers to §8; the remaining resources (Postgres, cluster, registry, secrets store, identity provider, frontend domain, read-only SQL login).

**Phase 1 — Data foundation (MEABF first).**
- New employee record: the shape the agent reads (today built from our `employees` table) built instead from PBMS per turn — PBMS keys, employer, department, title, dates, status, tier (0–3), salary per year, service, reciprocal service. Our `employees` table no longer holds members; persona/account/email/password columns go.
- A **read layer for MEABF only** over `PbsMeabDev`: fixed parameterised queries for one employee (record, salary per year, service, estimates), with the current-row rule, hire-date fallback, status-code mapping (including Debit 1–9 and blanks), the agreed service figure, the field allow-list and sentinel-year filtering applied in one place. Fails loudly on an unmapped code rather than guessing. **Buildable before the client answers §8.7** (§4.7): gaps become "not available" or a question to the user; the mappings are filled in as answers arrive.
- No sync and no precompute: nothing about members is stored on our side beyond the conversation itself (which is deleted on close).
- The LABF/CEABF/MWRD mapping follows once the client answers §8 — a second mapping into the same model, not a second model.

**Phase 2 — Identity and access.**
- Sign-in through the client's identity provider: validate its tokens, map the subject to a user, read the claims that scope which employees they may see.
- Row-level security re-keyed from account/employee to "user's employee scope".
- Access audit log.
- Ephemeral sessions: deletion on close and on idle.

**Phase 3 — Agent.**
- `find_employee`, per-employee state, the current subject, disambiguation.
- Set questions across employees declined plainly (D1 deferred).
- Employer voice across prompts, blocks, coverage and verification.
- The Article boundary at its four enforcement points; out-of-Article premises declined.
- Coverage layer reduced to what the new scope still needs.

**Phase 4 — Latency** (§6).

**Phase 5 — Frontend.** Employer sign-in, employee search, chat without profiles/personas, calculation card per employee.

**Phase 6 — Verification.**
- New eval suites: employer voice, Article boundary (in and out), several employees in one chat, same-name disambiguation, record-lookup questions.
- The engine against PBMS estimates on a sample per fund and tier; disagreements explained or fixed.
- Full eval suite on `gpt-6-luna` (evals so far ran on `gpt-5.6-luna`).

**In parallel, already specified:** multi-format ingestion from Blob (`2026-09-28-multi-format-ingestion-design.md`); deployment to the client's cluster.

## 6. The latency goal

**Where we are:** median 6.12s to first answer token in-region; `investigate` 4.4–4.9s. One model call is ~1–1.5s and a typical turn makes 2–3 in sequence, so **under 3 seconds allows roughly two model calls before the answer streams.**

**Assessment:**
- **Under 3s is achievable for record lookups** ("what is Maria's hire date?" — one lookup, one model call, ~2s) **and for simple statute questions**, through a fast path that does not enter the full investigation loop.
- **Under 7s is realistic for calculation and multi-step reasoning**, with a progress event ("Looking up Maria's service history…") inside ~1s.
**The rule (D3): TTFT is the first word on the screen, and the 3s target may not be bought with any degradation — accuracy, relevance, citation depth or anything else.** Latency work is therefore constrained to changes that leave answers the same:
- **Make the same work faster:** the fast path for record lookups and simple questions; search before the first model call (already measured: 9.71s → 7.59s with no loss); the employee's PBMS lookups run in parallel with that search; `gpt-6-luna` measured against `gpt-5.6-luna`.
- **Stream sooner, not less:** a first sentence that can be written before the investigation finishes (who the employee is, what is being looked up) — but only where that sentence is factually settled, since the answer check still applies to it.
- **Refused as latency levers:** capping the investigation, dropping evidence completion, or skipping verification. The last latency round measured exactly that trade (citations 12 → 8) and it is ruled out.
- **The gate:** every latency change must hold the eval suite's score and the verification pass rate — a latency win that fails either is not a win.

**D3a (answered 2026-09-29): only answer text counts.** A progress line is still shown — it is what makes a 5–7s turn feel attended — but it does not stop the clock. So under 3s is the target for record lookups and simple statute questions; calculation and multi-step turns aim for the 7s ceiling, since the no-degradation rule rules out cutting their investigation short.

Still to measure: `gpt-6-luna`'s latency on our workload, untested beyond ~1.2–1.4s for a trivial call.

## 7. Decisions

Answered 2026-09-29:

- **D1. Questions across employees — out of scope for now**, pending the client (question 22). First answered "in scope within one Article"; revised the same day after measuring that the live query behind it does not finish in 2 minutes (§4.2). Kept simple: one employee at a time.
- **D2. Article boundary — depends on the employee under discussion and the query: one hop mostly, transitive references only when the query requires it.** Enforced as reachability through sections actually read (§4.4).
- **D3. TTFT is the first word on the screen; no degradation in accuracy, relevance or anything else is an acceptable price for the 3s target.** Consequences in §6.
- **D4. Coverage layer — deferred.** §8.1 is with the client. Meanwhile the focus is MEABF only, so every employee is Article 8 and the coverage determination does not run on the new path.

- **D3a. Only answer text counts toward TTFT**; progress lines are shown but do not stop the clock (§6).

## 8. Questions for the client

Each question says why it matters and what we will assume if it stays unanswered.

### 8.1 Who the users are

1. **Who exactly is an "employer" user?** An HR or payroll person at an employing organisation (City of Chicago, Board of Education, Park District, Cook County, MWRD), a timekeeper for a department, or fund staff at LABF/CEABF/MEABF/MWRD?
   *Why:* decides the Article boundary and which employees each user sees. *If unanswered:* we cannot build Phase 2.
2. **Can one employer user have employees in more than one fund?** For example, a City of Chicago HR user whose staff are in MEABF, LABF, Police and Fire.
   *Why:* if yes, the boundary follows the employee, not the user (§4.4). *Assumed:* yes for City-level users.
3. **Roughly how many employer users, and how many concurrently?**
   *Why:* sizing the cluster, database and model quota.

### 8.2 Sign-in and access scope

4. **Which application's sign-in do these users use today, and where does it live?** No employer sign-in exists in the four PBMS databases — only internal staff users, and MEABF's via Active Directory. The `Cas*` databases hold an ASP.NET Identity sign-in for the union-dues contractor portal; is that it, or is there another system?
5. **Can that system issue tokens our app can validate (OpenID Connect / OAuth2 — e.g. Entra ID, ADFS, or IdentityServer on the ASP.NET app)?** If yes: issuer URL, audience/client id, JWKS URL. If no: how should our app authenticate users — and we would need a read-only connection to the user store, which we would rather avoid.
6. **Which claim or table says which employees a user may see?** By employer type (`Emp_Type_Cd`: City, B/E, Park District…), by department (`T_Depts`), by fund, by an explicit assignment list, or something else? Where is that mapping kept?
   *Why:* this becomes the row-level security rule; it must come from the client's system, never from what the user types.
7. **Are there roles** (e.g. read-only viewer vs. someone allowed to run estimates or see salary detail)?

### 8.3 Data and fields

8. **Which employee fields may the assistant use?** Proposed allow-list: name, employee number, fund, employer type, department, title, hire/termination dates, status, tier, birth date, annual salary history, service credit, reciprocal service, marital status (spouse annuity), and existing PBMS estimates. **Proposed exclusions:** SSN, bank details, tax data, addresses, beneficiary personal details.
9. **For LABF, which service figure is authoritative?** PBMS stores none per member; it derives service per estimate. Is there a view or stored procedure that produces the official figure, or should we use the latest estimate's `Service_Yrs`?
10. **Salary: which figure should the assistant treat as a year's salary** — the annual salary rate (`Ansal`), gross pay, or the capped salary (`Capped_Salary`, Tier 2/3)? And confirm PBMS's final average salary rules match `T_Benefit_Settings_By_Tier` (LABF: best 4 of last 10 for Tier 1, best 8 of 10 for Tiers 2–3).
11. **Status codes:** confirm how the 19 PBMS statuses map to active / inactive / retired, especially `ACAR` (active after retire), `A35`, `RP` (reciprocal) and the `MBR*` codes (member of another fund).
12. **Tier 3 (LABF, from 2017-07-01):** which statutory provision defines it, and does it exist in the other funds?
13. **MEABF uses a different PBMS version.** Is there documentation or a contact for its schema?
14. **Other funds.** Only LABF, CEABF, MEABF and MWRD are on this server. Are other funds' employers (Park, Police, Fire, Teachers) in scope, and if so, where is their data?

### 8.4 Freshness and access

15. *(Withdrawn — answers read PBMS live, so data is always current.)*
16. **Are these "Dev" databases real member data or masked copies, and which environment should the production assistant read from?**
17. **A read-only SQL login scoped to the four PBMS databases (and to the allow-listed columns, ideally via views)** — requested. The `sa` login must not be used by the application.
18. **Network path from the Kubernetes cluster to the SQL Server** (private endpoint/VNet, or the public port 2022 with a firewall rule), and **which Azure region the cluster is in** — every live query pays the round trip between them.
18a. **Is there a read replica we should use instead of the operational server?** Live single-employee lookups are light (under half a second, indexed), but the server has 2 CPUs and 15 GB shared with eight databases.

### 8.5 Records, retention and compliance

19. **Must we keep an access audit** (which user viewed which employee, when) even though chat history is discarded? For how long, and who reviews it?
20. **Is there any requirement to keep conversation content** (complaints, disputes, regulatory review), or is discarding it on close acceptable?
21. **Any compliance regime to meet** (internal security review, data-processing agreement, residency — e.g. must all processing stay in a specific Azure region)?

### 8.6 Behaviour

22. **Questions across employees** ("which of my employees are eligible to retire this year?") — wanted, and how soon? They need a nightly precompute on your server (or a replica) because the live query takes minutes; out of scope until you confirm.
23. **Estimates:** must the assistant's figures match PBMS's official estimates, or is it acceptable for them to be labelled estimates from the statute with differences explained?
24. **Out-of-scope questions:** when a user asks about another fund's Article, is a plain decline acceptable, or should it point them somewhere?

### 8.7 MEABF data (needed now — MEABF is the current focus)

§4.7 says which of these the assistant can instead ask the user in chat. **Status after a final read of the database (2026-09-29):**
- **Answered, nothing to ask:** 26 (empty shells, hidden), 31 (historical codes, hidden), 32 (names masked — a realistic sample requested instead).
- **Largely answered, confirmation needed:** 25 (Debit codes, and whether recent contributions override status), 27 (`First_Payroll_Date` as fallback), 28 (Tier 0 and Tier 3), 30 (current-row pointer, employer ownership), 34 (ILGA text for Articles 1, 20 and cited sections).
- **Needs the client:** 29 (is `sfGetSerSalRegServ` authoritative, and may we call it) and 33 (who an employer user is).
- **Asked of the user in chat meanwhile:** 27 and 29 (§4.7).

**Partly answered by the database itself (2026-09-29).** PBMS documents several of these in its own lookup tables and procedures; the questions below are now *confirm* questions where it does.

25. **Confirm the Debit statuses.** `T_Debit_Types` defines them, and `spBenefitsAnnuity_PostProcess_Update_Debit_Type` sets `Employee_Status_Cd = 'DB' + debit type` when a benefit is processed: DB1 employee annuitant (retired, 53,907), DB2/DB2A death-in-service annuity, DB3 death-in-service refund, DB4 full refund / refund in lieu (64,748), DB5 fixed at age 65, DB6 returned to work within a year, DB7 transfer to permanent file, DB8 closing card for miscellaneous reasons, DB9/J refund or transfer to another fund. *To confirm:* our proposed mapping — DB1 retired (benefit in payment); DB2–DB4, DB8, DB9 closed (no figure); DB5, DB6, DB7 need the fund's reading.
    **Final check (2026-09-29) — status alone is not a reliable "active" signal.** Counting current rows that have contribution records in 2024–2026: DB6 "returned to work" 82 of 85, **DB7 "permanent file" 1,036 of 1,312**, DB4 "full refund" 4,636 of 62,411, DB1 "annuitant" 1,821 of 53,290 — people still or again contributing under a closed-looking status (rehires after a refund, annuitants back at work). `Retirement_Date` is never populated (0 current rows), so it cannot be used. *To confirm:* whether recent contributions should override the status code when deciding that someone is an active member.
    *Why:* status decides whether a figure is a projection, a benefit in payment, or nothing.
26. **Blank status — answered.** Of 58,089 blank-status rows, only 1,222 are anyone's current row (§30); the rest are superseded. Those 1,222 have **no payroll ever, no contribution since 2024, and no benefit record** — most have no dates at all. They are empty shell records and are hidden. *Nothing to ask* unless the client knows otherwise.
27. **Hire date is missing on 49% of rows (4,633 active members).** Checked against the active members who have both: **`First_Payroll_Date` is within a month of `Hire_Date` for 82%** (32,258 of 39,498) and more than a year later for 3,113 (rehires, most likely). **`EligibleMemberDate` is not a substitute** — within a month of hire for 11 of 41,126. *Proposed:* `First_Payroll_Date` as the fallback, labelled as such, or ask the user. *To confirm:* that this is acceptable. (Tier no longer depends on it — q.28.)
28. **Tiers — largely answered by the database (read 2026-09-29).**
    - **Where tier comes from:** PBMS does not derive it. It arrives on the employer's payroll file (`spPayrollInsertT_Employees` takes it as a parameter and turns anything outside 0–3 into `1`); a staff edit that leaves it empty stores `0` (`spPersonInfo_Update_Employee`: `IsNull(@pTier_Type_Cd, '0')`). A code `T` exists and is treated as `3` for contributions (`sfGet_T_Deduction_Law`).
    - **Tier 3 = members under §1-160(c-5), and (d-10) electors.** PBMS gives Tier 3 its own contribution law (`M7`/`F7`, added 2018, ticket #3052), **effective 2017-07-06 with a 9.5% annuity rate against 6.5% for everyone else** — exactly §8-174(a-10): "for each employee subject to subsection (c-5) of Section 1-160, 9.5% of each payment of salary". Of 32,235 current Tier 3 rows, 31,069 were hired on or after 2017-07-06; 968 hired 2011–2017 fit the (d-10) election; 195 hired before 2011 are unexplained. Settings match too (minimum age 60 with 10 years, unreduced at 65).
    - **Tier 0 = tier never set, on closed records.** 5,045 current rows: **none active**, 352 inactive, 4,633 retired/refunded/closed (Debit statuses); 4,332 hired before 2011. For a figure they read as Tier 1 by date; in practice they are mostly benefits already in payment.
    - **No tier: 1,382 current rows**, 475 of them active, 468 of those hired after 2017-07-06 — most likely Tier 3 not yet recorded.
    - **The record's tier and the hire date disagree for about 5,500 current rows** (Tier 1 hired after 2011: 3,298; Tier 2 before 2011 or after 2017-07-06: 1,909). Tier depends on *first participation in any reciprocal Illinois system*, which a hire date at this employer does not show — so **the assistant takes the tier from the record and never re-derives it from the hire date** when one is present (`derive_tier` becomes the fallback for a missing tier only). A second tier column (`CCTier`) agrees with `Tier_Type_Cd` in all but 54 rows, and payroll lines agree with the employee row on 99% of 2025 lines (the rest look like mid-year changes).
    - *To confirm:* Tier 3 = §1-160(c-5) plus (d-10)(i) electors; Tier 0 = "not set"; how to treat the 1,382 with no tier and the 195 pre-2011 Tier 3 rows. The statute text for all of it is §1-160, now ingested from our ILGA copy (question 34).
29. **Service credit.** Per-year service is stored for only 8,688 people (`T_SerSalByYear`). PBMS computes it with `spSerSalByYear`, which *writes* results, so a read-only login cannot call it — but the per-year figure comes from **`dbo.sfGetSerSalRegServ(person, interface, year)`, a scalar function, which is read-only by nature and could be called directly.** *To confirm:* is that function's result the authoritative regular service for a year, and may we call it (performance on the shared server to be measured on a handful of members first)? **Why it matters, measured:** of 44,131 active members, only **3,195 have a PBMS estimate** and **8,083 have stored per-year service** — roughly 33,000 active members have no stored service figure at all.
    *Meanwhile:* the assistant asks the user for service when the record has none, and labels the figure as resting on their number (§4.7).
30. **Current row — answered by PBMS's own pointer.** `T_Person_Info.Active_Employee_Seq_No` names each person's current employee row (set for 207,445 of 222,281 people; the rest are beneficiaries and others), and PBMS's own search joins on it. *To confirm:* that we should use it too, and — for a person whose earlier rows are under another employer — that the employer on the current row is the one whose users may see them. This is common, not an edge case: **26,355 people have rows under two or more employers** (City and Board of Education, mostly).
31. **Employer codes `O`, `D` and `X` — almost entirely historical.** `D` is never a current row and `O` is one (of 3,318); `X` is two. *To confirm:* nothing further unless those three current rows matter; otherwise they are hidden.
32. **Names — answered: this copy is masked.** 44,131 active members share only **312 distinct last names and 332 distinct first names** — synthetic names drawn from small pools, which is also why same-name collisions looked so common (§3.3). *Asked instead:* a masked copy that keeps realistic name distributions, or a small realistic sample, so name lookup and same-name disambiguation can be tested honestly. (It also bears on question 16: at least the names in this copy are not real; whether other fields are masked is still to confirm — plain SSN values are present on every active row.)
33. **Employer users within MEABF:** is a user an employer as a whole (City, Board of Education), a department (`T_Departments`), or a timekeeper (`Timekeeper` on departments and titles)? *Not answerable from the database:* its only user tables (`T_Users`, `T_Active_Directory_Users`, `T_User_Access`) are MEABF's internal staff.
34. **Statute text beyond Article 8.** The client supplied one document, *Illinois Pension Law MEABF.docx* — Article 8 in full (the same 211 sections as ILGA's; text 99.7% identical, see "Statute corpus" at the top), and it is the only statute text in use for now. **But Article 8 cites other Articles: Article 1 twelve times, Article 20 four times, and 14, 2, 9, 5 and others.** Most importantly, **MEABF's Tier 2 and Tier 3 benefits are governed by §1-160 in Article 1** — 16,237 and 32,235 current members, about a fifth of everyone with a current row. With Article 8 alone, the assistant can name those sections but cannot read them, so Tier 2 and Tier 3 figures and reciprocal-service answers (Article 20) are not possible; only Tier 1 members can get a figure. *Proceeding with:* ILGA's public text for Articles 1 and 20 in full and the 12 other cited sections (see "Statute corpus" at the top), since our copies proved at least as current as the client's Article 8. *To confirm:* that this is acceptable, or that the client would rather supply those texts.
35. **How an employer identifies an employee (asked 2026-09-30).** Phase 1 looks an employee up by **`T_Employees.Employee_Id` within their employer** — assumed to be the employer's own payroll/HR employee number. Measured: `Employee_Id` is set for 43,811 of 44,138 active members and nearly unique within an employer (43,767 distinct employer + ID pairs; the few repeats are resolved by PBMS's current-row pointer, and anything still ambiguous is put to the user). **`T_Person_Info.MEAB_ID` is empty on every row**, so it cannot serve. PBMS's internal person number is accepted as well, for administrators. Name search follows in Phase 3, once names are unmasked (q.32). *To confirm:* that `Employee_Id` is the number employers use for their staff, or which number they use instead.
36. **Tier 2/3 pensionable salary cap.** §1-160(b-5) caps the salary that counts ($106,800 from 2011, indexed each year by the lesser of 3% or half of CPI-U); PBMS's `T_Benefit_Settings_By_Tier` holds the 2025 figure, $127,283.01. *To confirm:* that the yearly values in PBMS are authoritative and may be read for each year of a member's salary history.

## 9. Risks

- **The Article-boundary assumption** (§4.4) — if employers span funds, Phase 2 and 3 designs change.
- **Service credit for LABF** — reproducing PBMS's derivation is substantial work and easy to get subtly wrong; an authoritative source from the client is far better.
- **Latency vs. evidence** (§6) — the 3s target may cost citation depth; bound it explicitly.
- **Model change** — `gpt-6-luna` is untested on the eval suite.
- **Personal data at scale** — tens of thousands of real records; allow-list, row-level security, audit and the read-only login are prerequisites, not polish.
- **MEABF data quality** (§3.3) — half the hire dates missing, most statuses unexplained, service stored for few. The query layer must refuse to guess: an unmapped code or a missing required field surfaces as "not available" rather than as a plausible wrong figure, the same principle that starves borrowed inputs today.

## 10. Resource status

| Resource | Status |
|---|---|
| Azure OpenAI — chat `gpt-6-luna`, embeddings `text-embedding-3-large` | Tested 2026-09-28 |
| Azure AI Search | Admin key tested; semantic ranker on, plan to confirm (Standard) |
| Blob storage | Connects; no container yet |
| SQL Server (PBMS) | Reachable; `sa` only — read-only login requested |
| PostgreSQL | Pending |
| Kubernetes cluster, container registry, secrets store | Pending |
| Identity provider | Pending (§8.2) |
| Frontend domain, logging access | Pending |
