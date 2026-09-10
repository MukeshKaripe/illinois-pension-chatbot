# Phase 1 — Visual System and Chat Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dark template with the "Civic calm" design system and rebuild the chat shell — greeting, starter prompts, grouped history, a composer with Stop — as a frontend-only change.

**Architecture:** All colour comes from CSS tokens in `globals.css`; components use semantic Tailwind utilities only. `ChatInterface` keeps state and streaming; presentation moves into small components (`ChatHeader`, `Sidebar`, `EmptyState`, `Composer`, `StatusLine`, `ChatMessage`). Two pure modules (`lib/sse.ts`, `lib/history.ts`) carry the logic worth testing and are tested with Node's built-in runner.

**Tech Stack:** Next.js 16.3 (App Router, Turbopack), React 19, Tailwind v4 + `@tailwindcss/typography`, base-ui `Button` (`src/components/ui/button.tsx`), lucide-react, `next/font/google`, `node --test` (Node 22.18 strips TypeScript types natively — no new dependency).

**Spec:** `docs/superpowers/specs/2026-09-10-profiles-and-ui-design.md` — §3 (visual system) and §4 (chat shell). Read both before starting.

## Global Constraints

- Frontend only. No backend file changes in this phase.
- Colour tokens exactly as spec §3's table; `--ring: var(--primary)`.
- Components (`src/app/**/*.tsx`, `src/components/chat/**/*.tsx`) use only semantic colour utilities (`bg-background`, `text-foreground`, `bg-card`, `bg-muted`, `text-muted-foreground`, `border-border`, `border-input`, `bg-primary`, `text-primary`, `text-destructive`, `bg-sidebar`, `border-sidebar-border`). No `neutral-*`, `blue-*`, `purple-*`, `amber-*`, `red-*`, `bg-white`, `text-white`, `bg-gradient-*`, `backdrop-blur`, `blur-[…]`.
- Dark mode follows `prefers-color-scheme`; no toggle.
- Fonts: Geist Sans for UI; Source Serif 4 for assistant answers and statute quotes at 17px, line-height 1.65; tabular numerals on tables.
- Conversation column max 720px. User turns: right-aligned bubble on `--muted`. Assistant turns: full-width serif text, no bubble.
- Shadows only on menus, popovers and drawers (the mobile sidebar is a drawer). Transitions 150ms ease-out on those only.
- Icons: lucide, 16/18px.
- Copy, verbatim: greeting "Hello"; anonymous note "This is a temporary conversation."; sidebar line "Sign up to keep your conversations"; page title "Illinois Pension Assistant"; anonymous prompts as in Task 5.
- Keep CLAUDE.md's frontend rules: send credential headers only via `credentialHeaders()`; every `localStorage` access goes through `src/lib/api.ts`.
- Per `frontend/AGENTS.md`, consult `node_modules/next/dist/docs/` before using any Next API not already shown here.
- `npm run lint` must not exceed the current baseline of 6 problems; this plan removes most of them. `npm run build` must pass after every task.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01ACgWaDbTNkvpS2Avqwy2FD
  ```
- All commands run from `frontend/` unless stated.

## File Structure

| File | Responsibility |
|---|---|
| `package.json` (modify) | `test` script |
| `tsconfig.json` (modify) | allow `.ts` import extensions so tests type-check under `next build` |
| `src/lib/sse.ts` (create) | Parse SSE frames from a growing buffer |
| `src/lib/sse.test.ts` (create) | Tests for the parser |
| `src/lib/history.ts` (create) | Group sessions into Today / Yesterday / Earlier |
| `src/lib/history.test.ts` (create) | Tests for grouping |
| `src/lib/types.ts` (create) | `Message`, `Citation`, `SessionSummary` |
| `src/lib/starters.ts` (create) | Anonymous starter prompts |
| `src/app/globals.css` (modify) | Tokens, fonts, answer typography, shimmer |
| `src/app/layout.tsx` (modify) | Font loading, metadata |
| `src/components/chat/ChatMessage.tsx` (modify) | One message: user bubble or serif answer + citation chips |
| `src/components/chat/StatusLine.tsx` (create) | Stage label while waiting |
| `src/components/chat/Composer.tsx` (create) | Auto-growing textarea, Send / Stop |
| `src/components/chat/EmptyState.tsx` (create) | Greeting and starter prompts |
| `src/components/chat/Sidebar.tsx` (modify) | New chat, grouped history, account footer |
| `src/components/chat/ChatHeader.tsx` (create) | Sidebar toggle, product name, mobile New chat |
| `src/components/chat/ChatInterface.tsx` (modify) | State, streaming, layout shell |
| `src/app/login/page.tsx` (modify) | Restyle |
| `src/app/signup/page.tsx` (modify) | Restyle (class mapping) |

---

### Task 1: Test runner and SSE frame parser

**Files:**
- Modify: `frontend/package.json` (scripts)
- Modify: `frontend/tsconfig.json` (compilerOptions)
- Create: `frontend/src/lib/sse.ts`
- Test: `frontend/src/lib/sse.test.ts`

**Interfaces:**
- Produces: `type SseEvent = { event: string; data: Record<string, unknown> }`; `parseFrame(frame: string): SseEvent | null`; `takeFrames(buffer: string): { events: SseEvent[]; rest: string }`. Task 7 consumes `takeFrames` and `SseEvent`.

- [ ] **Step 1: Add the test script and allow `.ts` import extensions**

In `package.json` `scripts`, add after `"lint": "eslint"`:

```json
    "lint": "eslint",
    "test": "node --test \"src/**/*.test.ts\""
```

In `tsconfig.json` `compilerOptions`, add after `"noEmit": true,`:

```json
    "allowImportingTsExtensions": true,
```

(Tests import `./sse.ts` with its extension because Node requires it; `next build` type-checks every `.ts` file, and this option — valid because `noEmit` is set — lets it accept that import.)

- [ ] **Step 2: Write the failing test**

Create `src/lib/sse.test.ts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseFrame, takeFrames } from "./sse.ts";

test("parses a frame's event name and JSON data", () => {
  assert.deepEqual(parseFrame('event: token\ndata: {"delta":"Hi"}'), {
    event: "token",
    data: { delta: "Hi" },
  });
});

test("defaults the event name to message", () => {
  assert.deepEqual(parseFrame('data: {"a":1}'), { event: "message", data: { a: 1 } });
});

test("drops a frame with no data or with invalid JSON", () => {
  assert.equal(parseFrame("event: ping"), null);
  assert.equal(parseFrame("data: {nope"), null);
});

test("keeps an incomplete frame as the remainder until it closes", () => {
  const first = takeFrames('event: token\ndata: {"delta":"Hel');
  assert.deepEqual(first.events, []);

  const second = takeFrames(first.rest + 'lo"}\n\nevent: done\ndata: {}\n\n');
  assert.deepEqual(second.events, [
    { event: "token", data: { delta: "Hello" } },
    { event: "done", data: {} },
  ]);
  assert.equal(second.rest, "");
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npm test`
Expected: FAIL — `Cannot find module '.../src/lib/sse.ts'`. (A `MODULE_TYPELESS_PACKAGE_JSON` warning may also print; it is harmless.)

- [ ] **Step 4: Implement the parser**

Create `src/lib/sse.ts`:

```ts
/**
 * Server-sent events, parsed from a buffer that grows one network read at a time.
 *
 * Frames are separated by a blank line and can be split across reads. Parsing
 * each read on its own drops any event whose frame straddles a chunk boundary,
 * which for a token stream means losing words from the middle of an answer — so
 * an incomplete frame is handed back as `rest` for the next read to finish.
 */

export type SseEvent = { event: string; data: Record<string, unknown> };

export function parseFrame(frame: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;

  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

export function takeFrames(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  let rest = buffer;
  let split = rest.indexOf("\n\n");
  while (split !== -1) {
    const parsed = parseFrame(rest.slice(0, split));
    if (parsed) events.push(parsed);
    rest = rest.slice(split + 2);
    split = rest.indexOf("\n\n");
  }
  return { events, rest };
}
```

- [ ] **Step 5: Run tests and the build**

Run: `npm test` — Expected: 4 pass, 0 fail.
Run: `npm run build` — Expected: compiles; TypeScript finishes without errors.

- [ ] **Step 6: Commit**

```bash
git add package.json tsconfig.json src/lib/sse.ts src/lib/sse.test.ts
git commit -m "Frontend: node --test runner, and the SSE frame parser as a tested module" -m "<trailer lines from Global Constraints>"
```

---

### Task 2: History grouping

**Files:**
- Create: `frontend/src/lib/history.ts`
- Test: `frontend/src/lib/history.test.ts`

**Interfaces:**
- Produces: `type HistoryLabel = "Today" | "Yesterday" | "Earlier"`; `type HistoryGroup<T> = { label: HistoryLabel; items: T[] }`; `groupSessions<T extends { last_active_at: string }>(sessions: readonly T[], now: Date): HistoryGroup<T>[]`. Task 6 consumes it.

- [ ] **Step 1: Write the failing test**

Create `src/lib/history.test.ts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { groupSessions } from "./history.ts";

// Local times on both sides, so the result does not depend on the machine's zone.
const now = new Date(2026, 8, 10, 15, 0);
const at = (day: number, hour: number, minute = 0) =>
  new Date(2026, 8, day, hour, minute).toISOString();

test("buckets by local calendar day", () => {
  const groups = groupSessions(
    [
      { id: "a", last_active_at: at(10, 9) },
      { id: "b", last_active_at: at(9, 23, 59) },
      { id: "c", last_active_at: at(1, 12) },
    ],
    now,
  );
  assert.deepEqual(
    groups.map((g) => [g.label, g.items.map((i) => i.id)]),
    [["Today", ["a"]], ["Yesterday", ["b"]], ["Earlier", ["c"]]],
  );
});

test("midnight belongs to the day it starts", () => {
  const groups = groupSessions([{ id: "m", last_active_at: at(10, 0) }], now);
  assert.equal(groups[0].label, "Today");
});

test("sorts newest first within a group", () => {
  const groups = groupSessions(
    [
      { id: "old", last_active_at: at(10, 8) },
      { id: "new", last_active_at: at(10, 14) },
    ],
    now,
  );
  assert.deepEqual(groups[0].items.map((i) => i.id), ["new", "old"]);
});

test("omits empty groups", () => {
  const groups = groupSessions([{ id: "c", last_active_at: at(1, 12) }], now);
  assert.deepEqual(groups.map((g) => g.label), ["Earlier"]);
});

test("no sessions, no groups", () => {
  assert.deepEqual(groupSessions([], now), []);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test`
Expected: FAIL — `Cannot find module '.../src/lib/history.ts'`.

- [ ] **Step 3: Implement**

Create `src/lib/history.ts`:

```ts
/** Sidebar history, grouped the way chat products do: Today, Yesterday, Earlier. */

export type HistoryLabel = "Today" | "Yesterday" | "Earlier";
export type HistoryGroup<T> = { label: HistoryLabel; items: T[] };

const ORDER: readonly HistoryLabel[] = ["Today", "Yesterday", "Earlier"];

const startOfDay = (d: Date) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

export function groupSessions<T extends { last_active_at: string }>(
  sessions: readonly T[],
  now: Date,
): HistoryGroup<T>[] {
  const today = startOfDay(now);
  const yesterday = startOfDay(
    new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1),
  );

  const buckets: Record<HistoryLabel, T[]> = { Today: [], Yesterday: [], Earlier: [] };
  const newestFirst = [...sessions].sort(
    (a, b) => Date.parse(b.last_active_at) - Date.parse(a.last_active_at),
  );
  for (const session of newestFirst) {
    const t = Date.parse(session.last_active_at);
    buckets[t >= today ? "Today" : t >= yesterday ? "Yesterday" : "Earlier"].push(session);
  }

  return ORDER.filter((label) => buckets[label].length > 0).map((label) => ({
    label,
    items: buckets[label],
  }));
}
```

- [ ] **Step 4: Run tests**

Run: `npm test` — Expected: 9 pass (4 SSE + 5 history), 0 fail.

- [ ] **Step 5: Commit**

```bash
git add src/lib/history.ts src/lib/history.test.ts
git commit -m "Frontend: group conversation history into Today, Yesterday and Earlier" -m "<trailer lines>"
```

---

### Task 3: Design tokens and fonts

**Files:**
- Modify: `frontend/src/app/globals.css` (whole file)
- Modify: `frontend/src/app/layout.tsx` (whole file)

**Interfaces:**
- Produces: utilities `font-serif`, classes `.prose.answer` and `.shimmer`, and the token set every later task uses.

Note a bug this fixes: the current `@theme inline` block says `--font-sans: var(--font-sans)`, which refers to itself, so Geist was never actually applied.

- [ ] **Step 1: Replace `src/app/globals.css`**

```css
@import "tailwindcss";
@import "tw-animate-css";
@plugin "@tailwindcss/typography";
@import "shadcn/tailwind.css";

/* Dark follows the system setting; there is no toggle (spec §3). */
@custom-variant dark (@media (prefers-color-scheme: dark));

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --font-sans: var(--font-geist-sans);
  --font-serif: var(--font-source-serif);
  --font-mono: var(--font-geist-mono);
  --font-heading: var(--font-geist-sans);
  --color-sidebar-ring: var(--sidebar-ring);
  --color-sidebar-border: var(--sidebar-border);
  --color-sidebar-accent-foreground: var(--sidebar-accent-foreground);
  --color-sidebar-accent: var(--sidebar-accent);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar: var(--sidebar);
  --color-chart-5: var(--chart-5);
  --color-chart-4: var(--chart-4);
  --color-chart-3: var(--chart-3);
  --color-chart-2: var(--chart-2);
  --color-chart-1: var(--chart-1);
  --color-ring: var(--ring);
  --color-input: var(--input);
  --color-border: var(--border);
  --color-destructive: var(--destructive);
  --color-accent-foreground: var(--accent-foreground);
  --color-accent: var(--accent);
  --color-muted-foreground: var(--muted-foreground);
  --color-muted: var(--muted);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-secondary: var(--secondary);
  --color-primary-foreground: var(--primary-foreground);
  --color-primary: var(--primary);
  --color-popover-foreground: var(--popover-foreground);
  --color-popover: var(--popover);
  --color-card-foreground: var(--card-foreground);
  --color-card: var(--card);
  --radius-sm: calc(var(--radius) * 0.6);
  --radius-md: calc(var(--radius) * 0.8);
  --radius-lg: var(--radius);
  --radius-xl: calc(var(--radius) * 1.4);
  --radius-2xl: calc(var(--radius) * 1.8);
  --radius-3xl: calc(var(--radius) * 2.2);
  --radius-4xl: calc(var(--radius) * 2.6);
}

/* "Civic calm" — warm paper, deep ink, one teal accent (spec §3). */
:root {
  color-scheme: light;
  --radius: 0.625rem;
  --background: oklch(0.985 0.004 85);
  --foreground: oklch(0.23 0.02 250);
  --card: oklch(1 0 0);
  --card-foreground: oklch(0.23 0.02 250);
  --popover: oklch(1 0 0);
  --popover-foreground: oklch(0.23 0.02 250);
  --primary: oklch(0.46 0.08 195);
  --primary-foreground: oklch(0.985 0.004 85);
  --secondary: oklch(0.96 0.006 85);
  --secondary-foreground: oklch(0.23 0.02 250);
  --muted: oklch(0.96 0.006 85);
  --muted-foreground: oklch(0.50 0.015 250);
  --accent: oklch(0.96 0.006 85);
  --accent-foreground: oklch(0.23 0.02 250);
  --destructive: oklch(0.577 0.245 27.325);
  --border: oklch(0.90 0.008 85);
  --input: oklch(0.90 0.008 85);
  --ring: var(--primary);
  --chart-1: oklch(0.87 0 0);
  --chart-2: oklch(0.556 0 0);
  --chart-3: oklch(0.439 0 0);
  --chart-4: oklch(0.371 0 0);
  --chart-5: oklch(0.269 0 0);
  --sidebar: oklch(0.965 0.006 85);
  --sidebar-foreground: oklch(0.23 0.02 250);
  --sidebar-primary: oklch(0.46 0.08 195);
  --sidebar-primary-foreground: oklch(0.985 0.004 85);
  --sidebar-accent: oklch(0.93 0.008 85);
  --sidebar-accent-foreground: oklch(0.23 0.02 250);
  --sidebar-border: oklch(0.90 0.008 85);
  --sidebar-ring: var(--primary);
}

@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --background: oklch(0.19 0.012 250);
    --foreground: oklch(0.93 0.006 85);
    --card: oklch(0.23 0.012 250);
    --card-foreground: oklch(0.93 0.006 85);
    --popover: oklch(0.23 0.012 250);
    --popover-foreground: oklch(0.93 0.006 85);
    --primary: oklch(0.72 0.09 195);
    --primary-foreground: oklch(0.19 0.012 250);
    --secondary: oklch(0.26 0.012 250);
    --secondary-foreground: oklch(0.93 0.006 85);
    --muted: oklch(0.26 0.012 250);
    --muted-foreground: oklch(0.70 0.012 250);
    --accent: oklch(0.26 0.012 250);
    --accent-foreground: oklch(0.93 0.006 85);
    --destructive: oklch(0.704 0.191 22.216);
    --border: oklch(0.32 0.012 250);
    --input: oklch(0.32 0.012 250);
    --sidebar: oklch(0.21 0.012 250);
    --sidebar-foreground: oklch(0.93 0.006 85);
    --sidebar-primary: oklch(0.72 0.09 195);
    --sidebar-primary-foreground: oklch(0.19 0.012 250);
    --sidebar-accent: oklch(0.26 0.012 250);
    --sidebar-accent-foreground: oklch(0.93 0.006 85);
    --sidebar-border: oklch(0.32 0.012 250);
  }
}

@layer base {
  * {
    @apply border-border outline-ring/50;
  }
  body {
    @apply bg-background text-foreground;
  }
  html {
    @apply font-sans;
  }
  table {
    font-variant-numeric: tabular-nums;
  }
}

/*
 * Assistant answers read as a set document: serif body, UI face for headings and
 * tables (spec §3). Unlayered, so it outranks the typography plugin's `.prose`.
 */
.prose.answer {
  font-family: var(--font-serif);
  font-size: 17px;
  line-height: 1.65;
  --tw-prose-body: var(--foreground);
  --tw-prose-headings: var(--foreground);
  --tw-prose-bold: var(--foreground);
  --tw-prose-links: var(--primary);
  --tw-prose-counters: var(--muted-foreground);
  --tw-prose-bullets: var(--muted-foreground);
  --tw-prose-hr: var(--border);
  --tw-prose-quotes: var(--foreground);
  --tw-prose-quote-borders: var(--primary);
  --tw-prose-code: var(--foreground);
  --tw-prose-pre-code: var(--foreground);
  --tw-prose-pre-bg: var(--muted);
  --tw-prose-th-borders: var(--border);
  --tw-prose-td-borders: var(--border);
}
.prose.answer :where(h1, h2, h3, h4, table) {
  font-family: var(--font-sans);
}
.prose.answer :where(table) {
  font-size: 15px;
}

/* The waiting line: a slow sweep of ink across muted text (spec §4). */
@keyframes shimmer {
  from { background-position: 100% 0; }
  to { background-position: -100% 0; }
}
.shimmer {
  background: linear-gradient(
    90deg,
    var(--muted-foreground) 40%,
    var(--foreground) 50%,
    var(--muted-foreground) 60%
  );
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: shimmer 1.8s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .shimmer {
    animation: none;
    background: none;
    color: var(--muted-foreground);
  }
}
```

- [ ] **Step 2: Replace `src/app/layout.tsx`**

```tsx
import type { Metadata } from "next";
import { Geist, Geist_Mono, Source_Serif_4 } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// Variable font: no weight needed (node_modules/next/dist/docs/.../font.md).
const sourceSerif = Source_Serif_4({ variable: "--font-source-serif", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Illinois Pension Assistant",
  description:
    "Answers about the Illinois Pension Code, with cited statute text and calculated estimates.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${sourceSerif.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
```

- [ ] **Step 3: Build**

Run: `npm run build` — Expected: passes. (Pages still carry their old hard-coded dark classes; they are replaced in Tasks 4–8.)

- [ ] **Step 4: Commit**

```bash
git add src/app/globals.css src/app/layout.tsx
git commit -m "Frontend: Civic calm tokens, and load the fonts the theme was only naming" -m "<trailer lines>"
```

---

### Task 4: Shared types, ChatMessage and StatusLine

**Files:**
- Create: `frontend/src/lib/types.ts`
- Modify: `frontend/src/components/chat/ChatMessage.tsx` (whole file)
- Create: `frontend/src/components/chat/StatusLine.tsx`

**Interfaces:**
- Produces: `type Citation = { section: string; title?: string }`; `type Message = { id: string; role: string; content: string; created_at: string; citations?: Citation[]; verified_citations?: string[]; tool_calls?: unknown[]; guardrail_passed?: boolean; stopped?: boolean }`; `type SessionSummary = { id: string; title: string | null; started_at?: string; last_active_at: string }`; `ChatMessage({ message: Message; isStreaming?: boolean })`; `StatusLine({ text: string })`.

- [ ] **Step 1: Create `src/lib/types.ts`**

```ts
/** Shapes the chat UI holds. The server's JSON is read into these at the edge. */

export type Citation = { section: string; title?: string };

export type Message = {
  id: string;
  /** "user" for the member; anything else is the assistant. */
  role: string;
  content: string;
  created_at: string;
  citations?: Citation[];
  verified_citations?: string[];
  tool_calls?: unknown[];
  guardrail_passed?: boolean;
  /** The member pressed Stop before the answer finished. */
  stopped?: boolean;
};

export type SessionSummary = {
  id: string;
  title: string | null;
  started_at?: string;
  last_active_at: string;
};
```

- [ ] **Step 2: Replace `src/components/chat/ChatMessage.tsx`**

```tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BookOpen } from "lucide-react";
import type { Message } from "@/lib/types";

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
}

export function ChatMessage({ message, isStreaming }: ChatMessageProps) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-muted px-4 py-2.5">
          {message.content}
        </div>
      </div>
    );
  }

  // Nothing streamed yet: the status line below the list speaks for it.
  if (!message.content && isStreaming) return null;

  // Markdown is re-parsed on every token, so a half-written table renders as
  // literal pipes until its row closes. Everything up to the last blank line is
  // complete enough to parse; the tail after it is shown as plain text and
  // re-renders as markdown the moment the block closes.
  const split = isStreaming ? message.content.lastIndexOf("\n\n") : -1;
  const settled = split > 0 ? message.content.slice(0, split) : message.content;
  const tail = split > 0 ? message.content.slice(split) : "";

  return (
    <div>
      {message.content && (
        <div className="prose answer max-w-none">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              // A four-column comparison has nowhere to go in a narrow column.
              // eslint-disable-next-line @typescript-eslint/no-unused-vars
              table: ({ node, ...props }) => (
                <div className="overflow-x-auto">
                  <table {...props} />
                </div>
              ),
            }}
          >
            {settled}
          </ReactMarkdown>
          {tail && <div className="whitespace-pre-wrap">{tail}</div>}
        </div>
      )}

      {message.stopped && <p className="mt-2 text-xs text-muted-foreground">Stopped</p>}

      {/* Only the provisions the answer actually relied on, and that passed
          verification. Listing every retrieved section implied a reliance the
          answer did not have. */}
      {message.verified_citations && message.verified_citations.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {message.verified_citations.map((section) => {
            const detail = (message.citations ?? []).find((c) => c.section === section);
            return (
              <div
                key={section}
                className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground"
                title={detail?.title ?? ""}
              >
                <BookOpen size={12} className="text-primary" />
                <span className="font-medium text-foreground">40 ILCS 5/{section}</span>
                {detail?.title && <span className="max-w-[16rem] truncate">{detail.title}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create `src/components/chat/StatusLine.tsx`**

```tsx
/**
 * What the agent is doing while the member waits.
 *
 * The server sends lowercase stage labels ("searching the Pension Code") from
 * `agent/graph.py::STAGE_LABELS`, and a preamble sentence before any of them.
 * Both are shown as one calm line — capitalised, ending in an ellipsis.
 */
export function StatusLine({ text }: { text: string }) {
  if (!text) return null;
  const trimmed = text.trim().replace(/[.…]+$/, "");
  const label = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return (
    <p role="status" aria-live="polite" className="shimmer text-sm">
      {label}…
    </p>
  );
}
```

- [ ] **Step 4: Build and lint**

Run: `npm run build` — Expected: passes (the old `ChatInterface` passes `any` messages, which still type-check).
Run: `npm run lint` — Expected: ≤ 6 problems; the `ChatMessage.tsx` `any` error is gone.

- [ ] **Step 5: Commit**

```bash
git add src/lib/types.ts src/components/chat/ChatMessage.tsx src/components/chat/StatusLine.tsx
git commit -m "Frontend: answers as serif text, questions as bubbles, and a calm status line" -m "<trailer lines>"
```

---

### Task 5: Composer, starter prompts and EmptyState

**Files:**
- Create: `frontend/src/lib/starters.ts`
- Create: `frontend/src/components/chat/Composer.tsx`
- Create: `frontend/src/components/chat/EmptyState.tsx`

**Interfaces:**
- Produces: `ANONYMOUS_STARTERS: readonly string[]`; `Composer({ value: string; onChange: (value: string) => void; onSubmit: () => void; onStop: () => void; isStreaming: boolean; disabled: boolean })`; `EmptyState({ signedIn: boolean; onPick: (prompt: string) => void; disabled: boolean })`.
- Profile-based starter selection (spec §4's rule table) is built in Phase 3, when profile facts reach the client; until then signed-in users see these prompts too (spec §4).

- [ ] **Step 1: Create `src/lib/starters.ts`**

```ts
/**
 * Starter prompts for the empty chat.
 *
 * Fixed strings, never a model call. Signed-in members see these too until
 * Phase 3 brings their profile to the client and the rules in spec §4 can pick
 * prompts from its facts.
 */
export const ANONYMOUS_STARTERS: readonly string[] = [
  "What does Article 11 cover?",
  "How is a pension calculated for a Chicago laborer?",
  "What is the difference between Tier 1 and Tier 2?",
  "Estimate a pension for someone aged 60 with 25 years of service.",
];
```

- [ ] **Step 2: Create `src/components/chat/Composer.tsx`**

```tsx
"use client";

import { useEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled: boolean;
}

/** About eight lines, after which the textarea scrolls. */
const MAX_HEIGHT_PX = 200;

export function Composer({ value, onChange, onSubmit, onStop, isStreaming, disabled }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with the text: reset to auto so it can also shrink, then fit.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value]);

  const canSend = value.trim().length > 0 && !disabled && !isStreaming;

  return (
    <div className="flex items-end gap-2 rounded-xl border border-input bg-card p-2 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/30">
      <textarea
        ref={ref}
        rows={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          // Enter sends, Shift+Enter is a newline; never send mid-composition
          // (an IME's Enter confirms the character, not the message).
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSubmit();
          }
        }}
        placeholder="Ask about eligibility, a Section, or an estimate…"
        aria-label="Message"
        className="max-h-[200px] flex-1 resize-none bg-transparent px-2 py-1.5 text-base outline-none placeholder:text-muted-foreground"
      />
      {isStreaming ? (
        <Button type="button" variant="outline" size="icon-lg" onClick={onStop} aria-label="Stop generating">
          <Square className="size-3.5 fill-current" />
        </Button>
      ) : (
        <Button type="button" size="icon-lg" onClick={onSubmit} disabled={!canSend} aria-label="Send">
          <ArrowUp className="size-[18px]" />
        </Button>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create `src/components/chat/EmptyState.tsx`**

```tsx
import { ANONYMOUS_STARTERS } from "@/lib/starters";

interface EmptyStateProps {
  signedIn: boolean;
  onPick: (prompt: string) => void;
  /** True while the conversation is still being created. */
  disabled: boolean;
}

export function EmptyState({ signedIn, onPick, disabled }: EmptyStateProps) {
  return (
    <div className="mx-auto flex min-h-full w-full max-w-[720px] flex-col justify-center px-4 py-12">
      <h2 className="font-serif text-4xl tracking-tight">Hello</h2>
      <p className="mt-2 text-muted-foreground">
        Ask about your pension under the Illinois Pension Code — what a Section says, who it
        covers, or roughly what a pension would come to.
      </p>

      <div className="mt-8 grid gap-2 sm:grid-cols-2">
        {ANONYMOUS_STARTERS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            disabled={disabled}
            onClick={() => onPick(prompt)}
            className="rounded-lg border border-border bg-card px-4 py-3 text-left text-sm hover:bg-muted disabled:opacity-50"
          >
            {prompt}
          </button>
        ))}
      </div>

      {!signedIn && (
        <p className="mt-6 text-sm text-muted-foreground">This is a temporary conversation.</p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Build**

Run: `npm run build` — Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add src/lib/starters.ts src/components/chat/Composer.tsx src/components/chat/EmptyState.tsx
git commit -m "Frontend: a composer with Stop, and a greeting with starter prompts" -m "<trailer lines>"
```

---

### Task 6: Sidebar and ChatHeader

**Files:**
- Modify: `frontend/src/components/chat/Sidebar.tsx` (whole file)
- Create: `frontend/src/components/chat/ChatHeader.tsx`

**Interfaces:**
- Consumes: `groupSessions` (Task 2), `SessionSummary` (Task 4), `Button`, `buttonVariants`.
- Produces: `Sidebar({ sessions: SessionSummary[]; currentSessionId: string | null; onSelectSession: (id: string) => void; onNewSession: () => void; onLogout: () => void; signedIn: boolean })`; `ChatHeader({ onToggleSidebar: () => void; onNewChat: () => void; showNewChatOnDesktop: boolean })`.

- [ ] **Step 1: Replace `src/components/chat/Sidebar.tsx`**

```tsx
import Link from "next/link";
import { LogOut, Plus, UserPlus } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { groupSessions } from "@/lib/history";
import type { SessionSummary } from "@/lib/types";

interface SidebarProps {
  sessions: SessionSummary[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onLogout: () => void;
  signedIn: boolean;
}

export function Sidebar({
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  onLogout,
  signedIn,
}: SidebarProps) {
  const groups = groupSessions(sessions, new Date());

  return (
    <div className="flex h-full w-72 flex-col">
      <div className="p-3">
        <Button variant="outline" size="lg" className="h-10 w-full justify-start" onClick={onNewSession}>
          <Plus />
          New chat
        </Button>
      </div>

      <nav aria-label="Conversations" className="flex-1 overflow-y-auto px-3 pb-3">
        {/*
          An anonymous visitor has no cross-visit history to list, and this is a
          product decision rather than a gap: there is no identity to
          authenticate a list against beyond the token this one browser holds.
          The current conversation survives a reload; earlier ones are what an
          account is for, so say that instead of showing an empty list.
        */}
        {signedIn ? (
          groups.map((group) => (
            <section key={group.label} className="mt-4 first:mt-1">
              <h3 className="px-2 pb-1 text-xs font-medium text-muted-foreground">{group.label}</h3>
              <ul className="space-y-0.5">
                {group.items.map((session) => {
                  const current = session.id === currentSessionId;
                  return (
                    <li key={session.id}>
                      <button
                        type="button"
                        onClick={() => onSelectSession(session.id)}
                        aria-current={current ? "page" : undefined}
                        className={`w-full truncate rounded-md px-2 py-1.5 text-left text-sm ${
                          current ? "bg-sidebar-accent text-foreground" : "text-foreground/80 hover:bg-sidebar-accent"
                        }`}
                      >
                        {session.title || "New conversation"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))
        ) : (
          <div className="mt-2 space-y-3 px-2 text-sm">
            <p className="text-muted-foreground">Sign up to keep your conversations</p>
            <Link href="/signup" className={buttonVariants({ size: "lg", className: "h-10 w-full" })}>
              <UserPlus />
              Create an account
            </Link>
            <Link href="/login" className="block text-center text-xs text-muted-foreground hover:text-foreground">
              or sign in
            </Link>
          </div>
        )}
      </nav>

      {signedIn && (
        <div className="border-t border-sidebar-border p-3">
          <Button variant="ghost" className="h-9 w-full justify-start" onClick={onLogout}>
            <LogOut />
            Sign out
          </Button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `src/components/chat/ChatHeader.tsx`**

```tsx
import { PanelLeft, SquarePen } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ChatHeaderProps {
  onToggleSidebar: () => void;
  onNewChat: () => void;
  /** On a phone the sidebar is hidden by default, so New chat always shows there. */
  showNewChatOnDesktop: boolean;
}

export function ChatHeader({ onToggleSidebar, onNewChat, showNewChatOnDesktop }: ChatHeaderProps) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-1 border-b border-border px-3">
      <Button variant="ghost" size="icon" onClick={onToggleSidebar} aria-label="Toggle sidebar">
        <PanelLeft className="size-[18px]" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        onClick={onNewChat}
        aria-label="New chat"
        className={showNewChatOnDesktop ? "" : "md:hidden"}
      >
        <SquarePen className="size-[18px]" />
      </Button>
      <h1 className="ml-2 text-sm font-semibold">Illinois Pension Assistant</h1>
      {/* Phase 3: the profile chip sits at the right end of this bar. */}
    </header>
  );
}
```

- [ ] **Step 3: Build and lint**

Run: `npm run build` — Expected: passes.
Run: `npm run lint` — Expected: ≤ 6 problems; the `Sidebar.tsx` `any` error is gone.

- [ ] **Step 4: Commit**

```bash
git add src/components/chat/Sidebar.tsx src/components/chat/ChatHeader.tsx
git commit -m "Frontend: grouped history in the sidebar, and a quiet header" -m "<trailer lines>"
```

---

### Task 7: ChatInterface — layout shell, streaming, Stop

**Files:**
- Modify: `frontend/src/components/chat/ChatInterface.tsx` (whole file)

**Interfaces:**
- Consumes: `takeFrames`, `SseEvent` (Task 1); `Citation`, `Message`, `SessionSummary` (Task 4); `ChatMessage`, `StatusLine` (Task 4); `Composer`, `EmptyState` (Task 5); `Sidebar`, `ChatHeader` (Task 6); `API_BASE_URL`, `anonymousSession`, `auth`, `credentialHeaders`, `fetchWithAuth` from `@/lib/api`.
- Produces: `ChatInterface()` (default render of `app/page.tsx`, unchanged).

Behaviour this task must keep from commit `1be47ff`: "New chat" clears the view before the request, a stale response from an earlier click is ignored, Send stays disabled while the session is pending, `setSessions` uses functional updates.

New behaviour:
- **Stop** aborts the fetch; the partial answer stays, marked `stopped`.
- **New chat and switching conversations abort** an in-flight answer (simple and predictable; background answers are out of scope).
- **A conversation created in this browser is not re-fetched** — its messages are known to be empty, and the fetch could land after the member's first message and wipe it.
- **A stale message load is ignored** when the member has already switched again.
- **Sidebar state is `boolean | null`**: `null` means the default — shown on desktop, hidden on phones — decided by CSS, so there is no hydration mismatch and no flash.
- **Auto-scroll only while pinned** to the bottom (within 120px).
- **The sidebar title** is set from the first message locally, as a stand-in until a reload fetches the server's title.

- [ ] **Step 1: Replace `src/components/chat/ChatInterface.tsx`**

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  API_BASE_URL,
  anonymousSession,
  auth,
  credentialHeaders,
  fetchWithAuth,
} from "@/lib/api";
import { takeFrames, type SseEvent } from "@/lib/sse";
import type { Citation, Message, SessionSummary } from "@/lib/types";
import { ChatHeader } from "./ChatHeader";
import { ChatMessage } from "./ChatMessage";
import { Composer } from "./Composer";
import { EmptyState } from "./EmptyState";
import { Sidebar } from "./Sidebar";
import { StatusLine } from "./StatusLine";

const isDesktop = () =>
  typeof window !== "undefined" && window.matchMedia("(min-width: 768px)").matches;

/** Long enough to recognise a conversation by in the sidebar. */
const LOCAL_TITLE_LENGTH = 60;
/** How close to the bottom still counts as "reading along". */
const PIN_THRESHOLD_PX = 120;

export function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  // null = the default: shown on desktop, hidden on a phone, decided by CSS.
  const [sidebar, setSidebar] = useState<boolean | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [agentStatus, setAgentStatus] = useState("");
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [signedIn, setSignedIn] = useState(false);
  const router = useRouter();

  // Guards against a stale response landing after a second click: whichever
  // call started most recently is the one allowed to set state.
  const sessionRequestId = useRef(0);
  // Conversations created in this browser, whose messages are known to be empty.
  const createdHere = useRef(new Set<string>());
  // The answer in flight, so Stop, New chat and switching can abort it.
  const abortRef = useRef<AbortController | null>(null);
  // Follow the answer down only while the reader is already at the bottom.
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  const closeSidebarOnPhone = () => {
    if (!isDesktop()) setSidebar(null);
  };

  const createNewSession = async () => {
    // Clear the view before the network call, not after — the backend round
    // trip to Azure Postgres is real (see api/sessions.py::create_session),
    // and a member should see the empty conversation the instant they click
    // "New chat" rather than watch the old one sit there for it.
    abortRef.current?.abort();
    const requestId = ++sessionRequestId.current;
    setMessages([]);
    setSessionId(null);
    closeSidebarOnPhone();

    try {
      const res = await fetchWithAuth("/sessions", { method: "POST" });
      if (requestId !== sessionRequestId.current) return;
      if (res.ok) {
        const data = await res.json();
        // The backend returns a session_token only for an anonymous session,
        // and it is the only thing that will ever reach this conversation
        // again — row-level security scopes it by nothing else.
        if (data.session_token) {
          anonymousSession.remember(data.session_id, data.session_token);
        }
        const now = new Date().toISOString();
        createdHere.current.add(data.session_id);
        setSessions((prev) => [
          { id: data.session_id, title: null, started_at: now, last_active_at: now },
          ...prev,
        ]);
        setSessionId(data.session_id);
      }
    } catch (err) {
      console.error("Failed to create session", err);
    }
  };

  // Pick up where this browser left off.
  //
  // Signed in, that means the session list. Anonymous, it means the one
  // conversation whose token this browser is holding: `GET /sessions` returns
  // an empty list without an identity by design (architecture doc 8), so the
  // stored token is the only way back to a conversation after a reload.
  useEffect(() => {
    const restore = async () => {
      const isSignedIn = auth.isSignedIn();
      setSignedIn(isSignedIn);

      if (!isSignedIn) {
        const existing = anonymousSession.id();
        if (existing) setSessionId(existing);
        else createNewSession();
        return;
      }

      try {
        const res = await fetchWithAuth("/sessions");
        if (res.ok) {
          const data: SessionSummary[] = await res.json();
          setSessions(data);
          if (data.length > 0) setSessionId(data[0].id);
          else createNewSession();
        }
      } catch (err) {
        console.error("Failed to load sessions", err);
      }
    };
    restore();
  }, []);

  // Load messages when the conversation changes.
  useEffect(() => {
    if (!sessionId || createdHere.current.has(sessionId)) return;
    let stale = false;
    const loadMessages = async () => {
      try {
        const res = await fetchWithAuth(`/sessions/${sessionId}/messages`);
        if (res.ok && !stale) setMessages(await res.json());
      } catch (err) {
        console.error("Failed to load messages", err);
      }
    };
    loadMessages();
    return () => {
      stale = true;
    };
  }, [sessionId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages, agentStatus]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < PIN_THRESHOLD_PX;
  };

  const selectSession = (id: string) => {
    abortRef.current?.abort();
    setSessionId(id);
    closeSidebarOnPhone();
  };

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || !sessionId || isStreaming) return;

    const now = new Date().toISOString();
    const aiMessageId = `a-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", content, created_at: now },
      {
        id: aiMessageId,
        role: "ai",
        content: "",
        created_at: now,
        citations: [],
        verified_citations: [],
        tool_calls: [],
      },
    ]);
    setSessions((prev) =>
      prev.map((s) =>
        s.id === sessionId
          ? { ...s, title: s.title || content.slice(0, LOCAL_TITLE_LENGTH), last_active_at: now }
          : s,
      ),
    );
    setInput("");
    setIsStreaming(true);
    setAgentStatus("Thinking");
    setStreamingMessageId(aiMessageId);
    pinnedToBottom.current = true;

    const controller = new AbortController();
    abortRef.current = controller;

    const update = (patch: Partial<Message>) =>
      setMessages((prev) => prev.map((m) => (m.id === aiMessageId ? { ...m, ...patch } : m)));

    let aiContent = "";
    let sawAnswerToken = false;

    const apply = ({ event, data }: SseEvent) => {
      switch (event) {
        case "preamble":
          // Shown immediately, before any model call, so the member sees a
          // response within milliseconds rather than staring at nothing for
          // the several seconds retrieval and generation take.
          setAgentStatus(String(data.text ?? ""));
          break;
        case "status":
          if (!sawAnswerToken) setAgentStatus(String(data.stage ?? ""));
          break;
        case "token":
          sawAnswerToken = true;
          setAgentStatus("");
          aiContent += String(data.delta ?? "");
          update({ content: aiContent });
          break;
        case "replace":
          // Verification rejected what was streamed; swap it out entirely.
          aiContent = String(data.text ?? "");
          update({ content: aiContent });
          break;
        case "done":
          setAgentStatus("");
          update({
            id: data.message_id ? String(data.message_id) : aiMessageId,
            citations: (data.citations as Citation[] | undefined) ?? [],
            verified_citations: (data.verified_citations as string[] | undefined) ?? [],
            tool_calls: (data.tool_calls as unknown[] | undefined) ?? [],
            guardrail_passed: data.guardrail_passed !== false,
          });
          break;
        case "error":
          setAgentStatus("");
          aiContent = aiContent || String(data.message ?? "Something went wrong.");
          update({ content: aiContent });
          break;
      }
    };

    try {
      // Not fetchWithAuth: this needs the raw stream body. The credential
      // headers still come from the same place, because a tokenless browser
      // here used to send the literal string "Bearer null" — which is a token
      // as far as the header is concerned, fails to decode, and 401s.
      const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...credentialHeaders() },
        body: JSON.stringify({ content }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, rest } = takeFrames(buffer);
        buffer = rest;
        events.forEach(apply);
      }
      // Flush a final frame the server closed without a trailing blank line.
      takeFrames(buffer + decoder.decode() + "\n\n").events.forEach(apply);
    } catch (err) {
      if (controller.signal.aborted) {
        update({ stopped: true });
      } else {
        console.error("Chat error:", err);
        update({ content: aiContent || "Something went wrong. Please try again." });
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setIsStreaming(false);
      setAgentStatus("");
      setStreamingMessageId(null);
    }
  };

  const sidebarClass =
    sidebar === true
      ? "translate-x-0"
      : sidebar === false
        ? "-translate-x-full md:hidden"
        : "-translate-x-full md:translate-x-0";

  return (
    <div className="flex h-dvh overflow-hidden bg-background text-foreground">
      {sidebar === true && (
        <button
          type="button"
          aria-label="Close sidebar"
          className="fixed inset-0 z-30 bg-foreground/20 md:hidden"
          onClick={() => setSidebar(null)}
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex border-r border-sidebar-border bg-sidebar shadow-lg transition-transform duration-150 ease-out md:static md:z-auto md:shadow-none md:transition-none ${sidebarClass}`}
      >
        <Sidebar
          sessions={sessions}
          currentSessionId={sessionId}
          onSelectSession={selectSession}
          onNewSession={createNewSession}
          signedIn={signedIn}
          onLogout={() => {
            auth.signOut();
            router.push("/login");
          }}
        />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <ChatHeader
          onToggleSidebar={() => setSidebar((prev) => !(prev ?? isDesktop()))}
          onNewChat={createNewSession}
          showNewChatOnDesktop={sidebar === false}
        />

        <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <EmptyState signedIn={signedIn} onPick={send} disabled={!sessionId} />
          ) : (
            <div className="mx-auto w-full max-w-[720px] space-y-8 px-4 py-8">
              {messages.map((m) => (
                <ChatMessage key={m.id} message={m} isStreaming={m.id === streamingMessageId} />
              ))}
              <StatusLine text={agentStatus} />
            </div>
          )}
        </div>

        <div className="mx-auto w-full max-w-[720px] px-4 pb-4">
          <Composer
            value={input}
            onChange={setInput}
            onSubmit={() => send(input)}
            onStop={() => abortRef.current?.abort()}
            isStreaming={isStreaming}
            disabled={!sessionId}
          />
          <p className="mt-2 text-center text-xs text-muted-foreground">
            Estimates and summaries based on the Illinois Pension Code. For an official
            determination, contact your fund.
          </p>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Tests, build and lint**

Run: `npm test` — Expected: 9 pass.
Run: `npm run build` — Expected: passes.
Run: `npm run lint` — Expected: at most 2 problems (the `any` in `login/page.tsx` and the `window.location` warning in `lib/api.ts`). If a `react-hooks` rule flags `createNewSession` or the restore effect, keep the effect as written and report it; do not add dependencies that would re-run the restore.

- [ ] **Step 3: Commit**

```bash
git add src/components/chat/ChatInterface.tsx
git commit -m "Frontend: the new chat shell — greeting, grouped history, composer with Stop" -m "<trailer lines>"
```

---

### Task 8: Login and signup restyle, and the palette gate

**Files:**
- Modify: `frontend/src/app/login/page.tsx` (whole file)
- Modify: `frontend/src/app/signup/page.tsx` (classes only; no logic change)

**Interfaces:**
- Consumes: `buttonVariants` from `@/components/ui/button`.

- [ ] **Step 1: Replace `src/app/login/page.tsx`**

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API_BASE_URL, auth } from "@/lib/api";
import { buttonVariants } from "@/components/ui/button";

const field =
  "w-full rounded-lg border border-input bg-background px-3.5 py-2.5 text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/30";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const router = useRouter();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);

    try {
      // The login endpoint is OAuth2's password flow: form-encoded, "username".
      const formData = new URLSearchParams();
      formData.append("username", email);
      formData.append("password", password);

      const response = await fetch(`${API_BASE_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: formData,
      });
      if (!response.ok) throw new Error("Invalid email or password");

      const data = await response.json();
      auth.signIn(data.access_token);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred during login");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4 py-12 text-foreground">
      <div className="w-full max-w-sm space-y-8">
        <div className="space-y-2 text-center">
          <h1 className="font-serif text-3xl tracking-tight">Illinois Pension Assistant</h1>
          <p className="text-sm text-muted-foreground">Sign in to your account</p>
        </div>

        <form onSubmit={handleLogin} className="space-y-5 rounded-xl border border-border bg-card p-6">
          {error && (
            <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </div>
          )}

          <div className="space-y-1.5">
            <label htmlFor="email" className="text-sm font-medium">Email address</label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={field}
              placeholder="you@example.com"
              required
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className="text-sm font-medium">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={field}
              required
            />
          </div>

          {/* A native button: this one must submit the form. */}
          <button
            type="submit"
            disabled={isLoading}
            className={buttonVariants({ size: "lg", className: "h-10 w-full" })}
          >
            {isLoading ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="text-center text-sm text-muted-foreground">
          No account?{" "}
          <Link href="/signup" className="text-primary hover:underline">Create one</Link>
          {" · "}
          <Link href="/" className="text-primary hover:underline">Continue without an account</Link>
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Restyle `src/app/signup/page.tsx` by class mapping**

Change classes only — no state, handlers or copy (other than the heading's classes). Apply each row; the two `absolute … blur-[120px]` decorative `<div>`s are deleted outright.

| Current classes | Replace with |
|---|---|
| page `bg-neutral-950 text-neutral-50 relative overflow-hidden` | `bg-background text-foreground` (keep layout classes `flex items-center justify-center min-h-screen py-12`, add `px-4`) |
| card `bg-neutral-900/50 backdrop-blur-xl rounded-2xl shadow-2xl border border-neutral-800 relative z-10` | `bg-card rounded-xl border border-border` |
| heading `text-3xl font-bold tracking-tight bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent` | `font-serif text-3xl tracking-tight` |
| `text-neutral-50`, `text-neutral-100`, `text-neutral-200`, `text-neutral-300` | `text-foreground` |
| `text-neutral-400`, `text-neutral-500` | `text-muted-foreground` |
| `hover:text-neutral-200` | `hover:text-foreground` |
| `field` constant: `bg-neutral-950 border border-neutral-800 rounded-xl focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 outline-none transition-all text-neutral-100` | `bg-background border border-input rounded-lg focus:ring-2 focus:ring-ring/30 focus:border-ring outline-none text-foreground` |
| primary buttons `bg-white text-neutral-950 font-semibold rounded-xl hover:bg-neutral-200 transition-all` | `bg-primary text-primary-foreground font-medium rounded-lg hover:bg-primary/90` |
| spinner `border-neutral-950/20 border-t-neutral-950` | `border-primary-foreground/30 border-t-primary-foreground` |
| links `text-blue-400 hover:text-blue-300` | `text-primary hover:underline` |
| errors `text-red-400 bg-red-950/50 border border-red-900/50` | `text-destructive bg-destructive/10 border border-destructive/30` |
| `text-xs text-red-400` | `text-xs text-destructive` |
| synthetic notice `text-amber-200/80 bg-amber-950/30 border border-amber-900/40` | `text-foreground bg-muted border border-border` |
| `text-amber-300/80` | `text-muted-foreground` |
| panels `border-neutral-800 bg-neutral-950/40` | `border-border bg-muted/50` |
| any selected/hover persona-card state using `blue-*` (e.g. `border-blue-500 bg-blue-950/30`) | `border-primary bg-primary/5` |
| any other remaining palette class | by role: text → `text-foreground`/`text-muted-foreground`; surface → `bg-card`/`bg-muted`; accent → `primary`; error → `destructive` |

- [ ] **Step 3: Run the palette gate**

Run (from `frontend/`):
```bash
grep -rnE "(neutral|blue|purple|amber|red)-[0-9]|bg-white|text-white|bg-gradient|backdrop-blur|blur-\[" src/app src/components/chat --include=*.tsx
```
Expected: no output. Any hit is fixed with the mapping above before continuing.

- [ ] **Step 4: Build and lint**

Run: `npm run build` — Expected: passes.
Run: `npm run lint` — Expected: 1 problem (the pre-existing `window.location` warning in `src/lib/api.ts`).

- [ ] **Step 5: Commit**

```bash
git add src/app/login/page.tsx src/app/signup/page.tsx
git commit -m "Frontend: sign-in and sign-up in the same system as the chat" -m "<trailer lines>"
```

---

### Task 9: Verification in the running app

**Files:** none changed unless a check fails (a fix then gets its own commit).

- [ ] **Step 1: Automated checks**

Run: `npm test` (9 pass), `npm run lint` (≤ 1 problem), `npm run build` (passes).

- [ ] **Step 2: Start both servers**

From the repo root: `uv run uvicorn pension_chat.main:app --reload` (wait for "Warmed search connection", ~8s). From `frontend/`: `npm run dev`. Open `http://localhost:3000`.

- [ ] **Step 3: Walk the checklist, in light and in dark (OS setting), at desktop width and at 400px**

- Anonymous: "Hello", four prompts, "This is a temporary conversation."; sidebar shows "Sign up to keep your conversations" and no history list.
- Clicking a prompt sends it at once; the status line shows the stage labels capitalised with an ellipsis, then disappears when tokens arrive.
- The answer is full-width serif text; the question is a muted bubble on the right; tables use sans with aligned digits; citation chips appear after `done`.
- Enter sends; Shift+Enter adds a line; the composer grows to ~8 lines and then scrolls.
- Stop during an answer: generation stops, the partial text stays, "Stopped" shows beneath it.
- New chat: the view clears instantly, even mid-answer.
- Signed in (`pen_test_1@yopmail.com` or a new account): history grouped Today / Yesterday / Earlier; a new conversation's title appears after its first message; Sign out works.
- At 400px: the sidebar is hidden by default, opens over the chat with a backdrop, closes on backdrop tap and on choosing a conversation; the header shows New chat; nothing scrolls horizontally.
- Login and signup pages: no dark panels, gradients or glows remain.
- Browser tab reads "Illinois Pension Assistant"; body text is Geist (DevTools → Computed → font-family), answers are Source Serif 4.

- [ ] **Step 4: Record how the server handles Stop (spec §10 risk)**

After pressing Stop once, reload the conversation and note whether the assistant message was persisted in full, in part, or not at all, and whether the backend log shows generation continuing. Report the finding; do not change backend behaviour in this phase.

- [ ] **Step 5: Report**

Summarise the checklist results, the Stop finding, and screenshots if taken. If any check needed a fix, list the fix commits.
