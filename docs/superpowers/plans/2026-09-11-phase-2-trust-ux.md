# Phase 2 — Trust UX Implementation Plan

> **For agentic workers:** executed inline (superpowers:executing-plans). **Lean plan, by agreement:** the user is working to a token budget, so this document fixes interfaces, behaviours, tests and acceptance checks; the code lives in the commits rather than being written twice.

**Goal:** Let a member see where an answer comes from and how a figure was reached — evidence drawer, calculation card, linked citations — and keep a stopped answer on reload.

**Architecture:** The backend turns data it already computes into structured payloads: a pure `agent/card.py` builds a calculation card from `calculation_result`, the worksheet and `answer_blocks`; it rides in `tool_calls` (no migration) both in the `done` event and on the persisted message. A public, strictly validated `GET /api/v1/sections/{id}` serves statute text. The frontend adds one drawer with two modes and derives card/stopped state from `tool_calls`, so live and reloaded messages render the same way.

**Spec:** `docs/superpowers/specs/2026-09-10-profiles-and-ui-design.md` §5, §10 (Stop finding).

## Global Constraints

- Figures on the card come only from `calculation_result` (the interpreter), never from answer text. **No card when verification failed** (`report.passed` is false) — the answer was replaced, and its figures are unverified.
- The drawer's worked steps, comparison and assumptions reuse `answer_blocks` (Python-rendered markdown); the frontend does not re-derive them.
- "Estimate" shows unless `execution_policy == "authoritative"`.
- Section ids are validated against `^\d{1,2}[A-C]?-\d{1,4}(\.\d{1,3})?[a-z]?$` before reaching `fetch_sections`, which interpolates ids into an OData filter.
- A stopped partial answer is persisted with `tool_calls=[{"type": "stopped"}]` and `guardrail_passed=None`, and shown as "Stopped — not finished or checked".
- Visual system per spec §3: tokens only, serif only for statute text, shadows only on the drawer.
- Commit trailer as in Phase 1. Backend: `uv run pytest`, `uv run ruff check`. Frontend: `npm test`, `npm run build`, `npm run lint` (≤ 2 warnings, 0 errors).

## Tasks

### Task 1: Calculation card builder (backend, TDD)
- Create `src/pension_chat/agent/card.py`: `DRAWER_BLOCKS = ("WORKED_STEPS", "COMPARISON", "ASSUMPTIONS")`; `calculation_card(state: Mapping[str, Any], sheet: WorkSheet) -> dict | None`. Takes the first formula entry whose `base.ok`; returns `{type: "card", formula_name, section, subsection, estimate, annual_amount, monthly_amount, inputs: [{name, label, value, source}], scenarios: [{label, annual_amount, monthly_amount}], blocks: {slot: markdown}}`. An input's `source` is the sheet fact's source, or `"derived"` when the sheet holds no such fact (e.g. final average salary, derived from history). Labels from `blocks._label`.
- Test `tests/test_card.py`: headline and basis carried; authoritative → not an estimate; input sources; drawer gets only `DRAWER_BLOCKS`; scenarios carried; no successful result → `None`.

### Task 2: Wire the card, and keep a stopped answer (backend)
- `agent/graph.py`: before the final `done`, `card = calculation_card(state, WorkSheet.from_state(state, record_usable=...))` only when `report.passed`; yield `{"type": "done", "state": state, "card": card}`.
- `api/sessions.py::event_stream`: read `card` from `done`; `tool_calls = _audit_trail(final_state) + [card]` for both the persisted row and `sse_done`. Wrap the body in `try/finally`; a `persisted` flag is set once the normal path takes over; in `finally`, if not persisted and text was streamed, write the partial answer inside `anyio.CancelScope(shield=True)` (a client disconnect cancels the stream, and an unshielded await in a cancelled scope is cancelled again).
- Verify: `uv run pytest`; manual abort mid-token via curl, then `GET /sessions/{id}/messages` shows the assistant row with `{"type": "stopped"}`.

### Task 3: Section endpoint (backend, TDD)
- Create `src/pension_chat/api/sections.py`: `SECTION_ID` regex; `SectionResponse{section, title, article, citation, text}`; `assemble_section(section, chunks) -> SectionResponse | None` (natural sort on `subsection_id`, chunks joined by blank lines); `GET /api/v1/sections/{section_id}` → 422 on a malformed id, 404 when nothing is indexed. Register in `main.py`.
- Test `tests/test_sections.py`: accepted ids (`11-134`, `1-160`, `1-113.4a`, `20-104`, `22A-101`); rejected (`""`, `11`, a quote, an OData fragment, a path); natural subsection order; empty → `None`.

### Task 4: Client helpers (frontend, TDD)
- `src/lib/card.ts`: `CalculationCardData`, `CardInput` types; `cardFrom(toolCalls)`, `isStopped(toolCalls)`, `formatMoney(value)`, `SOURCE_LABELS` (record → "Record", stated → "You said", hypothetical → "Scenario", assumed → "Assumed", derived → "Derived", withheld → "On file, not used").
- `src/lib/sections.ts`: `linkSections(markdown, verified)` turns "§ 11-134" / "Section 11-134" into `[…](#section-11-134)` only for verified sections; `sectionFromHref(href)`.
- Tests `src/lib/card.test.ts`, `src/lib/sections.test.ts`.

### Task 5: Drawer, evidence, calculation card (frontend)
- `components/chat/Drawer.tsx` (right panel ≥ md, bottom sheet on phones, Esc and × close), `EvidencePanel.tsx` (fetches the section; loading and error states; statute text in serif), `CalculationCard.tsx` (in-message: headline annual and monthly, Estimate label, § basis, key inputs with source labels, "View calculation"), `CalculationPanel.tsx` (drawer: inputs table, scenarios, `blocks` rendered as markdown).
- `ChatMessage.tsx`: card and stopped state from `tool_calls`; `linkSections` + an `a` renderer that opens evidence; citation chips become buttons. `ChatInterface.tsx`: `panel` state and the drawer.
- Verify: tests, build, lint, palette gate; running app: a calculation question shows the card; View calculation and a § chip open the drawer; reload keeps both; Stop mid-answer then reload shows "Stopped".
