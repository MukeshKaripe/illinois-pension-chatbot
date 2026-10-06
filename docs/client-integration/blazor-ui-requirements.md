# Pension Code Assistant: what to build in Blazor (UI requirements, v1)

**For:** the client's front-end team.
**Companion document:** `blazor-frontend-api-spec.md` (the API contract: endpoints, streamed events, payloads). This document says *what the user sees and how it behaves*; the API spec says *what data drives it*. Section references like "API §3.3" point there.
**Reference implementation:** the React app in `frontend/`, the one shown in the demo. Where this says "as in the reference app", match it.

---

## 1. What the product is

A chat assistant for fund staff (employers/HR) about the Illinois Pension Code, Article 8 (MEABF):
- It answers questions about the law, with citations to the statute.
- It calculates pension figures for named employees (read from PBMS) or described ones ("someone aged 61 with 20 years…").
- Every figure comes from a calculation engine and is shown with how it was worked out.

**v1 scope:**
- **One conversation at a time.** No conversation list, and history is gone when the chat is closed.
- **No sign-in** (internal network only).
- **No profiles.**

## 2. Screen layout

One screen, desktop-first (usable down to 375 px wide):

```
┌───────────────────────────────────────────────────────────────┬─────────────────────┐
│ HEADER: § seal + title          [people chips…]     [New chat]│                     │
├───────────────────────────────────────────────────────────────┤   SIDE PANEL        │
│                                                               │   (opens on demand) │
│   MESSAGE LIST (centred column, max ~720 px wide)             │   - Statute text    │
│     user question (right/plain)                               │   - Calculation     │
│     assistant answer (markdown)                               │     workspace       │
│       └ citation chips · calculation receipt · choice card    │                     │
│     working note (while waiting)                              │                     │
│                                                               │                     │
├───────────────────────────────────────────────────────────────┤                     │
│ SCENARIO CHIPS (when any)                                     │                     │
│ COMPOSER: [ Ask about eligibility, a Section, or an estimate… ]│                     │
│           [Send] / [Stop]                                     │                     │
└───────────────────────────────────────────────────────────────┴─────────────────────┘
```

- **The side panel** slides in from the right and pushes or overlays the chat. On narrow screens it's a full-screen sheet. It closes with ✕ or Esc.
- **The empty state** (before the first question) replaces the message list (§3.2).

## 3. Components to build

### 3.1 Header
- **Left:** a small § seal mark in brass, then the title "Pension Code Assistant" with the subtitle "Illinois Pension Code · Article 8 (MEABF)".
- **Middle: people chips** (§3.9). Shown only when `GET /health` returns `record_source: "pbms"`.
- **Right: a "New chat" button** (`aria-label="New chat"`). It cancels any answer in flight, creates a new session (API §2), and clears the messages, people and scenarios.

### 3.2 Empty state
Shown before the first message:
- **A short welcome:** "Ask about the Pension Code, or name an employee to estimate their pension."
- **Four starter cards** (label + question). Clicking one sends its question. Suggested starters for MEABF; use the client's wording if preferred:

| Label | Question |
|---|---|
| Understand the law | How is the retirement annuity calculated under Section 8-138(g-1)? |
| Check eligibility | What is the difference between Tier 1 and Tier 2? |
| Estimate a pension | A member who joined in 2005 is 61, has 10 years of service and an average salary of $50,000. What would they get if they retired today? |
| Compare options | What does Section 8-137 say about automatic increases after retirement? |

### 3.3 Composer
- **A multi-line text box,** auto-growing to about 6 lines. Placeholder: "Ask about eligibility, a Section, or an estimate…" (`aria-label="Message"`).
- **Enter sends; Shift+Enter inserts a new line.** Never send while an input-method (IME) composition is in progress.
- **The Send button becomes Stop** (`aria-label="Stop generating"`) while an answer streams. Stop cancels the request (API §9).
- **Disabled:** while a session is being created.
- **Empty or whitespace-only input** doesn't send.

### 3.4 User message
Plain text, wrapping, with no markdown. Visually lighter than the answer, aligned or tinted as in the reference app.

### 3.5 Assistant answer
- **The text is markdown,** rendered with raw HTML disabled (API §4). That covers paragraphs, bold, lists and tables; tables scroll horizontally on narrow screens.
- **It streams in as it's written** (API §3.1). Follow API §4 exactly: a half-written table shows as plain text until it completes, and orphan table headers are dropped.
- **"Section 8-138" and "§1-160" references become links** when they're in `verified_citations`, and open the statute panel (§3.10).
- **States:**
  - **Streaming:** text grows, with a subtle caret or pulse at the end.
  - **Replaced** (`replace` event): swap the text; no special marking.
  - **Stopped:** keep the partial text and add a small muted note, "Stopped".
  - **Error:** if no text arrived, show the error message in a muted error style.
- **Under the answer, in this order:**
  1. citation chips (§3.6);
  2. one calculation receipt per card (§3.7);
  3. a choice card, if any (§3.8).

### 3.6 Citation chips
- **One small chip per section** in `verified_citations`, labelled "§8-138", with the section title from `citations` as a tooltip.
- **Click** opens the statute panel.
- **No chips** when `verified_citations` is empty.

### 3.7 Calculation receipt (one line per card)
- **A compact, clickable line,** not a big card. In the demo: "Estimate · $19,770.89 a year · §8-138", prefixed with the person's name when `card.person` is set ("RO_BK TA_NE · Estimate · …").
- **Badge text:** "Estimate" when `estimate`, plus "· experimental for this fund" when `support_level` is `"experimental"`.
- **When `annual_amount` is null** (not payable today), show the first scenario: "At 67 · $28,244.13 a year".
- **Click** opens the calculation workspace (§3.11) for that card.
- **Format amounts** as US currency with cents. The amounts arrive as strings; parse with `decimal`, never `double`.

### 3.8 Choice card (choosing an employee)
Shown when `done.tool_calls` has `subject_suggestion` (API §5.2):
- **The prompt line:** "Which Jane Smith do you mean?", one group per name in `choices`.
- **One button per candidate,** showing department · employer · born YYYY. Never show the `ref`.
- **More than 12 candidates:** group them by department first; the user picks a department, then the person.
- **`unavailable`:** the line "Employee records couldn't be checked just now." and no buttons.
- **Picking a person:**
  - `choose`: call `POST /subjects`; once every name has a pick, re-send the original question automatically.
  - `which_one`: send "About <name>: <question>".
- **After it's answered, or once a later message exists,** the card is disabled.

### 3.9 People chips (header)
- **One chip per person** in the conversation (max 4): "Name · Department". The `ref` is never shown.
- **Active people** (from the `people` event or `GET /subjects` `active`) are highlighted, with a navy outline or fill.
- **Each chip has an ✕** that calls `DELETE /subjects/{ref}`.
- **When empty,** show the muted hint "Name an employee in your question".
- **Refresh the list** after each answer's `done` (`GET /subjects`) and after any add or remove.

### 3.10 Side panel: statute text
- **Opened** from a citation chip or a section link. Loads `GET /sections/{id}` (API §8).
- **Shows:**
  - the title "§8-138", with the section title as subtitle;
  - the citation "40 ILCS 5/8-138";
  - the text **in the serif typeface**, preserving line breaks and the (a)/(b) lettering.
- **States:** loading (a skeleton); "Section not found" for a 404.

### 3.11 Side panel: calculation workspace
Opened from a receipt (§3.7). Everything comes from the card (API §3.3). As in the reference app, top to bottom:
1. **Headline:** the annual figure, large ("$19,770.89 a year"), the monthly figure beneath, the badge, and the person's name if set.
   - **One what-if** ("and at 65?"): that what-if leads the headline, with today's figure beneath it.
   - **Several what-ifs:** today's figure leads, and each what-if is listed below.
   - A scenario labelled "0 more years" is today's figure again; don't show it twice.
   - **Not payable now:** "Retiring now: not payable yet", then the `not_payable_now` reason, then the scenario figure as the headline.
   - The rules are in `cardHeadline`, `frontend/src/lib/card.ts`.
2. **Inputs:** a table of label · value · source.
   - Sources show as the reference app's labels: `record` "Record", `stated` "You said", `hypothetical` "Scenario", `assumed` "Assumed", `derived` "Derived", `statutory_parameter` "Set by the Code", `withheld` "On file, not used". Any other value is shown as-is; null shows nothing.
   - `record_label` (e.g. "PBMS record") replaces "Record" when present.
3. **Formula:** `formula_name` and "40 ILCS 5/<section><subsection>", which links to the statute panel.
4. **How it's worked out:** `blocks.WORKED_STEPS`, rendered as markdown.
5. **Assumptions:** `blocks.ASSUMPTIONS`, as markdown.
6. **Comparison:** `blocks.COMPARISON`, as markdown, if present.
7. **What-ifs so far:** the card's `scenarios`, as label · annual · monthly.
8. **What-if buttons:** "At 60", "At 62", "At 65", "At 67", "+1 year", "+2 years".
   - Each sends a question: "What if they retire at 62?", or "What if <person> retires at 62?" when `person` is set; "What if they retire 1 year later?" for +1.
   - Disabled while an answer is streaming.
9. **Actions:** "Copy summary" (plain text: person, formula, citation, figures, inputs; it shows "Copied" for 2 s), and "Print" (prints the panel only).

### 3.12 Working note (while the answer is being prepared)
- **Shown below the question** until the first answer token.
- **The status line:** the `preamble` text, replaced by `status` stages.
- **Progress steps** from `progress` events, as a small list ("Searching the Code", "Reading Section 8-138", …). Shown **only if the turn has run 4 s or more** (any step with `at ≥ 4`); quick turns show just the status line.
- **Table previews** from `view` events, rendered as markdown, so the worked-steps table can appear before the sentence explaining it.
- **On the first token, the whole working note disappears.** The tables come again inside the answer.

### 3.13 Scenario chips (above the composer)
- **A row headed "Scenarios":** one chip per scenario in `done.scenarios`, showing "at age 65 · $31,267.16".
- **Each chip has an ✕** that calls `DELETE /scenarios?label=…`; the list is replaced with the response.
- **Hidden when empty.** Disabled while streaming.

## 4. Behaviour rules (easy to get wrong)

1. **Only verified things are shown as relied on.** Chips and links come from `verified_citations`, never from all retrieved `citations`.
2. **Never build a figure or a card in the UI.** Show only what the API sends. No card when `guardrail_passed` is false.
3. **Don't show tables twice.** Previews disappear at the first token.
4. **Stop keeps the partial answer.** New chat and closing the page cancel any request in flight.
5. **No raw HTML from answers,** ever (XSS).
6. **The serif typeface is only for statute text;** everything else is sans-serif. Readers can tell the Code speaking from the assistant.
7. **Browser storage failures must not break the page.** Wrap every access.
8. **One conversation per tab.** Keep the session id and token in tab or circuit state, and optionally in `localStorage` for reload restore (API §7).

## 5. Look and feel ("civic calm: statute and seal")

Match the reference app. The design tokens (CSS custom properties; `oklch()` works in all current browsers):

| Token | Value | Use |
|---|---|---|
| `--background` | `oklch(0.984 0.003 250)` | Page |
| `--foreground` | `oklch(0.24 0.03 258)` | Text |
| `--card` | `oklch(1 0 0)` | Panels, cards |
| `--primary` | `oklch(0.40 0.10 258)` | **Navy: the one working accent** (buttons, links, active chips, focus rings) |
| `--primary-foreground` | `oklch(0.985 0.003 250)` | Text on navy |
| `--muted` | `oklch(0.955 0.006 250)` | Subtle fills |
| `--muted-foreground` | `oklch(0.50 0.02 255)` | Secondary text |
| `--border` | `oklch(0.905 0.008 250)` | Hairlines |
| `--destructive` | `oklch(0.577 0.245 27.325)` | Errors only |
| `--seal` | `oklch(0.36 0.10 258)` | The § seal |
| `--brass` | `oklch(0.74 0.11 80)` | **Identity only:** the § seal. Not for buttons or highlights. |
| `--radius` | `0.625rem` | Corner radius (sm 0.6×, lg 1×, xl 1.4×) |

- **Typefaces:** Geist Sans (UI and answers), Source Serif 4 (statute text), Geist Mono (rarely needed). All are free web fonts.
- **No gradients, no blur, no extra accent colours.** Navy for actions, brass for the seal, and that's all.
- **Dark mode is optional for v1.** If built, the reference app's `globals.css` has the dark values.

## 6. Accessibility
- **Everything is keyboard-reachable:** composer, chips, receipt, panel, what-if buttons and choice buttons.
- **Visible focus rings** in navy.
- **The panel** traps focus while open, returns focus to the opener on close, and closes with Esc.
- **The streaming answer region** has `aria-live="polite"`, updated per sentence or batch, not per token.
- **Labels:** "Message", "Send", "Stop generating", "New chat", "Close panel", "Employees in this chat", "Scenarios in this chat".
- **Contrast** meets WCAG AA with the tokens above.

## 7. Performance
- **Render streaming text in batches** (about every 50 ms), not per token.
- **Re-parse markdown** only for the message that's streaming.
- **Prefer Blazor WebAssembly or Auto render mode** for the chat, so the browser streams directly from the API. Blazor Server adds a relay per token (API §10).

## 8. Out of scope for v1

| Not in v1 | Note |
|---|---|
| Sign-in, accounts, profiles, signup | The internal network is the access control |
| Conversation history list / sidebar | Single-session chats |
| Uploading documents | — |
| Admin screens (formula review) | Backend endpoints exist; later |

## 9. Acceptance

The API spec's checklist (API §13), plus:
- [ ] Layout matches §2 at 1440 px and 375 px widths.
- [ ] Empty state with four working starters.
- [ ] Receipt line under every calculated answer; the workspace shows every part of §3.11 that has data.
- [ ] What-if buttons send the right question, including the person's name.
- [ ] The working note shows progress only on slow turns, and disappears at the first token.
- [ ] Statute panel in serif; Esc closes panels; focus returns.
- [ ] Demo script questions behave as in the React app, compared side by side.

Questions: the backend team. To see any behaviour live, run the React reference app (`frontend/`, `npm run dev`) against the same backend.
