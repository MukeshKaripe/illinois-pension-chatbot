# Pension Code Assistant: API spec for the Blazor front end

**For:** the client's front-end team, building the chat UI in Blazor.
**Version:** v1 (production release 2026-10-07). Written 2026-10-06.
**Backend:** FastAPI service `pension-chat`, deployed on the client's Kubernetes cluster and reachable on the internal network only.

This document is the contract between the chat UI and the backend: every endpoint, every streamed event, and how to render what comes back. Our reference implementation is the React app in `frontend/`. Where this document says "the reference UI does X", that's the behaviour to match.

---

## 1. Overview

```
Blazor UI ──HTTP/JSON──> POST /api/v1/sessions                 start a conversation
          ──HTTP + SSE─> POST /api/v1/sessions/{id}/messages   ask; the answer streams back
          ──HTTP/JSON──> GET  /api/v1/sections/{id}            statute text for a citation
          ──HTTP/JSON──> /api/v1/sessions/{id}/subjects        people in the conversation
          ──HTTP/JSON──> /api/v1/sessions/{id}/scenarios       what-ifs the conversation holds
          ──HTTP/JSON──> GET  /api/v1/sessions/{id}/messages   reload a conversation
```

- **Base URL:** `https://<backend-host>/api/v1`. Make it a configuration value.
- **Users** are fund staff (employers/HR) asking about the Pension Code and about named employees. Employee records are read live from PBMS.
- **One conversation is one session.** v1 keeps no cross-visit history.

## 2. Sessions and identity (v1: internal network, no sign-in)

v1 runs on the internal network only and uses **anonymous sessions**. The backend issues a secret token per conversation, and every later call about that conversation must carry it.

### `POST /api/v1/sessions`
Starts a conversation. No body, no auth header.

```json
200 OK
{ "session_id": "6f1c…-uuid", "session_token": "kq3…opaque" }
```

- Keep both values for as long as the conversation is open.
- Send the token on **every** later request about this session as the header `X-Session-Token: <session_token>`.
- **Never send an `Authorization` header in v1.** Sending `Authorization: Bearer ` with an empty or `null` value is treated as an invalid token and returns 401.
- **"New chat"** means calling `POST /sessions` again and dropping the old id and token.
- **Reload restore:** if you store the id and token (for example in browser `localStorage` or in the circuit's state), reopening the page can restore the conversation with `GET /sessions/{id}/messages` (§7). If browser storage is used, wrap every access in try/catch: a private window or blocked site data throws instead of returning null.

**Errors on any session endpoint:**
- `404`: no such session.
- `403`: wrong or missing `X-Session-Token`.

## 3. Asking a question (the streaming endpoint)

### `POST /api/v1/sessions/{session_id}/messages`

```http
POST /api/v1/sessions/{id}/messages
Content-Type: application/json
Accept: text/event-stream
X-Session-Token: <token>

{ "content": "What would TH_FN O'_LN get if they retired at the end of this year?" }
```

The response is **Server-Sent Events** (`text/event-stream`). It stays open until the `done` event, then closes. Each event is:

```
event: <name>
data: <one line of JSON>
<blank line>
```

**Reading it in Blazor:**
- **WebAssembly:** call `request.SetBrowserResponseStreamingEnabled(true)` and use `HttpCompletionOption.ResponseHeadersRead`, then read the body stream line by line.
- **Server:** use `HttpClient` with `ResponseHeadersRead` from the server.
- **Either way:** a frame can be split across reads, so buffer until the blank line that ends a frame. Our parser is `frontend/src/lib/sse.ts` (`takeFrames`, `parseFrame`), about 30 lines; port it.
- **Batch UI updates** (render at most every ~50 ms) rather than re-rendering on every token.

### 3.1 Events, in the order they arrive

| Event | `data` | When | What the UI does |
|---|---|---|---|
| `preamble` | `{"text": "Looking up the relevant sections…"}` | Immediately, before any work | Show as a status line under the question |
| `status` | `{"stage": "retrieving"}` | As stages change | Replace the status line, only until the first `token` |
| `progress` | `{"text": "Reading Section 8-138", "at": 2.4}` | Each step of the work; `at` = seconds since the question | Collect them. Show the list **only if** any step has `at >= 4` (a fast turn needs no narration). Clear on the first `token`. |
| `people` | `{"active": ["<ref>", …]}` | Before any answer text, when the turn is about named employees | Highlight those people's chips (§5) |
| `view` | `{"slot": "WORKED_STEPS", "markdown": "| Step | Amount |…"}` | Before the answer text, when a table is ready | Show as a preview while waiting. **Clear all previews on the first `token`**: the same tables arrive inside the answer text, so showing both duplicates them. |
| `token` | `{"delta": "Your estimated"}` | The answer, piece by piece | Append `delta` to the answer; render as markdown (§4) |
| `replace` | `{"text": "…", "reason": {…}}` | After streaming, if verification rejected the answer | **Replace the whole answer** with `text`. The streamed text was not verified. |
| `error` | `{"message": "…"}` | Something failed | Show the message if no answer text arrived; otherwise keep the text |
| `done` | see §3.2 | Last | Attach citations, cards and scenarios to the message; stop the spinner |

Slots in `view`: `WORKED_STEPS`, `COMPARISON`, `THRESHOLDS`, `PEOPLE`, `ASSUMPTIONS`.

### 3.2 The `done` event

```json
{
  "message_id": 1287,
  "citations": [{ "id": "8-138", "section": "8-138", "title": "Minimum annuities…", "citation": "40 ILCS 5/8-138" }],
  "verified_citations": ["8-138", "1-160"],
  "guardrail_passed": true,
  "scenarios": [{ "label": "at age 65", "annual_amount": "31267.16", "monthly_amount": "2605.60" }],
  "tool_calls": [ …audit trail; see below… ]
}
```

- `citations`: every section retrieved for the turn, with titles.
- `verified_citations`: the sections the answer **actually relied on and that passed verification**. Show citation chips for these only.
- `guardrail_passed`: `false` means the answer was replaced by a safe fallback. Show it, but there will be no calculation card.
- `scenarios`: the what-ifs the conversation now holds (§6). Replace your list with this one.
- `tool_calls`: an array of objects, each with a `type`. The UI reads three types and ignores the rest (the others are provenance for audit):

| `type` | Meaning | UI |
|---|---|---|
| `card` | A calculation result (§3.3). One per person when the turn compared several people. | A one-line receipt under the answer that opens the calculation panel |
| `subject_suggestion` | The question named someone the system must ask about (§5.2) | A choice card under the answer |
| `stopped` | The user pressed Stop on this message | Mark the message "stopped" |

### 3.3 The calculation card (`type: "card"`)

```json
{
  "type": "card",
  "formula_name": "Retirement annuity",
  "section": "8-138", "subsection": "g-1",
  "estimate": true,
  "annual_amount": "19770.89", "monthly_amount": "1647.57",
  "not_payable_now": null,
  "person": "RO_BK TA_NE",
  "support_level": "checked",
  "record_label": "PBMS record",
  "inputs": [
    { "name": "final_average_salary", "label": "Final average salary", "value": "70610.33", "source": "record" },
    { "name": "service_years", "label": "Years of service", "value": "14", "source": "record" }
  ],
  "scenarios": [{ "label": "at age 67", "annual_amount": "28244.13", "monthly_amount": "2353.68" }],
  "blocks": {
    "WORKED_STEPS": "**How that is worked out**\n\n| Step | Amount |\n|---|---:|\n…",
    "COMPARISON": "…",
    "ASSUMPTIONS": "**What I assumed**\n\n- the salary you gave, $80,000, taken as flat…"
  }
}
```

- **Amounts are strings of decimal numbers** (`"19770.89"`). Format them as currency in the UI; don't parse them to `double` for display.
- **`annual_amount` is `null` when retiring today pays nothing** (e.g. Tier 2 before 62). Then `not_payable_now` says why, and the figure is in `scenarios`.
- **`estimate: true` means show an "Estimate" badge.** `support_level: "experimental"` adds "experimental for this fund".
- **`inputs[].source`** is one of `record`, `stated`, `assumed`, `derived`, `statutory`, or `null`. Show it beside each value ("from the record", "you said", "assumed"…).
- **`blocks`** are markdown, rendered in the calculation panel exactly as given.
- **The card is absent when verification replaced the answer.** Never build a card yourself from answer text.

**The reference UI:**
- **Receipt line:** "Estimate · $19,770.89 a year · §8-138". Clicking it opens a side panel with the figure, inputs and their sources, worked steps, assumptions, the scenarios, and Copy/Print.
- **What-if chips** in the panel send a follow-up question.
- **Logic** is in `frontend/src/lib/card.ts`.

## 4. Rendering the answer text

The answer (`token` deltas, or `replace` text) is **GitHub-flavoured markdown**: paragraphs, **bold**, lists, and pipe tables.

1. **Use a markdown renderer with raw HTML disabled.** For example Markdig with `UseAdvancedExtensions()` and `DisableHtml()`. The text comes from a language model, so it must never be rendered as raw HTML; `MarkupString` renders whatever it's given.
2. **Render while streaming,** re-rendering the accumulated text. One exception: a table that is still being written parses as literal pipes until its rows close. So if the text after the last blank line starts with `|`, show that part as plain text until streaming ends. Port `splitStreaming` from `frontend/src/lib/streaming.ts`.
3. **Drop orphan table headers.** The model sometimes writes a table header just before a code-inserted table, and markdown merges the two. Remove a header row plus separator that is immediately followed by another header row plus separator. Port `dropOrphanTableHeaders` (same file).
4. **Section links.** References like "Section 8-138" or "§1-160" whose section is in `verified_citations` become clickable and open the statute panel (§8). Don't link sections that aren't verified. Port `linkSections` from `frontend/src/lib/sections.ts`.
5. **Statute text uses a serif typeface; everything else sans-serif.** The typeface tells the reader when the Code itself is speaking.

## 5. People (named employees)

Users ask about employees by name ("What would Jane Smith get at 62?"). Names are matched against PBMS. The conversation holds up to **4 people**.

**Person object** (`ref` is an opaque key: send it back, never display it):
```json
{ "ref": "…", "display_name": "RO_BK TA_NE", "first_name": "RO_BK", "last_name": "TA_NE",
  "department": "Streets", "employer": "City", "birth_year": 1964 }
```

### 5.1 Endpoints (all need `X-Session-Token`)

| Call | Returns |
|---|---|
| `GET /sessions/{id}/subjects` | `{"people": [Person…], "active": ["ref"…]}`: the conversation's people, and who the last turn was about |
| `POST /sessions/{id}/subjects` with body `{"ref": "…"}` | `{"people": [...]}`. Errors: `404` no such employee, `503` PBMS not answering. |
| `DELETE /sessions/{id}/subjects/{ref}` | `{"people": [...]}` |
| `DELETE /sessions/{id}/subjects` | `{"people": []}` |

The reference UI shows people as header chips ("Name · Department"), highlights the `active` ones from the `people` event, and lets the user remove one.

### 5.2 Choosing someone (`subject_suggestion` in `done.tool_calls`)

When a name matches several employees, or none, the answer asks, and `done.tool_calls` carries:

```json
{ "type": "subject_suggestion", "kind": "choose",
  "question": "What would Jane Smith get at 62?",
  "choices": [ { "first_name": "Jane", "last_name": "Smith", "unavailable": false,
                 "candidates": [Person, Person, …] } ] }
```

- **`kind: "choose"`:** show each choice's candidates (department, employer, birth year; never the ref). Above 12 candidates, group them by department first.
  - When the user picks one, call `POST /subjects` with its `ref`.
  - Once **every** choice with candidates has a pick, **re-send `question`** as a new message.
- **`kind: "which_one"`:** the person is already in the conversation, and the question was ambiguous ("what about her?"). When the user picks someone, send `"About <display_name>: <question>"` as a new message, with no `POST`.
- **`unavailable: true`:** the name couldn't be looked up (PBMS was down). Show the choice without candidates.
- **Once answered,** a choice card can't be clicked again.
- **Logic** is in `frontend/src/lib/subject.ts`.

## 6. Scenarios (what-ifs)

`done.scenarios` lists the what-ifs the conversation holds ("at age 65"…). The reference UI shows them as chips above the input box.

| Call | Returns |
|---|---|
| `GET /sessions/{id}/scenarios` | `[{"label", "annual_amount", "monthly_amount"}]` |
| `DELETE /sessions/{id}/scenarios?label=at%20age%2065` | the remaining list (omit `label` to forget all) |

## 7. Reloading a conversation

`GET /api/v1/sessions/{id}/messages` (with `X-Session-Token`) returns the conversation in order:

```json
[ { "id": 1286, "role": "user", "content": "…", "created_at": "2026-10-07T09:12:03+00:00" },
  { "id": 1287, "role": "assistant", "content": "…markdown…", "citations": [...],
    "verified_citations": ["8-138"], "tool_calls": [...], "guardrail_passed": true,
    "created_at": "…" } ]
```

Assistant messages carry the same `tool_calls` as `done`, so cards, choice cards and the "stopped" marker are read the same way live and on reload. The rendering rules in §4 apply here too.

## 8. Statute text for a citation

`GET /api/v1/sections/{section_id}` (public, no token), e.g. `/sections/8-138`:

```json
{ "section": "8-138", "title": "Minimum annuities - Additional provisions",
  "article": "8", "citation": "40 ILCS 5/8-138", "text": "…full statute text…" }
```

- Section ids look like `8-138`, `1-160`, `8-138.1`, `1-113.4a`. Anything else returns `422`.
- **`text`** is plain text with the statute's own lettering ((a), (b)…). Render it in the serif typeface, preserving line breaks.

## 9. Stop

**Stop = cancel the HTTP request.** In .NET, cancel the `CancellationToken` passed to the request, or dispose the response stream. The backend detects the disconnect and **keeps what was already streamed**, marked `{"type": "stopped"}` in that message's `tool_calls` on reload.

- In the UI, keep the partial answer, mark it "stopped", and re-enable the input.
- **New chat** and switching conversations must also cancel an answer that is still streaming.

## 10. Health and configuration

`GET /api/v1/health` returns `{"status": "healthy", "service": "pension-chat", "version": "…", "record_source": "pbms"}`.
- Use it for readiness.
- Show the people chips only when `record_source` is `"pbms"`.

**What we need from you for deployment:**
1. **The origin(s) the UI is served from,** e.g. `https://pensions.client.internal`. The backend allows browser calls only from listed origins (CORS). With Blazor WebAssembly, or any browser-side call, the browser's origin must be on the list. With Blazor Server calling from the server, CORS doesn't apply.
2. **Whether the UI calls from the browser (WebAssembly) or the server (Blazor Server).** WebAssembly streams fastest. In Server mode each token is relayed through your server and the SignalR circuit, which adds latency.

## 11. Errors and timeouts

| Situation | What you get | Do |
|---|---|---|
| Session missing or token wrong | `404` / `403` before the stream starts | Start a new session |
| Backend error mid-answer | `error` event, then the stream ends | Keep any text received; show the message if there was none |
| Verification rejects the answer | `replace` event | Replace the displayed answer |
| PBMS (employee records) down | The answer says the records couldn't be read; subject calls return `503` | Show the answer; disable adding people |
| Slow answer | `progress` events keep arriving | Show progress after 4 s (§3.1). Don't time out the request under 120 s: a first-time calculation can take 30–60 s. |

## 12. A complete example

**1. Start a conversation:**
```
POST /api/v1/sessions
→ {"session_id":"9b2e…","session_token":"tKx…"}
```

**2. Ask** (with `X-Session-Token: tKx…`): `{"content":"An employee who joined in 2015 is 62, earns $80,000 and has 11 years of service. What is their annuity if they retire now?"}`

**3. The stream:**
```
event: preamble
data: {"text": "Let me check the applicable pension rules and run the calculation for you..."}

event: progress
data: {"text": "Searching the Code", "at": 0.4}

event: view
data: {"slot": "WORKED_STEPS", "markdown": "**How that is worked out**\n\n| Step | Amount |\n|---|---:|\n| Final average salary | $80,000.00 |\n…"}

event: token
data: {"delta": "Section 8-138 supports an estimated annuity of **$15,417.60 a year"}

event: token
data: {"delta": " ($1,284.80 a month)**. …"}

event: done
data: {"message_id": 1290, "verified_citations": ["8-138", "1-160"], "guardrail_passed": true,
       "citations": [...], "scenarios": [], "tool_calls": [{"type": "card", "estimate": true,
       "annual_amount": "15417.60", "monthly_amount": "1284.80", "section": "8-138", ...}]}
```

**4. What the UI shows:**
- the answer as markdown, with the worked-steps table inline;
- citation chips for §8-138 and §1-160;
- the receipt "Estimate · $15,417.60 a year · §8-138", which opens the calculation panel.

## 13. Acceptance checklist for the Blazor UI

- [ ] A session is created, and `X-Session-Token` is sent on every session call; no `Authorization` header.
- [ ] Answers stream token by token, and the first words appear as they arrive (no buffering of the whole response).
- [ ] Markdown renders with raw HTML disabled; tables render; a half-written table shows as plain text until complete.
- [ ] `view` previews show while waiting and disappear on the first token (no duplicated tables).
- [ ] Progress steps show only on turns lasting more than 4 s.
- [ ] `replace` swaps the whole answer.
- [ ] Citation chips come from `verified_citations` only; clicking one opens the statute text in a serif typeface.
- [ ] A calculation card renders from `tool_calls` (badge, amounts formatted from strings, inputs with sources, blocks); it's absent when `guardrail_passed` is false.
- [ ] Several people produce one card each.
- [ ] A `choose` suggestion adds the picked person and re-sends the question once all picks are made; `which_one` re-sends "About <name>: …".
- [ ] People chips update from the `people` event and the subjects endpoints.
- [ ] Scenario chips show from `done.scenarios` and can be removed.
- [ ] Stop cancels the request and keeps the partial answer; New chat cancels any answer in flight.
- [ ] Reload restores the conversation from `GET /messages`, with cards and choice cards.
- [ ] The UI works when browser storage is blocked.

Questions about this contract go to the backend team. The React reference app (`frontend/`) shows each behaviour working.
