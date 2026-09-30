# Phase 1: the MEABF read layer, people in context, and the demo path

**Status:** design approved 2026-09-30. It was revised twice: first so employees are found by first and last name in the chat (never by MEAB ID), then on 2026-10-01 for **several people in one conversation** and **comparisons in one turn**. This written spec awaits review.
**Scope:** `docs/superpowers/specs/2026-09-29-employer-first-scope.md` (Phase 1; D1 "questions across employees within the same article"; client questions 35–38).

## 1. What this builds

An employer names employees in the chat, chooses who is meant when a name is shared, and the chatbot answers from their PBMS records. It can do this for one person at a time or for several side by side:

> "What would KA_LS MA_LP's pension be if they retired at 62?"
> → "There are 9 active employees named KA_LS MA_LP. Which one?" *(a list: department, employer, birth year)*
> *(picks "Streets & Sanitation, born 1964")* → the question is answered for that person, and a chip for them appears in the header.
> "And at 65?" → still about them; the chip stays highlighted.
> "Compare that with QW_XZ PL_MR." → QW_XZ PL_MR is chosen the same way, then both figures come back in one table, each labelled, and both chips are highlighted.

It has three parts:

- **The read layer.** `PbmsProvider` implements the existing `RecordProvider` interface (`src/pension_chat/records.py`) over the client's SQL Server (`PbsMeabDev`), with live, parameterised, read-only queries and no sync.
- **People in context** (§4). A conversation keeps the people chosen in it, at most 4, most recent first, shown as chips in the header.
  - **Who a turn is about** is decided deterministically, before any model call. The next section gives the rules.
  - **A new person joins only when named, and only after the user chooses them.**
  - The chips update the moment a turn's people are decided, before any answer text arrives.
  - Internally each person is keyed by MEAB ID, the one unique identifier. **Users never type or see it.**
- **One loop for several people** (§5). A question about 2–4 people runs **one** agent loop:
  - the law is read once;
  - each record is loaded in parallel;
  - the calculation engine runs once per person (in milliseconds; the model never does the arithmetic);
  - the answer is written once, around a code-rendered comparison table.

  Each person's figure is computed only from that person's own record.

### Success criteria

1. **Naming one employee.** A message naming an active employee produces a choose card. Choosing a person answers the original question for them, and adds them as a chip.
2. **Ambiguous names.** An ambiguous name never selects anyone. The user always chooses. When more than 12 people share the name, the card asks for the department first.
3. **Who a turn is about follows the rules in §4.2.** Naming someone already in the conversation needs no card. A question naming nobody stays with the people of the previous turn. A singular pronoun ("her") when that turn covered several people is asked about, never guessed.
4. **The chips.** They show the conversation's people. The ones the current turn is about are highlighted, and they update before the first answer token. Answers about one person need not repeat the name.
5. **Comparisons.** A question about 2–4 people is answered in one turn with one figure per person, each computed from that person's record and labelled in a code-rendered table. First-token latency is within 1 s of a single-person turn.
6. **Read-only access.** No statement outside the fixed query registry can reach SQL Server, and no query reads a column outside the allow-list (§3.2).
7. **PBMS unavailable.** When PBMS is unreachable or slow, the turn still answers and says whose record could not be read. Nothing falls back to Postgres or invents a value.
8. **Speed.**
   - Checking a message for names costs no database time and no model call.
   - A candidate lookup is one query per unfamiliar name.
   - Records are read in parallel, at most 300 ms in-region.
   - Later turns reuse each pinned record.
9. **Nothing else changes.** Local development, the unit tests and the existing eval cases are unchanged unless `RECORD_SOURCE=pbms`, or unless an eval case declares `people`.

### Not in this phase

- **Search by anything other than first and last name.** No Employee ID, no MEAB ID, no partial names.
- **Employees who are not active members.** The lookup covers `Employee_Status_Cd = 'A'` only.
- **Questions about groups.** For example "everyone in Finance who is Tier 2", or "who retires next year". They need counting across the database rather than reading records, and get their own design.
- **More than 4 people in a conversation.** The least recently used person leaves when a fifth is chosen.
- **Scoping employers to their own employees** (Phase 3, with the client's identity provider).
- **PBMS estimate comparison as an eval reference, and the Tier 2/3 cap table** (question 36).

## 2. Constraints

These are standing constraints from the product owner.

- **Credentials.** Until the client issues a read-only login, the `sa` credentials are used **for reads only**. Guards (§3.1) enforce this in code, not by convention. Switching to the read-only login changes `.env` only.
- **Secrets.** Credentials come from `PBMS_SQL_SERVER`, `PBMS_SQL_DATABASE`, `PBMS_SQL_USER` and `PBMS_SQL_PASSWORD` in `.env`. They are never logged, printed or placed in an exception message.
- **Data in the repo.** No real PBMS data enters the repository, whether in fixtures, tests or logs. Test data is synthetic.
- **Test members.** Synthetic test members are never written to the client's database. Nothing is ever written to it.
- **Personal identifiers.** SSNs and other unneeded personal identifiers are never read (§3.2).
- **Masked names.** The Dev copy's names are masked (`KA_LS`, `MA_LP`; client question 37), and **the masked `First_Name`/`Last_Name` are the names the chatbot uses**. The unmasked `CC*` columns and other tables are never read.

## 3. The read layer: `src/pension_chat/pbms/`

Nothing else in the codebase imports a SQL Server driver.

### 3.1 `connection.py`: the guard

- **Driver.** `pyodbc` over **ODBC Driver 18 for SQL Server**, with a small in-house pool (at most 4 connections), through `asyncio.to_thread`. A pooled connection the server has dropped is retried once on a fresh one. The connection string sets:
  - `Encrypt=yes`;
  - `TrustServerCertificate=yes` (the Dev server's certificate is self-signed; this is recorded as a Phase 3 item);
  - `ApplicationIntent=ReadOnly`;
  - `APP=pension-chat-readonly`, so the client's DBA can see and filter our sessions.

  The driver's own read-only access mode is also requested.
- **Read-only session.** Every connection begins with `SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED`, so the chatbot never takes locks that could block PBMS's own work.
- **Fixed statements only.** The public surface is `await client.fetch(name, **params)`. `name` must be a key of `queries.REGISTRY`, and anything else raises `UnknownQuery` before a connection is touched. There is no method that accepts SQL text.
- **Defence in depth.** At import time, every registered statement must:
  - begin with `SELECT`;
  - contain none of `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `EXEC`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE` or `INTO`, and no `;`;
  - name only allow-listed columns.

  A test asserts this for the registry.
- **Timeouts.** 5 s per query by default. A statement may declare a longer one: the name directory load (§3.4) allows 30 s.
- **Safe errors.** Errors are wrapped in `PbmsUnavailable` (a `RecordUnavailable`) with the statement name and SQLSTATE only, raised `from None`. `repr(client)` never shows the connection string.

### 3.2 `queries.py`: the fixed SQL

There are five named, parameterised statements (`?` placeholders). "Active member" means a linker row whose MEAB ID starts `M-`, on a live employee row (`e.Status = 'A'`) with `e.Employee_Status_Cd = 'A'`.

| name | parameters | returns |
|---|---|---|
| `name_directory` | none | `DISTINCT First_Name, Last_Name` of active members with both names on file |
| `resolve_name` | `first_name`, `last_name` | every active member with exactly that name: MEAB ID, name, birth year, employer, department, status |
| `resolve_meab_id` | `meab_id` | the same row for one MEAB ID (used to confirm a choice and to restore the chip) |
| `record` | `meab_id` | birth date, hire date, first payroll date, separation date, status, PBMS tier code, reciprocal flag, employer, department |
| `salary_years` | `meab_id` | one row per calendar year: salary (by the rule Task 1 settles, §6.2) and months with a pension deduction |

**The column allow-list** is a module constant that a test enforces. Every column a statement names must be in it. Explicitly never allowed: `Ssn`, `SSN`, `eSSN`, every `CC*` column, `PbsAddress_*`, `Pbs*_Phone`, `PbsEmail`, `Maiden_Name`, `Middle_Name` and `MemberID`.

### 3.3 `provider.py`: `PbmsProvider`

`PbmsProvider` implements `RecordProvider`, and also the name search the demo path needs:

- `get_record(key)` takes a MEAB ID and returns an `EmployeeRecord` or `None`. Any key that is not a MEAB ID, such as a Postgres profile UUID, returns `None` without a query.
- `get_salary_history(key)` returns a list of `SalaryYear`.
- `find_employees(query, scope=None)` takes a MEAB ID and returns its match. The subject endpoints use it.
- `names_in(text) -> list[tuple[str, str]]` returns the `(First_Name, Last_Name)` pairs from the name directory (§3.4) that occur in `text`. It runs in memory.
- `find_by_name(first, last) -> list[EmployeeMatch]` returns every active member with that name.

#### Mapping

| `EmployeeRecord` field | PBMS source |
|---|---|
| `key` | `T_Pbs_Linker.MEAB_Id` (canonical `M-######`; internal only) |
| `display_name` | `T_Person_Info.First_Name` + `Last_Name` (masked in Dev) |
| `birth_date` | `T_Person_Info.Birth_Date` |
| `employer` | `T_Employees.ER_Type_Cd`, described by `T_Employer_Types.Er_Type_Short_Desc` |
| `department` | `T_Employees.Department_Seq_No`, described by `T_Departments.Department_Short_Desc` |
| `hire_date` | `T_Employees.Hire_Date`, else `First_Payroll_Date` with `missing["hire_date"]` noting the substitution |
| `termination_date` | `T_Employees.Seperation_Date` |
| `status` | `T_Employees.Employee_Status_Cd`. `Status = 'A'` only marks a live row (§6.1). |
| `tier` | see "Tier" below |
| `service_years`, `service_source` | counted from payroll months with a pension deduction, `service_source="counted from payroll (months with a pension deduction)"`, when §6.2 confirms it matches PBMS; otherwise absent with a reason in `missing` |
| `reciprocal_service_years` | absent. When `T_Employees.Reciprocal_Flg = 'Y'`, `missing` says PBMS flags reciprocal service but holds no amount (the `T_Reciprocal_*` tables are empty in Dev) |
| salary history | complete calendar years only. The current year's part-year pay is not an annual salary, but its months still count toward service. |

**Tier.** PBMS's `Tier_Type_Cd` is a prior, not an oracle. The provider also derives the tier from the participation date:
- before 2011: Tier 1;
- 2011–2016: Tier 2;
- 2017: open between Tier 2 and Tier 3;
- 2018 onwards: Tier 3.

The two are compared:
- **They agree:** use it.
- **They disagree:** PBMS's value is used, and `missing["tier"]` records both values.
- **An unmapped code** (`0`, `T`, blank) with a derivable date: the tier comes from the date, with the code noted.
- **Neither settles it:** `tier=None`, and the existing branch-on-critical-input rule applies.

**A gap is absent, never defaulted.** Every field PBMS leaves empty goes into `missing` with a reason. The missing-input policy then applies: critical inputs are asked for or branched on, and at most 2 secondary assumptions are made, each disclosed.

### 3.4 The name directory

**Why a directory.** Names are how employers identify employees, but a message cannot be sent to SQL Server to find out whether it contains a name. So at startup the provider loads the `name_directory` statement into memory: the distinct first and last names of active members (12,920 pairs in Dev). It holds nothing else, and writes nothing anywhere.

**Refresh and failure.**
- The directory reloads in the background every 6 hours.
- If a load fails, the previous directory stays.
- If there has never been a successful load, name search reports itself unavailable (§7).

**Matching a message** (`names_in`) is deterministic and case-insensitive:

1. **Tokens.** The message is split into whitespace-separated tokens. Surrounding punctuation is trimmed, but `_`, `'` and `.` are kept inside a token, since names contain them (`A'_AA`, `A._AA`). A trailing possessive `'s`/`’s` is removed from a token only when the name without it is in the directory.
2. **Runs.** Every run of 2–4 consecutive tokens is looked up as `"FIRST LAST"`, with any spaces inside a first or last name preserved. Runs of the form `LAST, FIRST` are also looked up.
3. **Result.** Each matched pair is returned once, in order of first appearance.

**Measured uniqueness (active members, 2026-09-30):**
- 12,920 distinct names across 44,125 people;
- only 6,206 people (14%) have a name nobody else shares, and one name is shared by 109 people;
- name + department identifies 93% (at most 12 sharing);
- name + department + birth year identifies 99.6% (at most 3 sharing).

People with no first name on file (8,708 persons across all statuses) cannot be found by name; that is part of client question 37.

## 4. People in context

### 4.1 Identity versus the people asked about

`employee_id` in agent state is the **principal**. It is the Postgres UUID of the signed-in profile, and it scopes row-level security and the checkpointer. It does not change.

The **people in context** are the employees the conversation is about.

- **Storage.** Migration `009_session_subjects` adds `sessions.subjects jsonb NOT NULL DEFAULT '[]'`, with `CHECK (jsonb_array_length(subjects) <= 4)`.
  - It holds the conversation's people, most recent first: `[{"ref", "display_name", "department", "employer", "birth_year"}]`.
  - `ref` is the internal MEAB ID. Code validates it against `^M-[0-9]{6}$` before storing.
  - Names are stored so a returning name is recognised without a query; the transcript already holds them, under the same row-level policies.
- **Choosing.** Choosing a person puts them first. A fifth person pushes out the least recently used one.
- **Recency.** A turn about people already in the list moves them to the front.
- **The MEAB ID is internal.**
  - It is never shown in the UI or written into an answer.
  - In API payloads it travels only as the opaque `ref`, which the UI sends back to choose or remove someone.
  - The prompt names people by name and department.
- **Audit.** Each assistant message's audit trail (`session_messages.tool_calls`) records the refs its turn was about, and any choice it offered.

### 4.2 Who a turn is about (deterministic, in `triage`)

Nothing here calls a model, and the database is asked only to resolve an unfamiliar name.

1. **Names in the message.**
   - They are matched against the conversation's people first, by display name, case-insensitive.
   - When PBMS name search is in force, they are also matched against the name directory (§3.4).
   - A name found in both counts as the person in context.
2. **An unfamiliar name.** If the message names anyone not yet in context, the turn becomes a **choice turn**:
   - every unfamiliar name gets its candidates (one query each, at most 4 names), and the reply offers one card per name;
   - nobody is loaded, and the question is not answered yet;
   - the suggestion carries the question, so the UI re-sends it once every name in it has been chosen;
   - names already in context ride along untouched.
3. **Only familiar names.** The turn is about exactly those people, in the order named, at most 4.
4. **No names.** The turn is about the **previous turn's people** (`last_people`, durable in the checkpoint):
   - "And at 65?" after a comparison stays a comparison.
   - There is one exception. If the previous turn covered several people and this message uses a singular personal pronoun (`he`, `she`, `him`, `her`, `his`, `hers`), the turn becomes a **which-one turn**. The reply asks "Which of them do you mean?" and offers a card of those people, with no query.
   - With no previous turn, the turn is about the first person in the list. With nobody in context, it is about no one, and the question is answered from the Code.
5. **The result.** The result is `turn_people`, a list of refs, and it is emitted at once as a `people` event (§4.4) so the chips update before any answer text arrives.
6. **Choosing ends a choice turn.** The UI adds the chosen person with `POST /sessions/{id}/subjects` and re-sends the question. That turn finds only familiar names, and answers.

### 4.3 Loading records

`fetch_employee_record` loads every person in `turn_people` **in parallel**, through the provider.

- **Pinning.** Each person's first load is pinned in `pinned_people` (durable, keyed by ref), and later turns reuse the pin. An answer resting on facts that changed mid-conversation would contradict the one before it.
- **The per-person bundle.** It is `people: {ref: {"record", "salary_history", "member_facts", "label"}}`. `label` is the display name, suffixed with the department when two people in context share a name.
- **The first person also fills the existing single-member keys** (`employee_record`, `salary_history`, `member_facts`). So a one-person turn is exactly today's path, and nothing downstream changes for it.
- **A person whose record cannot be read** is listed in `unavailable_people`, and the answer says whose record could not be read.
- **With no provider**, the Postgres profile path is unchanged.

### 4.4 API and events

- `GET /api/v1/sessions/{id}/subjects` returns `{"people": [...], "active": [refs]}`. `active` is the last turn's people, read from the checkpoint, so a reload restores the highlight.
- `POST /api/v1/sessions/{id}/subjects {"ref"}` re-resolves `ref` in PBMS (active members only), puts that person first, drops the fifth if there is one, and returns the list.
  - It returns 400 for a malformed `ref`, 404 when no active member has it, and 503 when PBMS is unavailable.
- `DELETE /api/v1/sessions/{id}/subjects/{ref}` removes one person, and `DELETE /api/v1/sessions/{id}/subjects` removes all.
- All of these check that the caller owns the session.
- **The `people` SSE event:** `{"active": [refs]}` is sent as soon as `triage` has decided the turn's people, before any answer token.
- **After a turn,** the session's list is reordered by recency, with one write only when the order changed.
- `/health` reports `record_source`. The UI shows the chips only when it is `"pbms"`.

## 5. One loop for several people

A question about several people runs the **same single loop** as a question about one. Only the calculation step and the tables are per person.

| Step | How many times | Cost |
|---|---|---|
| Decide the turn's people | once (deterministic, `triage`) | none |
| Read the law (search, sections) | **once**; all active members are Article 8 | unchanged |
| Load records | once per person, **in parallel** | about one round trip |
| Resolve the formula | once per tier; people sharing a tier share it | usually already saved |
| Calculate | once per person, in the deterministic engine | milliseconds each |
| Write the answer | **once** | a few more lines |

- **The calculation tool is told whose figure it computes.** With more than one person in the turn:
  - `compute_benefit` takes a required `person`, one of the labels the context block lists.
  - The model calls it once per person. Calls in the same model step run one after another in milliseconds, since no model call happens between them.
  - The handler runs the existing chain (`resolve_recipes → resolve_inputs → validate_recipes → execute_calculations`) on a slice of state carrying **only that person's** record, salary history and facts. B's salary can never reach A's figure: the same rule that keeps one fund's record out of another fund's formula.
- **Results are kept per person.**
  - Each result entry carries `person` (the label) and `person_ref`. Its key is prefixed with the label, so a second person's figure never overwrites the first's.
  - Scenario labels are prefixed the same way (`"KA_LS MA_LP — at age 62"`).
  - A one-person turn keeps today's keys and labels exactly.
- **The comparison table is code-rendered.**
  - With figures for two or more people, the view `PEOPLE` renders one row per person, in turn order: person · department · tier · formula (section) · annual · monthly.
  - It is a marked view like `COMPARISON`: the model places the marker, never writes the figures, and cannot swap two people's numbers.
  - The prose around it may compare, but the numbers come from the table.
- **Verification.** A figure in the answer may trace to *any* turn person's record or computed result. Each record's facts are offered to the verifier under keys prefixed by the person's ref, so two people's values never collide into one key. Which number belongs to whom is guaranteed by the code-rendered table, not by the verifier.
- **Cards.** Each person with a figure gets their own calculation card, titled with their label. The card's record inputs read "PBMS record". A one-person turn's card is unchanged.
- **Context for the model.** With several people, the context block lists each one with their label, department, age, service and tier, and says to call `compute_benefit` once per person with `person`. Each person's record gaps (`missing`) are listed under their own label.

## 5a. UI

This uses the existing design tokens and adds no new visual language.

- **`PeopleChips.tsx`**, in `ChatHeader`, shown only when `record_source` is `"pbms"`:
  - **Nobody in context:** a muted hint, "Name an employee in your question".
  - **Otherwise:** one small chip per person, `KA_LS MA_LP · Finance`, most recent first. Chips for the current turn's people are highlighted (filled). The others are outlined. Each chip has a small ✕ that removes that person.
  - The highlight changes as soon as the `people` event arrives, before the answer streams.
  - At 360 px the chips wrap below the title rather than scroll.
- **`SubjectChoice.tsx`**, one per unfamiliar name (or one for a which-one turn), beneath its message:
  - **One candidate:** a row with **Use this employee**.
  - **2–12 candidates:** one row each (department · employer · born YYYY) with **Use**.
  - **More than 12:** department buttons with counts first, then the people.
  - **After choosing:** the card is marked chosen, the chips update, and **once every card in that message has a choice, the original question is re-sent once**.
- **Calculation cards:** one per person, titled with the person's label when the turn had several. Record inputs read "PBMS record".
- **Code:**
  - `lib/api.ts` gains `fetchPeople`, `addPerson`, `removePerson`, `clearPeople` and `fetchRecordSource`;
  - the pure logic (labels, department grouping, reading suggestions from `tool_calls`, "all chosen?") lives in `lib/subject.ts`, tested with `npm test`.
- **Unchanged:** the existing profile and persona flow when the source is `postgres`.

## 6. Settled by measurement first (plan Task 1)

The plan's first task is a read-only discovery script. It uses aggregates and codes only, and prints no personal values. Its findings are written into this spec as §6.2 before any provider code is written.

1. **The per-year salary rule.** For a sample of retirees, the script compares PBMS's own final average salary (`T_Annuity_Service_Salary`) against three candidates, comparing match rates only:
   - `Ann_Sal` (annual rate);
   - the sum of `Gross` per year;
   - `Capped_Salary`.

   The best match becomes `salary_years`, and client question 38 asks the fund to confirm it.
2. **The service source.** Service counted from payroll months is compared with PBMS's `MEAB_Svc` for the same retirees.
3. **Status codes.** It lists the `Employee_Status_Cd` codes and their descriptions.
4. **Timing** (after Task 3). Each statement is timed against the database, including the directory load.

### 6.1 Measured 2026-09-30 (counts and codes only)

- **Salary must be read through `T_Pen_Ded_Master`.** `T_Pen_Ded_Detail` has no index on `Person_Info_Seq_No`, and one member's rows read directly scanned 46.7M rows and timed out after 120 s. Two indexes support the indexed route:
  - `T_Pen_Ded_Master` is indexed on `(Person_Info_Seq_No, Year, Pen_Ded_Master_Seq_No)`;
  - detail rows are then found by `Pen_Ded_Master_Seq_No`, which leads `Idx_Select_Combination`.

  That route took 240 ms from the dev machine, the same as a bare `SELECT 1` round trip. A test asserts that `salary_years` filters on `T_Pen_Ded_Master.Person_Info_Seq_No` and never on the detail table's column.
- **`T_Employees.Status` is the row's own active flag, not employment status.** 206,320 rows are `A`, against 44,125 active members counted from `Employee_Status_Cd`.
- **Tier codes (on live rows):**

  | Code | Rows |
  |---|---|
  | `1` | 152,069 |
  | `2` | 16,237 |
  | `3` | 32,233 |
  | `0` | 5,043 |
  | `T` | 0 (defined in `T_Tier_Types`, unused on live rows) |
  | blank | 738 |

  Only `1`, `2` and `3` map to tiers.
- **Dates.** `Hire_Date` is empty on 80,763 of the 206,320 live rows; `First_Payroll_Date` is empty on 5,867.
- **Names** (all 222,281 persons):
  - `First_Name` has the shape `AA_AA` on 209,521 rows; it is empty on 8,708, `_AA` on 3,107, and has a space, apostrophe or dot on about 200.
  - `Last_Name` is `AA_AA` on 221,446 rows.
  - About a quarter of the names are stored in mixed case, so matching ignores case.
  - `T_Person_Info` has no index keyed on names. One name lookup across active members took about 400 ms on the server (667 ms from here, against a 258 ms round trip).

## 7. Failures

| Condition | Behaviour |
|---|---|
| PBMS unreachable, login fails, or a query exceeds its timeout | The turn continues. Each person whose record could not be read is in `unavailable_people`, and the answer names them. A name lookup replies that it could not look the name up. Subject endpoints return 503. |
| One of several records fails to load | The others are answered. The answer says whose record could not be read, and the table has no row for them. |
| The name directory has never loaded | Names already in context are still recognised (they are matched against the conversation's own list). New names are not. The server logs a warning, and retries 1 minute after a failed startup load and every 6 hours after that. |
| A name shared by several people | All candidates are offered, and nobody is chosen for the user. |
| A singular pronoun after a turn about several people | A which-one card is offered; nobody is guessed. |
| A fifth person chosen | The least recently used person leaves the list, and the chips show it. |
| `compute_benefit` without `person` in a turn about several people, or with an unknown label | The tool answers with the labels it accepts, and computes nothing. |
| A `ref` that is malformed, or not an active member | 400 or 404 on `POST`. |
| Field empty in PBMS | `missing[field] = reason`, and the missing-input policy applies. |
| PBMS tier disagrees with the dates | PBMS's value is used, and the discrepancy is reported. |

Nothing ever falls back to Postgres or to a default value.

## 8. Testing

- **Unit tests** (using a fake connection, no network):
  - the registry guard;
  - the column allow-list;
  - mapping from synthetic rows, including every `missing` reason and every tier case;
  - `PbmsUnavailable` never containing credentials;
  - the pool's single retry;
  - name matching: case, possessives, `LAST, FIRST`, names with spaces and apostrophes, and no match inside a longer token;
  - the directory refresh keeping the old directory on failure.
- **Who a turn is about (§4.2)**, one test per rule:
  - familiar names;
  - an unfamiliar name giving a choice turn with one card per name;
  - no names following `last_people`;
  - a singular pronoun after a multi-person turn;
  - the first person when nothing precedes;
  - nobody in context;
  - the cap of 4.
- **Loading:** records loaded in parallel; per-person pins reused; one failing record reported while the others load; a one-person turn filling exactly today's keys.
- **Several people:**
  - `compute_benefit` with `person` computing from that person's record only (a test gives two people different salaries and checks each figure);
  - result keys and scenario labels prefixed;
  - the `PEOPLE` view;
  - verification accepting either person's figure;
  - one card per person;
  - a missing or unknown `person` refused.
- **API and events:**
  - `GET`, `POST` and `DELETE` on the subject list, including a malformed or unknown `ref` and the fifth-person eviction;
  - the recency reorder;
  - the `people` event being emitted before the first token;
  - the audit trail.
- **Frontend:** `lib/subject.ts`, run with `npm test`. It covers:
  - chip and candidate labels;
  - department grouping;
  - reading every suggestion in a message;
  - "every card chosen";
  - applying a `people` event.
- **Live smoke test:** `pytest -m pbms` is on demand only, never in CI. It loads the directory, resolves one name drawn from it and reads that person's record. It asserts types and counts only, and prints no values.
- **Evals:**
  - New fixtures shaped like PBMS gaps join S4.
  - A new family, **S13 "people in context"**, runs on fixtures with name search enabled. A case lists its conversation's people in `people:`, as if already chosen. The cases cover:
    - switching between two people by name;
    - returning to an earlier one;
    - a follow-up with no name;
    - a comparison of two and of three people (the expected figures are worked per person);
    - a singular pronoun after a comparison, which must ask.
  - Cases without `people:` run exactly as before.
