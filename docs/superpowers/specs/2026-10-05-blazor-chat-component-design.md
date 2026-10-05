# Blazor chat component: design

**Status:** draft, parked until the open questions in §9 are answered. Written 2026-10-05.
**Decision this records:** the chatbot front end moves from React (Next.js) to Blazor, as a component the client's own Blazor application hosts. The FastAPI backend is unchanged.

## 1. Why

- **The client's application is Blazor.** A component inside their solution is native to it: it needs no iframe, no second deployment, and no second sign-in.
- **Their .NET team can maintain it** after handover, in one stack.
- **Now is the cheapest time.** The front end is about 4,200 lines in 46 files (`frontend/src`, excluding tests). Every UI feature added in React makes the port larger.
- **The intelligence is in FastAPI.** Retrieval, the agent, the calculation engine and verification all stay where they are, so this is a UI port with the API contract fixed.

**Considered and not chosen:**
- **Keep React behind an iframe.** It's the fastest option, but it feels separate, sign-in needs a token handoff, and it adds a deployment to own.
- **Ship React as a web component.** It integrates better, but it's still a second technology inside a .NET product, and styles inside Shadow DOM need care.

Both stay available as a fallback if the spike in §7 fails.

## 2. Goals and non-goals

**Goals**
1. **Parity:** every behaviour in §4, verified by the parity checklist in §8.
2. **Same look:** keep the "Civic calm" design tokens (`frontend/src/app/globals.css`) and Tailwind, unless the client chooses their own style (§9, Q2).
3. **Streaming feels as fast as today.** Time to first answer word must not grow because of the front end (the latency spec, D3a).
4. **Their app hosts it in one line:** `<PensionChat ... />`, with configuration through parameters and DI.

**Non-goals**
- Any backend change beyond what §5.3 lists (embed-mode auth and CORS).
- New UI features during the port. The React app is frozen except for bug fixes until parity is reached.
- Our own login, signup, profile picker and personas. The host app owns identity (§5.3). They stay in the React app for our standalone demo.

## 3. Shape

```
Client's Blazor app (their solution)
 └─ references  PensionChat.Components   (Razor Class Library, NuGet or project reference)
      ├─ <PensionChat>            the whole chat: header, messages, composer, drawer
      ├─ PensionChatClient        typed HttpClient for the FastAPI endpoints
      ├─ SseReader                streams and parses Server-Sent Events
      ├─ Markdown renderer        Markdig, raw HTML disabled, plus streaming rules
      ├─ Lib/*                    C# ports of frontend/src/lib (pure logic)
      └─ wwwroot/                 compiled Tailwind CSS, icons, one small JS interop file
```

- **Package:** a Razor Class Library (`PensionChat.Components`), versioned, which the host references. The host registers it once:

  ```csharp
  builder.Services.AddPensionChat(o => {
      o.ApiBaseUrl = "https://<fastapi-host>";
      o.TokenProvider = sp => sp.GetRequiredService<IPensionChatTokenSource>(); // §5.3
  });
  ```

- **Use:** `<PensionChat SessionScope="@userId" StartingEmployee="@employeeRef" OnPersonSelected="..." />`.
- **Render mode:** the component works under InteractiveWebAssembly, InteractiveServer and InteractiveAuto. **Recommended: WebAssembly or Auto.** There the browser calls FastAPI directly, as today. Under Server, every streamed token goes FastAPI → host server → SignalR → browser. That works, but it adds a hop per token and holds a circuit per user. The host's mode is §9, Q1.
- **Target:** .NET 8 or later; matched to the host's version (§9, Q1).

## 4. What it must do (parity with today's React app)

Taken from `frontend/src/components/chat/*` and `lib/*`.

| Area | Behaviour | React source |
|---|---|---|
| Conversation | Send a message and stream the answer token by token; **Stop** aborts mid-answer and keeps what streamed; **New chat** starts a fresh session; restore the conversation on reload | `ChatInterface`, `Composer`, `lib/history.ts` |
| Streaming events | Handle `preamble`, `status`, `progress` (working note), `view` (code-rendered tables arriving before tokens), `token`, `replace` (verification replaced the answer), `people`, `done`, `error` | `ChatInterface`, `lib/sse.ts`, `lib/progress.ts` |
| Markdown while streaming | Render GitHub-flavoured markdown (tables, lists, bold) as it streams; hide half-written table headers (`dropOrphanTableHeaders`); split streaming text so partial blocks don't flicker (`splitStreaming`) | `ChatMessage`, `lib/streaming.ts` |
| Citations and evidence | Verified citations from `done`; clicking one opens the drawer with the statute text from `GET /sections/{id}`, in the serif statute typeface | `EvidencePanel`, `lib/sections.ts` |
| Calculation | One-line receipt under an answer; the drawer shows the figure, inputs with their origin, worked steps, formula, assumptions, what-if chips, Copy and Print | `CalculationCard`, `CalculationPanel`, `lib/card.ts` |
| Scenarios | Chips for the scenarios the member built; forget one or all (`GET/DELETE /sessions/{id}/scenarios`) | `ScenarioChips` |
| People | Header chips for the people in the conversation (up to 4); choosing between people with the same name; add, remove and clear (`/sessions/{id}/subjects`) | `PeopleChips`, `SubjectChoice`, `lib/subject.ts` |
| Starting points | Empty state with suggested questions | `EmptyState`, `lib/starters.ts` |
| Conversation list | Sidebar of the account's conversations, when signed in | `Sidebar` |
| Fund support level | "experimental" badge for funds not yet checked | `SupportBadge`, `lib/funds.ts` |

**Behaviours that are easy to lose and must be kept** (each is a test in §8):
- **Credentials only when present:** never send `Authorization: Bearer null`.
- **Browser storage:** every access is wrapped; a private window or blocked site data must not break the page.
- **Stop is a disconnect:** the backend keeps what streamed (CLAUDE.md, "Stop is a disconnect").
- **The calculation card is withheld when verification replaced the answer.** The front end shows what `done` carries and never derives a card itself.
- **The statute serif is used only for statute text.**

## 5. Technical design

### 5.1 Streaming (the highest risk)
- **WebAssembly:** `HttpRequestMessage.SetBrowserResponseStreamingEnabled(true)` plus `HttpCompletionOption.ResponseHeadersRead`, reading the body as a stream.
- **Server:** an `HttpClient` stream from the host server.
- **Parsing:** `SseReader` parses `event:` and `data:` frames incrementally, porting `lib/sse.ts` and its tests. Frames can split across reads.
- **Rendering:** UI updates are batched (at most every ~50 ms) so per-token re-rendering doesn't lag behind the stream.
- **Stop:** a `CancellationTokenSource` per answer.

### 5.2 Markdown
- **Markdig** with the advanced extensions (pipe tables, lists). **Raw HTML is disabled** (`DisableHtml()`), because the output is rendered through `MarkupString`, which renders whatever HTML it's given (an XSS risk).
- The streaming rules from `lib/streaming.ts` are ported and run before Markdig on each batch.
- Code-rendered views (the `view` event: WORKED_STEPS, COMPARISON, THRESHOLDS, PEOPLE) are markdown from the backend and go through the same renderer.

### 5.3 Identity: the host signs the user in, not us
- **The component never shows a login.** The host provides `IPensionChatTokenSource`, which returns a short-lived token for the signed-in user.
- **FastAPI accepts that token in embed mode.** The preferred form is a JWT from the client's identity provider, validated against its published keys. If that isn't possible, the token is signed by the host backend with a shared secret, kept for minutes and not hours.
- **Who the user is decides what they may see.** The existing RLS (`get_db_session`) and the anonymous-session path stay as they are; this adds a third way to establish the principal.
- **Backend changes, the only ones this spec asks for:**
  - validate the embed token;
  - map it to an account;
  - add the host's origin to CORS;
  - set CSP `frame-ancestors` for the iframe fallback.

### 5.4 Pure logic ported first
`frontend/src/lib` has ten modules with tests: `card`, `funds`, `history`, `progress`, `sections`, `sse`, `starters`, `streaming`, `subject`, plus `api`/`types`. Each becomes a C# class with its tests ported to xUnit **before** any screen is built, so the behaviour is proven identical where it can be tested without a browser.

### 5.5 Styling
- **Tailwind (standalone CLI)** scans `.razor` files and builds one CSS file shipped in the RCL's `wwwroot`. The `globals.css` tokens are copied as they are.
- **Components** (`components/ui/*`: button, badge, card, input, alert, avatar, separator, skeleton, scroll-area) become small Razor components with the same classes.
- **Drawer, menus and focus handling:**
  - re-implement on Blazor's own focus and keyboard events with a small JS interop for focus trapping;
  - or use a headless (unstyled) Blazor library.
  - **Not** MudBlazor or Fluent UI, unless the client chooses their app's style (§9, Q2).
- **Icons:** the Lucide SVGs in use, inlined as a Razor icon component.
- **Isolation inside the host app:** scope the CSS under a root class (`.pension-chat`) so ours doesn't leak into theirs or theirs into ours.

### 5.6 Storage
Session token and id go through `IJSRuntime` calls to `localStorage`, each wrapped in try/catch. Or, if the host prefers, through its own server-side session (§9, Q4).

## 6. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Server render mode | Every token takes an extra hop; latency grows | Recommend WebAssembly/Auto; measure in the spike |
| Markdown while streaming | Flicker, broken tables, or unsafe HTML | Port `streaming.ts` with its tests; disable HTML; spike first |
| WebAssembly first load | A few MB on first visit | Host-level prerendering, caching, trimming; the chat can load after the host page |
| Focus and accessibility regressions in the drawer and menus | Keyboard users stuck | Parity checklist includes keyboard paths |
| Two front ends during the port | Fixes made twice | Freeze React to bug fixes only; port in one sustained stretch |

## 7. Plan

1. **Spike (1–2 days), go/no-go:** in a throwaway Blazor app in the host's render mode, stream a real answer from FastAPI with Stop, and render it with Markdig plus the streaming rules, tables included.
   - **Go** if time to first visible word is within 100 ms of the React app on the same machine, and tables don't flicker.
   - **No-go** if not; fall back to the web-component option (§1).
2. **Port `lib/` to C#** with xUnit tests (§5.4).
3. **The conversation:** messages, composer, streaming, stop, new chat, restore.
4. **The drawer:** evidence, calculation workspace, scenarios.
5. **People and the sidebar.**
6. **Embed-mode auth** on FastAPI, and wiring into the host app.
7. **Parity pass** (§8), then retire the React chat for the client deployment. React stays for our own standalone demo until we decide otherwise.

**Estimate:** about 3–5 weeks for one developer who knows Blazor, after the spike. The estimate is revisited after the spike.

## 8. How we'll know it's done
- **Unit:** every `lib/*.test.ts` case has a passing xUnit counterpart.
- **Parity checklist:** each row of §4 and each "easy to lose" behaviour is demonstrated in the host app, using the demo script's questions (general questions, a named record, a described employee, a comparison, what-ifs, stop mid-answer, reload restore).
- **Latency:** time to first answer word measured in the host app equals the React app's on the same backend, within 100 ms.
- **Security:**
  - no raw HTML rendered from model output;
  - no token in URLs;
  - CORS limited to the host's origin.

## 9. Open questions for the client (answers set the design)
1. **Their .NET version and render mode** (Server, WebAssembly, Auto). This decides the streaming path and the latency risk.
2. **Visual style:** keep ours ("Civic calm" tokens), or match their app (and if so, which component library they use)?
3. **Identity:** which identity provider, and can we validate its tokens in FastAPI? Or will their backend sign a short-lived token for us?
4. **Session storage:** browser `localStorage`, or their server-side session?
5. **Who maintains the component after handover:** their team (C#-first conventions, their repo) or us (our repo, published as a package)?
6. **Placement:** a full page, a side panel, or both? This decides responsive layout and the default drawer behaviour.
