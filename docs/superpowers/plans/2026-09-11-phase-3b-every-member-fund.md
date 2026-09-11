# Phase 3b — Every Member Fund Implementation Plan

> **For agentic workers:** executed inline (superpowers:executing-plans). Lean plan by agreement (token budget): interfaces, behaviours, tests and acceptance; code lives in the commits.

**Goal:** A standard profile for every member fund (Articles 2–19), each editable within its own Article's bounds, chosen by employer rather than Article number, and honest about how well the assistant knows that fund.

**Architecture:** One backend module, `funds.py`, owns per-Article profile config and replaces `personas.OFFERED_ARTICLES` / `FUND_NAMES`. `personas.validate` reads its bounds; `/auth/personas` and `/me` serve each preset and profile with its fund's `member_label`, `employer_group`, `support_level` and `fields`; the frontend renders the edit form from `fields`. The calculation card carries the resolved Article's support level.

**Tech Stack:** as Phase 3a (FastAPI, pydantic, pytest; Next.js 16, `node --test`).

**Spec:** `docs/superpowers/specs/2026-09-10-profiles-and-ui-design.md` §6 "Phase 3b", §9 (smoke + config validity), §10 (recipe-derivation risk).

## Global Constraints

- Fund table exactly as spec §6 (Article → group → member label); groups in the order City of Chicago, Cook County, State of Illinois, Local and regional.
- `support_level` is `checked` or `experimental`, set in config, never inferred. 11 and 12 `checked`; every other fund `experimental`. Promotion is proposed to the user from the smoke results, not applied automatically — "checked" is a claim to members.
- `extra_fields` empty by default; a field is added only when a recipe reads it, and must have a `facts.ALIAS_GROUPS` alias (unit-tested). No storage is built for extra fields until the first one exists.
- No config entry names a non-member Article (`coverage.NON_MEMBERSHIP_ARTICLES`).
- Every standard persona passes its Article's bounds; tier still derived, never stored from input. Persona ids fixed literals.
- The two pilot members, their ids, histories and `SEEDED` are unchanged.
- User-facing term stays "profile"; UI tokens only (palette gate grep stays empty).
- Backend: `uv run pytest`, ruff on touched files. Frontend: `npm test`, `npm run build`, `npm run lint` (0 errors).

## Tasks

### Task 1: `funds.py` and sixteen standard personas
- `src/pension_chat/funds.py`: `FieldSpec(name, label, type: date|years|money|status, min, max, options)`; `Fund(article, employer_group, member_label, support_level, min_hire_age=16, salary_min=0.01, salary_max=1_000_000, extra_fields=())` with `fields` (the five common fields carrying this Article's bounds, then `extra_fields`); `FUNDS: dict[int, Fund]`; `GROUP_ORDER`; `for_article(n) -> Fund | None`; `support_level(article: int | str | None) -> str | None`. Bounds that differ: minimum hire age 21 for police, fire and the General Assembly (2–6), 25 for judges (18).
- `personas.py`: standard personas `article-{n}-standard` for 2–10 and 13–19 (Tier 1, active, plausible for the fund; Article 19 is a retired existing member). `STANDARD` = one per fund (11 and 12 use their existing standards). `validate` reads the fund's bounds. `OFFERED_ARTICLES`/`FUND_NAMES` removed.
- Tests (`tests/test_funds.py`, updates to `test_personas.py`, `test_profiles.py`): table matches the spec; groups in order; no non-member Article; every extra field has an alias; every persona coherent under its fund's bounds; exactly one standard per fund; 11 and 12 checked, others experimental; per-Article hire-age bound rejects a judge hired at 22 but accepts a laborer at 22.

### Task 2: API and card
- `PersonaOption` and `ProfileItem`: `fund` → `member_label`, plus `employer_group`, `support_level`, `fields: list[FieldOption]`.
- `agent/card.py`: `support_level` from the coverage verdict's Article, else the record's, else None.
- Tests: card support level (coverage article wins over record; none when unknown); `/personas` payload serialises every preset with fields.

### Task 3: Frontend
- `lib/api.ts` types; `lib/funds.ts` (tested): `groupPresets(personas, query)` — employer groups in order, funds inside, presets inside, search over member label, group and preset label; `valuesFrom(fields, source)`; `editsFrom(fields, original, values)` (only touched fields).
- `ProfileFields` renders `fields` (date, years, money, status) with the tier and salary-history notes kept; `PresetPicker` gets search, group headings, fund headings with an "Experimental" tag, preset cards showing label, age, service, status. Signup, new-profile and edit panels, `ProfileMenu`, `EmptyState` move to `member_label`; `ProfileMenu` and `CalculationCard` show the support level ("Estimate · experimental for this fund").

### Task 4: Seed, smoke evals and rollout
- `seed_synthetic_data.py` seeds `personas.STANDARD` (accountless, no email) beside the pilot pair, idempotently.
- `eval_agent.py` gains `fund_figure: "N"` — passes when every successful calculation rests on a section of Article N, or no calculation ran and the turn named missing inputs; fails on a figure from another Article or on neither.
- `evals/fund_smoke.yaml` (category `fund_smoke`): one case per fund, as its standard persona, one turn — "Roughly what would my pension be if I retired at the end of this year?" — expecting `coverage_article: N` and `fund_figure: N`. 11 and 12 included as positive controls.
- Run it; an Article that produces a figure from another Article's law, or whose coverage does not resolve to itself, is removed from `FUNDS` (not offered) until fixed. Report results and proposed promotions.

### Task 5: Docs
- CLAUDE.md (personas → funds, support level, smoke eval), spec §6 note on what shipped.
