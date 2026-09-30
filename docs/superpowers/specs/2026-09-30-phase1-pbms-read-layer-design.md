# Phase 1: the MEABF read layer and the demo path

**Status:** design approved 2026-09-30, then **revised the same day**: employees are found by first and last name typed in the chat, never by MEAB ID. This written spec awaits review.
**Scope:** `docs/superpowers/specs/2026-09-29-employer-first-scope.md` (Phase 1; client questions 35–38).

## 1. What this builds

An employer names an employee in the chat, confirms which person is meant, and the chatbot answers from that employee's PBMS record:

> "What would KA_LS MA_LP's pension be if they retired at 62?"
> → "There are 9 active employees named KA_LS MA_LP. Which one?" *(a list: department, birth year)*
> *(picks "Streets & Sanitation, born 1964")* → the question is answered for that person.

It has two halves:

- **The read layer.** `PbmsProvider` implements the existing `RecordProvider` interface (`src/pension_chat/records.py`) over the client's SQL Server (`PbsMeabDev`), with live, parameterised, read-only queries and no sync.
- **The demo path.**
  - A conversation has a **subject**: the employee being asked about.
  - The subject is found from a first and last name in the user's message, and becomes the subject only after the user confirms which person is meant.
  - Internally the subject is keyed by MEAB ID, the one identifier that is unique. **Users never type or see it** (§4.1).

### Success criteria

1. **Naming one employee.** A message naming an active employee by first and last name produces a confirm-or-choose card. Choosing a person answers the original question for them, and record-derived inputs on the calculation card read "PBMS record".
2. **Ambiguous names.** An ambiguous name never selects anyone. The user always chooses. When more than 12 people share the name, the card asks for the department first.
3. **Read-only access.** No statement outside the fixed query registry can reach SQL Server, and no query reads a column outside the allow-list (§3.2).
4. **PBMS unavailable.** When PBMS is unreachable or slow, the turn still answers and says the record could not be read. Nothing falls back to Postgres or invents a value.
5. **Speed.**
   - Checking a message for a name costs no database time and no model call.
   - The candidate lookup is one query, and happens only when a name in the message actually matches.
   - The first-turn record read is at most 300 ms in-region.
   - Later turns reuse the pinned record.
6. **Nothing else changes.** Local development, the unit tests and the eval suite are unchanged unless `RECORD_SOURCE=pbms`.

### Not in this phase

- **Search by anything other than first and last name.** No Employee ID, no MEAB ID, no partial names.
- **Employees who are not active members.** Retirees, inactive and deferred members are out of scope; the lookup covers `Employee_Status_Cd = 'A'` only.
- **Scoping employers to their own employees.** This covers who may see whom, and belongs to Phase 3 along with the client's identity provider.
- **PBMS estimate comparison** (`T_Intf_Estimate`/`T_Estimate_Detail`) as an eval reference. That comes after Phase 1.
- **Reading the Tier 2/3 cap table** (question 36).

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

## 4. The conversation's subject

### 4.1 Identity versus subject

`employee_id` in agent state is the **principal**. It is the Postgres UUID of the signed-in profile, and it scopes row-level security and the checkpointer. It does not change.

The **subject** is the employee being asked about.

- **Storage.** Migration `009_session_subject` adds `sessions.subject_meab_id text NULL` (with a `CHECK` for `^M-[0-9]{6}$`) and `sessions.subject_set_at timestamptz NULL`. The existing `sessions` row-level policies cover the new columns.
- **State.** `subject_meab_id` is added to `PensionAgentState`, and is passed on every turn from the session row.
- **Loading.** `fetch_employee_record` loads by `subject_meab_id` when a provider is in force. The pin rule is unchanged: a pinned record is reused only while its `id` equals the key, so a new subject reloads, and earlier answers keep the facts they rested on.
- **The MEAB ID is internal.**
  - It is never shown in the UI or written into an answer.
  - In API payloads it travels only as the opaque field `ref`, which the UI sends back when the user picks a person.
  - The answer prompt names the employee by name and department, not by MEAB ID.
- **Audit.** Each assistant message's audit trail (`session_messages.tool_calls`) records the subject the turn used, and any candidates it offered.

### 4.2 A name in a message: offer, never load

This runs in `triage`. It is deterministic and costs no model call. The database is queried only when a name matches.

1. **Finding a name.** `names_in(question)` is checked. A name equal to the current subject's name passes straight through; the question is about them. If nothing else matches, the turn proceeds normally.
2. **Resolving it.** For the first other name found, `find_by_name(first, last)` returns the candidates, and the turn does **not** answer the question yet. The reply is:

   | Candidates | Reply |
   |---|---|
   | 1 | "Did you mean KA_LS MA_LP (Streets & Sanitation, born 1964)?" plus a card with **Use this employee** |
   | 2–12 | "There are N active employees named KA_LS MA_LP. Which one?" plus a card listing each with department, employer and birth year |
   | more than 12 | "There are N active employees named KA_LS MA_LP. Which department?" plus a card that shows the departments (with counts) first, then the people in the chosen one |
   | 0 | not reachable: `names_in` only returns names in the directory, so a directory hit with no active match (the directory is up to 6 hours old) says "I couldn't find an active employee named …" |
   | PBMS unavailable | "I couldn't look up KA_LS MA_LP just now: the fund's records are not answering." |

3. **Choosing a person.** The card calls `PUT /sessions/{id}/subject {ref}`. The server re-resolves `ref` in PBMS before storing it. **The UI then re-sends the user's original question**, which the suggestion carries, so the user never has to ask twice.

### 4.3 API

- `PUT /api/v1/sessions/{id}/subject {"ref": "<opaque>"}` re-resolves `ref` with `resolve_meab_id`, stores it and returns the match without a `ref`. It returns:
  - 400 when `ref` is not a well-formed MEAB ID;
  - 404 when no active member has it;
  - 503 when PBMS is unavailable.
- `GET /api/v1/sessions/{id}/subject` returns the current match without a `ref` (or `null`), so a reload restores the chip.
- `DELETE /api/v1/sessions/{id}/subject` clears the subject.
- All three check that the caller owns the session, as the scenario routes do.
- `/health` reports `record_source`. The UI shows the employee chip only when it is `"pbms"`.

## 5. UI

This uses the existing design tokens and adds no new visual language.

- **`SubjectChip.tsx`**, in `ChatHeader`, shown only when `record_source` is `"pbms"`:
  - **No subject:** it shows nothing, or a muted hint: "Name an employee in your question".
  - **Subject set:** a chip showing `KA_LS MA_LP · Streets & Sanitation`, with **Clear**.
- **`SubjectChoice.tsx`** renders a `subject_suggestion` (from the message's `tool_calls`) beneath its message:
  - **One candidate:** a row with **Use this employee**.
  - **2–12 candidates:** one row per person (department · employer · born YYYY), each with **Use**.
  - **More than 12:** department buttons with counts. Clicking one shows its people.
  - **After a choice:** the card is marked chosen and disabled, the chip updates, and the original question is re-sent.
- **Calculation card:** record-derived inputs read "PBMS record".
- **Code:**
  - `lib/api.ts` gains `setSubject`, `clearSubject`, `fetchSubject` and `fetchRecordSource`;
  - grouping and labels live in `lib/subject.ts`, tested with `npm test`.
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
| PBMS unreachable, login fails, or a query exceeds its timeout | The chat turn continues with no record, and the answer says the fund's records could not be read. A name lookup replies that it could not look the name up. Subject endpoints return 503. |
| The name directory has never loaded | Names are not recognised. The server logs a warning and retries at the next refresh (every 6 hours; the first retry is 1 minute after a failed startup load). |
| A name shared by several people | All candidates are offered, and nobody is chosen for the user. |
| A `ref` that is malformed, or not an active member | 400 or 404 on `PUT`. |
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
- **Agent and API tests:**
  - the subject outranking the principal;
  - an unreadable record being said;
  - a name producing 1, 2–12 or more than 12 candidates;
  - the current subject's name passing through;
  - `PUT`, `GET` and `DELETE` on the subject, including a bad or unknown `ref`;
  - the audit trail.
- **Frontend:** `lib/subject.ts` (grouping by department, labels, reading a suggestion), run with `npm test`.
- **Live smoke test:** `pytest -m pbms` is on demand only, never in CI. It loads the directory, resolves one name drawn from it and reads that person's record. It asserts types and counts only, and prints no values.
- **Evals:** new fixtures shaped like PBMS gaps (service counted from payroll, a tier disagreement) join S4. Evals keep `FixtureProvider`, which has no name search, so the name path is untouched by them.
