# Phase 1: the MEABF read layer and the demo path

**Status:** design approved in conversation, 2026-09-30. Awaiting review of this written spec.
**Scope:** `docs/superpowers/specs/2026-09-29-employer-first-scope.md` (Phase 1; client questions 35–38).

## 1. What this builds

An employer asks about one of their employees and the chatbot answers from that employee's PBMS record:

> *(types `M-012345` in the Employee bar, confirms "Qwxzt Plmrb · M-012345 · Streets & Sanitation")*
> "What would her pension be if she retired at 62?"

It has two halves:

- **The read layer.** `PbmsProvider` implements the existing `RecordProvider` interface (`src/pension_chat/records.py`) over the client's SQL Server (`PbsMeabDev`), with live, parameterised, read-only queries and no sync.
- **The thin demo path.** A conversation has a **subject**, the employee being asked about, identified by MEAB ID. It is set explicitly from the UI, or suggested when an ID appears in a message and confirmed by the user.

### Success criteria

1. Typing a MEAB ID in the Employee bar, confirming, and asking a calculation question gives a figure whose record-derived inputs read "PBMS record (M-######)" on the calculation card.
2. No statement outside the fixed query registry can reach SQL Server, and no query reads a column outside the allow-list (§3.2).
3. When PBMS is unreachable or slow, the turn still answers, and says the record could not be read. Nothing falls back to Postgres or invents a value.
4. The first-turn PBMS read is at most 300 ms in-region, as reported by the existing per-stage timings. Later turns reuse the pinned record.
5. Local development, the unit tests and the eval suite are unchanged unless `RECORD_SOURCE=pbms`.

### Not in this phase

- **Name search.** In the Dev copy, `T_Person_Info` names are masked (client question 37), so name search waits for the client's answer.
- **Scoping employers to their own employees.** This covers who may see whom, and belongs to Phase 3 along with the client's identity provider.
- **PBMS estimate comparison** (`T_Intf_Estimate`/`T_Estimate_Detail`) as an eval reference. That comes after Phase 1.
- **Reading the Tier 2/3 cap table** (question 36). The core formulas remain awaiting sign-off.

## 2. Constraints

These are standing constraints from the product owner.

- **Credentials.** Until the client issues a read-only login, the `sa` credentials are used **for reads only**. Guards (§3.1) enforce this in code, not by convention. Switching to the read-only login changes `.env` only.
- **Secrets.** Credentials come from `PBMS_SQL_SERVER`, `PBMS_SQL_DATABASE`, `PBMS_SQL_USER` and `PBMS_SQL_PASSWORD` in `.env`. They are never logged, printed or placed in an exception message.
- **Data in the repo.** No real PBMS data enters the repository, whether in fixtures, tests or logs. Test data is synthetic.
- **Test members.** Synthetic test members are never written to the client's database. Nothing is ever written to it.
- **Personal identifiers.** SSNs and other unneeded personal identifiers are never read (§3.2).
- **The masking.** The Dev copy's name masking is respected. The unmasked `CC*` columns and other tables are not used to work around it.

## 3. The read layer: `src/pension_chat/pbms/`

Nothing else in the codebase imports a SQL Server driver.

### 3.1 `connection.py`: the guard

- **Driver.** `aioodbc` over **ODBC Driver 18 for SQL Server**, which is installed on the dev machine; the deployment image installs it too. The connection string sets:
  - `Encrypt=yes`;
  - `TrustServerCertificate=yes` (the Dev server's certificate is self-signed; this is recorded as a Phase 3 item);
  - `ApplicationIntent=ReadOnly`;
  - `Application Name=pension-chat-readonly`, so the client's DBA can see and filter our sessions.
- **Read-only session.** Every session begins with `SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED`, so the chatbot never takes locks that could block PBMS's own work.
- **Fixed statements only.** The public surface is `await pbms.fetch(name, **params)`. `name` must be a key of `queries.REGISTRY`, and anything else raises `UnknownQuery` before a connection is touched. There is no method that accepts SQL text.
- **Defence in depth.** At import time, every registered statement must:
  - begin with `SELECT` (or `WITH … SELECT`);
  - contain none of `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `EXEC`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT` or `INTO`, and no `;` in its body.

  A test asserts this for the registry.
- **Timeout and pool.** Each query has a 5 s timeout. A small pool (min 1, max 4) is opened lazily on first use and closed at app shutdown.
- **Safe errors.** Errors are wrapped in `PbmsUnavailable` with a message that never contains the connection string.

### 3.2 `queries.py`: the fixed SQL

There are four named, parameterised statements (`?` placeholders):

| name | parameters | returns |
|---|---|---|
| `resolve_meab_id` | `meab_id` | person and employee sequence numbers, masked display name, employer, department, status |
| `resolve_employee_id` | `employee_id`, optional `employer_cd` | the same, and possibly several rows (an Employee ID can repeat across employers) |
| `record` | `person_seq`, `employee_seq` | birth date, hire date, first payroll date, separation date, status, PBMS tier code, employer, department, service rows |
| `salary_years` | `person_seq` | one row per calendar year, built by the rule Task 1 settles (§6) |

**The column allow-list** is a module constant that a test enforces. Every column a statement names must be in it. Explicitly never allowed: `Ssn`, `SSN`, `eSSN`, every `CC*` column, `PbsAddress_*`, `Pbs*_Phone`, `PbsEmail`, `Maiden_Name` and `MemberID`.

### 3.3 `provider.py`: `PbmsProvider`

`PbmsProvider` implements `RecordProvider`:

- `find_employees(query, scope=None)` recognises `M-\d{6}` (case-insensitive, normalised to upper case) or a bare Employee ID, and returns `EmployeeMatch` rows;
- `get_record(key)` takes a MEAB ID and returns an `EmployeeRecord` or `None`;
- `get_salary_history(key)` returns a list of `SalaryYear`.

#### Mapping

| `EmployeeRecord` field | PBMS source |
|---|---|
| `key` | `T_Pbs_Linker.MEAB_Id` (canonical `M-######`) |
| `display_name` | `T_Person_Info.First_Name` + `Last_Name` (masked in Dev) |
| `birth_date` | `T_Person_Info.Birth_Date` |
| `employer` | `T_Employees.ER_Type_Cd`, described by `T_Employer_Types` |
| `department` | `T_Employees.Department_Seq_No`, described by `T_Departments.Department_Short_Desc` |
| `hire_date` | `T_Employees.Hire_Date`, else `First_Payroll_Date` with `missing["hire_date"]` noting the substitution |
| `termination_date` | `T_Employees.Seperation_Date` |
| `status` | `T_Employees.Employee_Status_Cd`. `Status = 'A'` only marks a live row (§6.1). |
| `tier` | see "Tier" below |
| `service_years`, `service_source` | PBMS service rows (`T_Payroll_Service_Detail`/`Master`) where present, with `service_source="PBMS service record"`; otherwise counted from pay periods with contributions, with `service_source="counted from payroll"` |
| `reciprocal_service_years` | `T_Employees.Reciprocal_Flg`/`Reciprocal_Employers` presence only. Otherwise absent, with a reason in `missing` (the `T_Reciprocal_*` tables are empty in Dev) |

**The MEAB ID is the only stored key.** An Employee ID is a way to *find* a MEAB ID and is never stored or used as a key.

**Tier.** PBMS's `Tier_Type_Cd` is a prior, not an oracle, as with coverage. The provider also derives the tier from the participation date:
- before 2011: Tier 1;
- 2011–2016: Tier 2;
- 2017: undetermined between Tier 2 and Tier 3;
- 2018 onwards: Tier 3.

The two are compared:
- **They agree:** use it.
- **They disagree:** `tier` is set to PBMS's value, and `missing["tier"]` records both values and the reason, so the answer reports the discrepancy instead of choosing silently.
- **2017 with no PBMS code:** `tier=None`, and the existing branch-on-critical-input rule applies.

**A gap is absent, never defaulted.** Every field PBMS leaves empty goes into `missing` with a reason, following the existing rule in `records.py`. The missing-input policy then asks for it or branches on it: critical inputs are asked for or branched on, and at most 2 secondary assumptions are made, each disclosed.

### 3.4 Wiring

- `config.py` gains `record_source: Literal["postgres", "pbms"] = "postgres"` and the four `pbms_sql_*` settings.
- `main.py`'s lifespan builds `PbmsProvider` when `record_source == "pbms"`. A request dependency then enters `use_provider(provider)` around each chat turn, the same context variable the eval suite already uses.
- The eval suite keeps `FixtureProvider`, and tests keep the default.

## 4. The conversation's subject

### 4.1 Identity versus subject

`employee_id` in agent state is the **principal**. It is the Postgres UUID of the signed-in profile, and it scopes row-level security and the checkpointer (`checkpointer.py`). It does not change.

The **subject** is the employee being asked about. It is a separate key:

- **Storage.** Migration `007_session_subject` adds `sessions.subject_meab_id text NULL` (with a `CHECK` for `^M-[0-9]{6}$`) and `sessions.subject_set_at timestamptz NULL`. The existing `sessions` row-level policies cover the new columns, so no new policy is needed.
- **State.** `subject_meab_id` is added to `PensionAgentState` and `initial_state()`.
- **Loading.** `fetch_employee_record` loads by `subject_meab_id` when a provider is in force, and by `employee_id` otherwise. The pin rule is unchanged: a pinned record is reused only while its `id` equals the key, so changing the subject reloads it, and earlier answers keep the facts they rested on.
- **Audit.** Each assistant message's audit trail (`session_messages.tool_calls`) records the `subject_meab_id` the turn used.

### 4.2 API

- `GET /employees/lookup?meab_id=M-012345`, or `?employee_id=…[&employer=…]`, returns `[{meab_id, display_name, employer, department, status}]` and writes nothing. Several matches are returned as they are, and the client must pick; nothing is auto-selected. It returns 404 when nothing matches, 503 when PBMS is unavailable, and 400 for a malformed ID. It is only mounted when `record_source == "pbms"`.
- `PUT /sessions/{id}/subject {"meab_id": "M-012345"}` re-resolves the ID in PBMS, stores it and returns the confirmed match.
- `DELETE /sessions/{id}/subject` clears the subject.
- The `done` event and `GET /sessions/{id}` gain a `subject` field, so a reload restores the chip.
- `/health` reports `record_source`, which the UI uses to show the Employee bar.

### 4.3 An ID in a message: suggest, never load

This runs in `triage`, is deterministic, and costs no model call. When a message contains `\bM-\d{6}\b` (case-insensitive) that is not the current subject:

1. The turn does **not** load that employee. It resolves the ID with one lookup.
2. **If the ID resolves,** the reply is the confirmation question ("Did you mean Qwxzt Plmrb (M-012345, Streets & Sanitation, active)? Set them as this conversation's employee?"), sent with a `subject_suggestion` SSE event carrying the match. The rest of the question waits for the user's confirmation.
3. **If it does not resolve,** the reply says no employee has that ID.
4. **If PBMS is unavailable,** the reply says the ID could not be checked.

An ID that is already the subject passes straight through.

## 5. UI

This uses the existing design tokens and adds no new visual language.

- **`SubjectBar.tsx`**, in `ChatHeader`, shown only when `/health` reports `record_source: "pbms"`:
  - **Empty:** an "Employee" input (`M-012345 or Employee ID`) with a Look up button.
  - **After lookup:** a confirm row with Use and Cancel, a pick-list when there are several matches, or "No employee with that ID".
  - **Set:** a chip showing `name · M-###### · department` with Change and Clear.
- **`SubjectSuggestion.tsx`** renders a `subject_suggestion` beneath its message with the same confirm row. Confirm calls `PUT`, and the bar then shows the chip.
- **Calculation card:** record-derived inputs read "PBMS record (M-######)".
- **Code:** `lib/api.ts` gains `lookupEmployee`, `setSubject` and `clearSubject`. The state logic lives in `lib/subject.ts` and is tested with `npm test`.
- **Unchanged:** the existing profile and persona flow is untouched when the source is `postgres`.

## 6. Settled by measurement first (plan Task 1)

The plan's first task is a read-only discovery script. It uses aggregates and codes only, and prints no personal values. Its findings are written into this spec as an addendum before any provider code is written.

1. **The per-year salary rule.** For a sample of members, the script compares how PBMS's own estimate and annuity tables (`T_Estimate_*`, `T_Annuity_Calc_*`, `T_Annuity_Service_Salary`) build a yearly salary against three candidates, comparing totals only:
   - `T_Pen_Ded_Detail.Ann_Sal` (annual rate);
   - the sum of `Gross` per calendar year;
   - `Capped_Salary`.

   The rule that matches becomes `salary_years`, and client question 38 asks them to confirm it.
2. **The service source.** It measures how often PBMS service rows exist for active members, and how a count from payroll compares with them where both exist.
3. **Indexes and timing.** It checks whether `T_Pen_Ded_Detail` (46.7M rows) and `T_Pen_Ded_Master` can be read per person through an index, and times each of the four statements for a sample of members. If the salary read cannot meet 300 ms in-region, it reads through `T_Pen_Ded_Master`, or the salary rows are fetched in parallel with the record.
4. **Tier codes.** It maps `T_Tier_Types` codes to tiers 1, 2 and 3, and counts active members whose PBMS tier disagrees with the tier derived from their dates.

### 6.1 Measured 2026-09-30 (counts and codes only)

- **Salary must be read through `T_Pen_Ded_Master`.** `T_Pen_Ded_Detail` has no index on `Person_Info_Seq_No`, and one member's rows read directly scanned 46.7M rows and timed out after 120 s. Two indexes support the indexed route:
  - `T_Pen_Ded_Master` is indexed on `(Person_Info_Seq_No, Year, Pen_Ded_Master_Seq_No)`;
  - detail rows are then found by `Pen_Ded_Master_Seq_No`, which leads `Idx_Select_Combination`.

  That route took 240 ms from the dev machine, the same as a bare `SELECT 1` round trip. Server time is therefore negligible, and in-region cost is network only. `salary_years` joins in this order, and a test asserts that the statement filters on `T_Pen_Ded_Master.Person_Info_Seq_No` and never on the detail table's column.
- **`T_Employees.Status` is the row's own active flag, not employment status.** 206,320 rows are `A`, against the 44,138 active members counted from `Employee_Status_Cd`. `status` is therefore mapped from `Employee_Status_Cd`, and `Status = 'A'` only selects live rows.
- **Tier codes (on live rows):**

  | Code | Rows |
  |---|---|
  | `1` | 152,069 |
  | `2` | 16,237 |
  | `3` | 32,233 |
  | `0` | 5,043 |
  | `T` | ? (not counted) |
  | blank | 738 |

  Only `1`, `2` and `3` map to tiers. Any other code (`0`, `T`, blank) gives `tier=None` with its code in `missing["tier"]`, and the tier is then taken from the dates. What `0` and `T` mean goes to the client (question 38).
- **Dates.** `Hire_Date` is empty on 80,763 of the 206,320 live rows; `First_Payroll_Date` is empty on 5,867. The `First_Payroll_Date` fallback (§3.3) therefore matters for most records, and is disclosed each time.

## 7. Failures

| Condition | Behaviour |
|---|---|
| PBMS unreachable, login fails, or a query exceeds 5 s | The chat turn continues with no record. The `record_unavailable` note makes the answer say the fund's records could not be read. Lookup and subject endpoints return 503. |
| Unknown MEAB ID | 404, or a "no employee with that ID" reply. |
| Malformed ID | 400. The UI validates before sending. |
| Several matches for an Employee ID | All are returned, and the user picks. |
| Field empty in PBMS | `missing[field] = reason`, and the missing-input policy applies. |
| PBMS tier disagrees with the dates | PBMS's value is used, and the discrepancy is in `missing["tier"]` and reported. |

Nothing ever falls back to Postgres or to a default value.

## 8. Testing

- **Unit tests** (`tests/test_pbms_*.py`, using a fake connection, no network):
  - the registry guard (unknown name, and every statement read-only and allow-listed);
  - the column allow-list;
  - mapping from synthetic rows, including every `missing` reason and every tier-agreement case;
  - `PbmsUnavailable` never containing credentials.
- **API tests:**
  - lookup: one match, several, none, unavailable, malformed;
  - `PUT`/`DELETE` subject, including the `CHECK` constraint;
  - `fetch_employee_record` loading by subject;
  - switching subject reloading the record;
  - the ID-in-message suggestion, which loads nobody.
- **Frontend:** `lib/subject.ts` state transitions, run with `npm test`.
- **Live smoke test:** `pytest -m pbms` is on demand only, never in CI. It resolves one MEAB ID from `.env`'s `PBMS_SMOKE_MEAB_ID` and asserts types and presence only. It prints no values.
- **Evals:** new fixtures shaped like PBMS gaps (no hire date, service counted from payroll, a tier disagreement) join S4. Evals keep `FixtureProvider`.
