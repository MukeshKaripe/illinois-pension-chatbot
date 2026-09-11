# Phase 3a — Accounts and Profiles Implementation Plan

> **For agentic workers:** executed inline (superpowers:executing-plans). Lean plan by agreement (token budget): interfaces, behaviours, tests and acceptance; code lives in the commits.

**Goal:** One account, many profiles. A member signs up with a display name, is greeted by it, sees the profile a chat uses, and can switch, edit or add profiles — each switch a new chat.

**Architecture:** An additive migration adds `accounts` and `employees.account_id`; the JWT names the account; a profile is an `employees` row, so every existing employee-keyed policy, the checkpointer, `fetch_employee_record` and the evals are unchanged. Three new RLS policies key on `app.current_account_id`; new endpoints use `get_db_session(account_id=…)`, so the admin-session allowlist in `tests/test_db_access.py` does not grow.

**Spec:** `docs/superpowers/specs/2026-09-10-profiles-and-ui-design.md` §6 (3a). User decision 2026-09-11: additive migration, applied to the dev DB once tests pass.

## Global Constraints

- **Additive and reversible.** 005 drops nothing; `downgrade()` hands sign-in back to the employee rows and restores `employees.email NOT NULL` (placeholders for profile rows that have none). Old sign-in columns on `employees` stay until a later cleanup migration.
- The app role has NOBYPASSRLS (003): the migration's data copy runs after `set_config('app.admin', 'on', true)`.
- Policies follow 003's pattern: `USING (p OR ADMIN) WITH CHECK (p OR ADMIN)`, identities read through `NULLIF(current_setting(…, true), '')`.
- `get_admin_session` users stay exactly {auth, admin, sessions, checkpointer, main}. The anonymous path (`session_token`, its predicate, `BASIS_UNRESOLVED`) is untouched.
- Tokens carry `typ: "account"`; a token without it (issued before this change) is a 401, so the client signs out cleanly instead of failing later.
- New chat latency: `POST /sessions` for a signed-in member stays one statement after `set_config` (an `INSERT … SELECT` that checks ownership and resolves the primary profile).
- Profiles are created only from presets and edited only within `personas.validate` bounds; a profile's Article never changes.
- Commit trailer as before. Backend: `uv run pytest`, ruff on touched files. Frontend: `npm test`, `npm run build`, `npm run lint` (0 errors).

## Tasks

### Task 1: Migration 005 and models
- `alembic/versions/005_accounts.py` (follows `004_admin_role`): `accounts(id, email, hashed_password, display_name, is_admin, primary_employee_id → employees ON DELETE SET NULL, created_at)`, unique `lower(email)`; `employees` + `account_id` (FK, indexed), `persona_key`, `label`; `employees.email` nullable. Copy: every employee with a password becomes an account over its own row (`primary_employee_id`), linked back via `account_id`. RLS on `accounts` (enable + force). Policies: `accounts_self_access` (`id = account`), `employees_account_access` (`account_id = account`), `sessions_account_access` (`employee_id IN (SELECT id FROM employees WHERE account_id = account)`); messages and checkpoints inherit through `sessions`.
- `db/models.py`: `Account`; `Employee.email` optional, `account_id`, `persona_key`, `label`.
- `db/session.py`: `get_db_session(..., account_id=None)` sets `app.current_account_id` in the same `set_config` statement.

### Task 2: Auth on accounts
- `TokenData(account_id, email)`; `create_access_token` adds `typ: "account"`; `get_current_user` 401s without it. `require_admin` reads `accounts.is_admin`.
- `/signup` (+ `display_name`): account + first profile (`persona_key`, member-facing `label`, `email=None`) + salary history, primary set, under `get_db_session(account_id=…, employee_id=…)`.
- `/register` (pilot claim, + `display_name`): an unclaimed seeded employee (no password, no account) gets an account.
- `/login`: by `lower(accounts.email)`.
- Tests: `tests/test_auth.py` admin cases move to `account_id` (same assertions); signup schema accepts `display_name` (1–60 chars, trimmed) and still forbids extra fields.

### Task 3: Profiles, sessions and `/me`
- `personas.py`: member-facing labels and `ARTICLE_GROUPS` per spec §6 table; `persona_from_row(row, history)` so edits apply to a stored profile.
- `api/profiles.py`: `GET /api/v1/me` → `{email, display_name, primary_profile_id, profiles: [...]}` (one call feeds the greeting, chip and switcher); `POST /api/v1/profiles` {persona, edits}; `PATCH /api/v1/profiles/{id}` {edits}; `POST /api/v1/profiles/{id}/primary`. All scoped by account. Register in `main.py`.
- `api/sessions.py`: create (signed in) via one `INSERT … SELECT` with optional `profile_id`, defaulting to primary; list via account scope with `profile_id`; send/get access check joins the owning account; the agent and `_scoped()` use `session.employee_id` (the profile).
- Tests: `tests/test_profiles.py` — `persona_from_row` round-trips a preset; edits keep the Article; labels and groups cover every preset.

### Task 4: Apply and verify on the dev DB
- `uv run alembic upgrade head`; `downgrade -1` then `upgrade head` again to prove reversibility; full test suite; live smoke on a port-8123 server: pilot login, signup with a name, `/me`, second profile, primary switch, new chat per profile, list sessions, anonymous chat unchanged, a message on a profile uses its record.

### Task 5: Frontend
- `lib/api.ts`: `me()`, `createProfile`, `updateProfile`, `setPrimary`; `createSession(profileId?)`.
- `lib/starters.ts`: `startersFor(profile)` implementing spec §4's rule table (tested).
- Greeting "Hello, {display name}" + one-line profile summary; profile chip in the header with a menu (switch → new chat, edit, new profile); editor and new-profile flows in the drawer, sharing preset-picker and field components with signup; signup gains display name and employer grouping; sidebar shows the profile label as a second line when there is more than one profile.

### Task 6: Record pinning
- A conversation keeps the record it loaded on its first turn: `fetch_employee_record` skips the read when the checkpoint holds one. Verify how `initial_state()` treats the keys and that coverage's `record_usable` is unaffected; re-run the coverage and calculation eval categories.
