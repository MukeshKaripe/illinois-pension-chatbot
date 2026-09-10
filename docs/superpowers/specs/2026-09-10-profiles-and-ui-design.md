# Profiles and UI — Design

**Date:** 2026-09-10
**Status:** Approved in brainstorming; supersedes the draft `persona_and_ui_mvp_spec.md` where they differ.
**Scope:** Visual system, chat shell, trust UX, multi-profile accounts, scenario chip.

## 1. Why

The UI reads as a generic dark template, applied inconsistently, and personas vanish after signup: a member picks one, and nothing on screen shows it, explains it, or lets them act on it. The goal is an interface that feels like a polished AI chat product, with the pension-specific parts — the member's profile, sources, calculations, scenarios — appearing in context around the conversation.

The draft spec's spine is kept: chat first, profile as quiet context, scenarios temporary, evidence in a drawer, figures only from the engine. Where this document departs from it, the reason is given.

## 2. Decisions and their reasons

| Decision | Reason |
|---|---|
| A standard persona for every member fund, Articles 2–19. | The whole Code is ingested, and nothing in the agent limits calculation to 11 and 12 — recipes come from "registry lookup, else derive from the statute" for any Article. The only gate was `OFFERED_ARTICLES` in `personas.py`. |
| Each Article shows a support level, set in config: **checked** or **experimental**. | The registry holds two recipes (Articles 11 and 12, Tier 1, `agent_derived`, `estimate`). For the other funds a figure is derived on first use and has no eval coverage. The objection to offering them was never that a figure might fail — it was failing *with nothing on screen saying why*. A visible support level answers that. |
| An employer-first picker: "Who do you work for?", with search, grouped by employer; Article as a secondary label. | Members do not know their Article number — that is why `coverage.py` exists. With 18 funds a picker is needed, but it asks about the employer, not the statute. |
| No "coming soon" Articles; no personas for 1, 20–24, 1A, 22A–C. | 1A (Department of Insurance oversight), 22A (State Board of Investment), 22B/22C (police and firefighter investment funds), and 1, 20–24 are not funds anyone is a member of (`NON_MEMBERSHIP_ARTICLES`). They remain fully askable; they can never be profile destinations. |
| No blank profiles; no "save scenario as profile". | Both let a member author their own record, collapsing the two voices — record and member — that `coverage.py` and the guardrails' provenance classes depend on. "Retire at 59" is also a scenario fact, not a profile fact. Profiles start from a preset and are edited within that Article's bounds. |
| Per-Article profile config, rendered by a small form renderer. | At 18 Articles a per-Article schema is earned (at 2 identical ones it was not). Common fields stay common with per-Article bounds; an Article-specific field is added **only when a recipe reads it**, so no field is editable without changing a figure. |
| No contribution fields. | Nothing stores them and no recipe reads them. An editable input that changes no figure is the failure `personas.apply_edits` exists to prevent for salary history. |
| No version table. A conversation pins the record it loaded on its first turn. | What matters is that a conversation stays consistent with the facts it started from. A version table overclaims: recipes and corpus evolve underneath, so pinning a profile does not reproduce an old figure. |
| Multiple profiles per account, via an `accounts` table and `employees.account_id`. | Trying different member situations is what makes personas useful in a prototype with synthetic data. Keeping RLS keyed on the employee id keeps the existing policies intact. |
| Switching profile starts a new chat. | A conversation's checkpoint binds coverage decisions, assumptions and scenarios to one record. |
| Display name asked at signup, stored on the account. | There is no name anywhere today; the greeting needs one. |
| Light theme by default, dark by system preference, no toggle. | The audience reads statute text and dollar figures and likely skews older. A toggle is deferred as YAGNI. |
| Cut: landing page, Sessions page, Settings page, "set as primary" step, validation checklists. | The anonymous chat is already the front door (`app/page.tsx`); the sidebar covers session history; an account menu covers settings; the first profile is primary; a checklist that is always green is decoration. |

## 3. Visual system — "Civic calm"

One source of truth: the tokens in `frontend/src/app/globals.css`. Components use semantic utilities (`bg-background`, `text-foreground`, `bg-card`, `border-border`, `text-muted-foreground`, `bg-primary`) and never raw palette classes. Every hard-coded `neutral-*`, `blue-*`, `purple-*`, gradient, ambient glow and `backdrop-blur` is removed.

### Tokens

| Token | Light | Dark |
|---|---|---|
| `--background` | `oklch(0.985 0.004 85)` warm off-white | `oklch(0.19 0.012 250)` |
| `--foreground` | `oklch(0.23 0.02 250)` ink | `oklch(0.93 0.006 85)` |
| `--card`, `--popover` | `oklch(1 0 0)` | `oklch(0.23 0.012 250)` |
| `--muted` | `oklch(0.96 0.006 85)` | `oklch(0.26 0.012 250)` |
| `--muted-foreground` | `oklch(0.50 0.015 250)` | `oklch(0.70 0.012 250)` |
| `--border`, `--input` | `oklch(0.90 0.008 85)` | `oklch(0.32 0.012 250)` |
| `--primary` (deep teal) | `oklch(0.46 0.08 195)` | `oklch(0.72 0.09 195)` |
| `--primary-foreground` | `oklch(0.985 0.004 85)` | `oklch(0.19 0.012 250)` |
| `--ring` | `var(--primary)` | `var(--primary)` |
| `--sidebar` | `oklch(0.965 0.006 85)` | `oklch(0.21 0.012 250)` |

`@custom-variant dark` changes from the `.dark` class to `@media (prefers-color-scheme: dark)`, and the dark values are defined in a matching media block. Contrast: body text and muted text meet WCAG AA on their backgrounds in both themes.

### Type
- **UI:** Geist Sans (already loaded), 15–16px.
- **Reading:** Source Serif 4 (`next/font/google`) for assistant answers and statute quotes, 17px, line-height 1.65.
- **Figures:** `font-variant-numeric: tabular-nums` on tables, calculation cards and any money figure.
- Page title: "Illinois Pension Assistant".

### Layout and restraint
- Conversation column max 720px, centred.
- User turns: a right-aligned bubble on `--muted`. Assistant turns: full-width serif text, no bubble.
- Radius 10px base. Shadows only on popovers, menus and drawers. Transitions 150ms ease-out on those only; no bounce, no pulse.
- Icons: lucide, 16/18px, one stroke weight.
- Mobile (≤ 768px): sidebar becomes an overlay; drawers become bottom sheets; the page never scrolls horizontally.

## 4. Phase 1 — Chat shell (frontend only)

**Sidebar**
- "New chat" button at the top.
- History grouped Today / Yesterday / Earlier, by `last_active_at`.
- Signed in: account menu at the bottom — display name, sign out.
- Anonymous: one quiet line, "Sign up to keep your conversations", with sign-up and sign-in links. No empty history list.

**Header:** sidebar toggle, product name, and (Phase 3) the profile chip.

**Empty state**
- Signed in: "Hello, {display name}", a one-line profile summary ("Chicago laborers · Close to retirement · 59, 31 years of service"), four starter prompts.
- Anonymous: "Hello", four general prompts, and "This is a temporary conversation."
- Until Phase 3 lands (no display name or profile endpoint yet), the signed-in empty state uses the anonymous copy and anonymous prompts, with "Hello" alone.

**Starter prompts** are deterministic templates chosen from profile facts, never a model call. One rule set, in one frontend module, unit-testable:

| Condition | Prompt |
|---|---|
| active, age ≥ 55 | "Can I retire now, and what would I get?" |
| active, service ≥ 28 | "Am I close to the maximum pension?" |
| active, hired on or after 2011-01-01 | "How is Tier 2 different for me?" |
| active, service < 10 | "When will I be eligible for a pension?" |
| retired | "How do annual increases work on my pension?" |
| always (fills to four) | "Compare retiring at 60, 62 and 65." / "What counts as salary for my pension?" / "What happens to my pension if I leave early?" |

Anonymous prompts: "What does Article 11 cover?", "How is a pension calculated for a Chicago laborer?", "What is the difference between Tier 1 and Tier 2?", "Estimate a pension for someone aged 60 with 25 years of service."

Clicking a prompt sends it immediately.

**Composer**
- Auto-growing textarea, max ~8 lines. Enter sends, Shift+Enter inserts a newline.
- Stop button while streaming: aborts the fetch via `AbortController`; the partial answer stays, marked "Stopped".
- Disabled only while a session is being created (existing guard).

**Status:** while waiting, one line of muted text showing the existing `STAGE_LABELS` value, with a subtle shimmer. No bouncing dots.

## 5. Phase 2 — Trust UX

**Evidence**
- New `GET /api/v1/sections/{section_id}` returns `{section, title, article, text}` using the existing `fetch_sections` (unfiltered by Article, as it is everywhere).
- In rendered answers, any section reference that appears in the message's `verified_citations` becomes a link. The citation chips under the message remain and are clickable. Both open the evidence drawer.

**Calculation card**
- The `done` event gains `worksheet`: the headline figure(s), `execution_policy`, legal basis section, and key inputs each with its provenance source (`record`, `stated`, `hypothetical`, `assumed`), taken from `WorkSheet` — the same data `blocks.py` formats today.
- A card above the answer text shows: headline annual and monthly figure; an "Estimate" label unless `execution_policy == "authoritative"`; the legal basis; key inputs with a small source label (Profile / You said / Scenario / Assumed). "View calculation" opens the drawer with worked steps and assumptions.
- The markdown blocks remain in the answer as a fallback; the card is additive.
- Persisted messages must carry the same `worksheet` so reloaded conversations show the card.

**Drawer:** one right-hand drawer with two modes, Evidence and Calculation. Closed by default; opens only on click; never navigates away from the chat.

## 6. Phase 3 — Profiles

Split in two: **3a** builds multi-profile accounts, switching and editing on the seven existing personas; **3b** adds a standard persona for every member fund, per-Article config, the employer picker and support levels. 3a does not wait on 3b.

### Data model (hand-written Alembic migration)
- New `accounts`: `id`, `email` (unique on `lower(email)`), `hashed_password`, `display_name`, `is_admin`, `primary_employee_id` (nullable FK), `created_at`.
- `employees` gains `account_id` (nullable FK — the two seeded pilot rows have none until claimed), `persona_key`, `label`.
- Migration moves `email`, `hashed_password`, `is_admin` from existing claimed `employees` rows into `accounts`, links them, then drops those columns from `employees`.
- `db/models.py` is updated to match, per CLAUDE.md.

### Auth
- JWT `sub` becomes the account id; `TokenData` carries `account_id` and `email`.
- `require_admin` reads `accounts.is_admin`.
- `/signup`: email, password, display name, persona key, edits → creates the account and its first employee (profile), sets it primary.
- `/register` (pilot pair): email, password, display name → creates an account and links the unclaimed seeded employee.
- `/login`: looks up the account.

### RLS
- Existing employee-keyed policies are unchanged.
- `get_db_session` gains `account_id`, setting `app.current_account_id` in the same `set_config` statement (no extra round trip).
- New policy on `employees`: rows visible where `account_id = app.current_account_id`, for listing an account's profiles. New policy on `accounts`: own row only.
- Ownership of a profile is checked inside queries that already run: `POST /sessions` inserts only when the employee belongs to the account; the `send_message` access check joins session → employee → account.

### API
- `GET /profiles` — the account's profiles plus which is primary.
- `POST /profiles` — persona key + edits → new profile (validated by `personas.validate`).
- `PATCH /profiles/{id}` — edits within bounds (salary history rescales via `apply_edits`).
- `POST /profiles/{id}/primary`.
- `GET /me` — display name, primary profile summary.
- `POST /sessions` takes an optional `profile_id`, defaulting to primary.

### Record pinning
`employee_record` and `salary_history` become durable per conversation: `fetch_employee_record` loads them on the first turn and skips the read when the checkpoint already holds them. Profile edits therefore affect new chats only. The implementation plan must first verify how `initial_state()` treats these keys across turns and that coverage's `record_usable` logic is unaffected.

### Presets and labels (3a)
Member-facing labels; internal keys unchanged:

| Key | Fund | Label |
|---|---|---|
| `article-11-standard` | Chicago laborers (Article 11) | Mid-career, still working |
| `article-11-near-retirement` | Chicago laborers (Article 11) | Close to retirement |
| `article-11-tier-2` | Chicago laborers (Article 11) | Joined after 2011 |
| `article-11-early-career` | Chicago laborers (Article 11) | Early career |
| `article-11-retired` | Chicago laborers (Article 11) | Already retired |
| `article-12-standard` | Chicago Park District (Article 12) | Mid-career, still working |
| `article-12-tier-2` | Chicago Park District (Article 12) | Joined after 2011 |

### Phase 3b — every member fund

**Per-Article profile config.** One backend module owns it and replaces `OFFERED_ARTICLES`. Per Article:
- `employer_group` and `member_label` (for the picker and the profile chip)
- `support_level`: `checked` or `experimental`
- `fields`: the common fields shown (birth date, hire date, employment status, service years, current salary; salary history is derived and rescaled as today), each with that Article's bounds and defaults
- `extra_fields`: Article-specific fields, empty by default

It is served with the personas; `personas.validate` reads its bounds. The frontend renders the edit form from `fields` + `extra_fields` over a small set of field types (date, years, money, status enum). **Rule for `extra_fields`:** a field is added only when a recipe for that Article reads it — typically discovered as a recurring `missing_inputs` entry — and `facts.py` gains the alias that maps it into the engine.

**Standard personas.** One per fund, values written from that fund's own statute (plausible hire age, tier, salary range, active status). Article 11 and 12 keep their extra presets.

| Article | Group | Member label |
|---|---|---|
| 5 | City of Chicago | Chicago police officer |
| 6 | City of Chicago | Chicago firefighter |
| 8 | City of Chicago | Chicago municipal employee |
| 11 | City of Chicago | Chicago laborer |
| 12 | City of Chicago | Chicago Park District employee |
| 17 | City of Chicago | Chicago Public Schools teacher |
| 9 | Cook County | Cook County employee |
| 10 | Cook County | Forest Preserve District employee |
| 2 | State of Illinois | General Assembly member |
| 14 | State of Illinois | State employee |
| 15 | State of Illinois | State university employee |
| 16 | State of Illinois | Teacher outside Chicago |
| 18 | State of Illinois | Judge |
| 3 | Local and regional | Police officer outside Chicago |
| 4 | Local and regional | Firefighter outside Chicago |
| 7 | Local and regional | Municipal employee (IMRF) |
| 13 | Local and regional | Water Reclamation District employee |
| 19 | Local and regional | Closed fund member — authored last: a closed fund admits no new members, so its persona must be an existing member |

**Support level.** `checked` requires the Article's smoke eval (§9) to pass alongside any existing evals; 11 and 12 start `checked`, every other fund starts `experimental`. The level shows on the preset card, in the profile chip menu, and on the calculation card ("Estimate · experimental for this fund"). It is set in config, never inferred from ingestion or from a recipe's existence.

**Rollout.** Articles go live one at a time: persona + config + smoke eval, then enabled. The profile machinery (3a) does not wait on them.

### UI
- **Signup:** (1) email, password, display name; (2) "Who do you work for?" — search plus employer groups, then the fund's presets as cards with label, age, service, status and support level (3a: just the Article 11 and 12 presets, grouped the same way); (3) review and edit within that Article's bounds; (4) start chatting. The synthetic-data notice stays. **New profile** reuses steps 2–3; a profile's Article never changes — another Article means another profile.
- **Profile chip** in the header: "Close to retirement · Art. 11". Menu: current profile summary; switch profile (confirm "Switching starts a new chat. This conversation stays as it is."); edit; new profile.
- **Edit profile:** the same review/edit component as signup; save note "Changes apply to new chats."
- **Sidebar items** show the profile label as a second line when the account has more than one profile.
- User-facing term is "profile"; "persona" stays internal.

## 7. Phase 4 — Scenario chip

- The `done` event gains `scenarios`: the active entries of `scenario_set`, each `{label, overrides}`.
- A chip row above the composer shows them ("Retire at 59"), each with a clear button.
- `DELETE /sessions/{id}/scenarios` clears `scenario_set` in the conversation's checkpoint.
- No "save as profile".

## 8. Out of scope

Separate landing page, Sessions page, Settings page, an Article-number picker, "coming soon" Articles, personas for non-member Articles, profile version table, contribution fields (until a recipe reads them), blank profiles, save-scenario-as-profile, theme toggle, validation checklists, inline `[View]` markers requiring model changes.

## 9. Testing

- **Unit (pytest, no network):** starter-prompt selection rules; account ownership checks; persona label/group mapping; `/signup` and `/register` validation paths.
- **Migration:** run upgrade and downgrade against a scratch database; verify the new RLS policies with scoped sessions (own profiles visible, another account's not).
- **Evals:** `scripts/eval_agent.py` still binds to the fixed persona ids; run the coverage and calculation categories after Phase 3 and after record pinning.
- **Per-Article smoke eval (3b):** one case per fund in `evals/`, run as the standard persona. Passes when coverage resolves to the persona's Article, and a calculation question either returns an estimate citing that Article's sections or names the missing inputs. Passing is what moves an Article from `experimental` to `checked`.
- **Config validity (unit):** every standard persona passes its Article's bounds; every `extra_fields` entry has a `facts.py` alias; no config entry names a non-member Article.
- **Frontend:** a unit test for starter-prompt selection; `npm run build` and `npm run lint` stay at or below the current baseline; manual browser check at desktop and 400px width in light and dark after each phase.

## 10. Risks to verify during planning

- Whether the server stops generation when the client aborts the stream (Stop button).
- How `initial_state()` treats `employee_record` across turns (record pinning).
- `eval_agent.py` and `seed_synthetic_data.py` assumptions about `employees.email`.
- The anonymous path must remain unchanged: `session_token`, its RLS predicate and `BASIS_UNRESOLVED`.
- Recipe derivation for funds whose formula rests on a different salary basis (e.g. police and fire) or inputs `facts.py` does not know. The expected failure is a missing input or a validator rejection, not a wrong figure — the smoke evals must confirm that before any such Article is enabled, even as `experimental`.
