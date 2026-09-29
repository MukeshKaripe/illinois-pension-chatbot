# Employer-First Assistant — Scope

**Date:** 2026-09-29
**Status:** Draft. Scope as stated by the product owner, checked against the current system and the client's PBMS databases. Decisions D1–D4 answered 2026-09-29 (§7). §8.1 sent to the client; the rest of §8 is open.
**Purpose:** Say what changes when the assistant's user becomes an employer rather than a member, what already exists, what is missing, and in what order to build it. Each phase in §5 gets its own spec before any code.

**Current focus: MEABF (Article 8) only.** Until the client answers §8, all data work targets the MEABF database (`PbsMeabDev`) and its employees and employers. The statute corpus stays whole — all Articles are ingested — but only MEABF's data is used. The other three funds follow once the questions are answered.

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
- **Same names are the normal case, not an edge case.** Among *active* City and Board of Education members, 34,383 people share an exact first and last name with a colleague at the same employer (7,933 name groups). Either the names are masked in this copy (§8.4, question 16) or disambiguation (§4.2) is on the main path of most turns.
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

## 5. Phases

Each phase gets its own spec. Phase 1 blocks almost everything; 2 and 3 can overlap once 1's data model is fixed.

**Phase 0 — Answers and environment.** Client answers to §8; the remaining resources (Postgres, cluster, registry, secrets store, identity provider, frontend domain, read-only SQL login).

**Phase 1 — Data foundation (MEABF first).**
- New employee record: the shape the agent reads (today built from our `employees` table) built instead from PBMS per turn — PBMS keys, employer, department, title, dates, status, tier (0–3), salary per year, service, reciprocal service. Our `employees` table no longer holds members; persona/account/email/password columns go.
- A **read layer for MEABF only** over `PbsMeabDev`: fixed parameterised queries for one employee (record, salary per year, service, estimates), with the current-row rule, hire-date fallback, status-code mapping (including Debit 1–9 and blanks), the agreed service figure, the field allow-list and sentinel-year filtering applied in one place. Fails loudly on an unmapped code rather than guessing.
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

25. **What do statuses "Debit 1" to "Debit 9" mean?** They cover 140,470 employee rows — more than Active and Inactive together. Which are retirees, which are deferred or vested inactive members, which are refunded or withdrawn?
    *Why:* status decides whether a figure is a projection, a benefit in payment, or nothing. *If unanswered:* those employees are shown as "status not mapped" and no figure is worked for them.
26. **58,089 employee rows have a blank status.** What are they, and should they be visible to employers at all?
27. **Hire date is missing on 49% of rows (4,633 active members).** Is `First_Payroll_Date` the correct substitute, or is membership/participation date (`EligibleMemberDate`) the right date for service and tier?
28. **Tier 0 and Tier 3.** What does Tier 0 mean (5,045 rows), and which statutory provision governs Tier 3 (32,237 rows; PBMS settings: minimum age 60 with 10 years, unreduced at 65)?
29. **Service credit.** Per-year service is stored for only 8,688 people (`T_SerSalByYear`). Which figure is authoritative for everyone else — the latest estimate's `MEAB_Svc`, a stored procedure that computes it on demand, or should we derive it from payroll following PBMS's rules?
30. **Several employee rows per person** (57,942 people). Which row is current — the latest `Effective_Date`, the one with status Active, or another rule? When a person has rows under two employers (e.g. City then Board of Education), which employer "owns" them for access purposes?
31. **Employer codes `O`, `D` and `X`** have no description (3,799 rows between them). What are they?
32. **Names:** 34,383 active City and Board of Education members share an exact first and last name with a colleague at the same employer. Is this copy masked? If it is, can we have a masked copy that keeps realistic name distributions, so name lookup can be tested honestly?
33. **Employer users within MEABF:** is a user an employer as a whole (City, Board of Education), a department (`T_Departments`), or a timekeeper (`Timekeeper` on departments and titles)?

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
