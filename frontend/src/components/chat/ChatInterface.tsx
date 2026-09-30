"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  API_BASE_URL,
  anonymousSession,
  auth,
  credentialHeaders,
  fetchMe,
  fetchScenarios,
  fetchPeople,
  addPerson,
  removePerson,
  fetchRecordSource,
  fetchWithAuth,
  forgetScenario,
  setPrimaryProfile,
  type Me,
} from "@/lib/api";
import { ScenarioChips } from "./ScenarioChips";
import { NewProfilePanel } from "@/components/profile/NewProfilePanel";
import { ProfileMenu } from "@/components/profile/ProfileMenu";
import { ProfilePanel } from "@/components/profile/ProfilePanel";
import type { ProgressStep } from "@/lib/progress";
import { takeFrames, type SseEvent } from "@/lib/sse";
import type { Citation, Message, Panel, Scenario, SessionSummary } from "@/lib/types";
import { CalculationPanel } from "./CalculationPanel";
import { ChatHeader } from "./ChatHeader";
import { PeopleChips } from "./PeopleChips";
import type { Person } from "@/lib/subject";
import { ChatMessage } from "./ChatMessage";
import { Composer } from "./Composer";
import { Drawer } from "./Drawer";
import { EmptyState } from "./EmptyState";
import { EvidencePanel } from "./EvidencePanel";
import { Sidebar } from "./Sidebar";
import { WorkingNote } from "./WorkingNote";


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
  // What the turn is doing, and what it has finished, while the answer is
  // still being written. Both are cleared the moment prose starts arriving.
  const [steps, setSteps] = useState<ProgressStep[]>([]);
  const [views, setViews] = useState<{ slot: string; markdown: string }[]>([]);
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [signedIn, setSignedIn] = useState(false);
  // The side drawer: a section's text or a calculation, beside the chat.
  const [panel, setPanel] = useState<Panel | null>(null);
  // Who is signed in and their profiles (`GET /me`), and the profile this chat
  // uses -- null meaning the account's primary.
  const [me, setMe] = useState<Me | null>(null);
  const [activeProfileId, setActiveProfileId] = useState<string | null>(null);
  // The what-ifs this conversation is holding (spec §7).
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  // The conversation's employees (PBMS only): who it is about, and who this turn is about.
  const [recordSource, setRecordSource] = useState("postgres");
  const [people, setPeople] = useState<Person[]>([]);
  const [activePeople, setActivePeople] = useState<string[]>([]);
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

  const createNewSession = async (profileId?: string) => {
    // Clear the view before the network call, not after — the backend round
    // trip to Azure Postgres is real (see api/sessions.py::create_session),
    // and a member should see the empty conversation the instant they click
    // "New chat" rather than watch the old one sit there for it.
    abortRef.current?.abort();
    setPanel(null);
    const requestId = ++sessionRequestId.current;
    setMessages([]);
    setScenarios([]);
    setSessionId(null);
    setActiveProfileId(profileId ?? null);
    closeSidebarOnPhone();

    try {
      const res = await fetchWithAuth("/sessions", {
        method: "POST",
        // No body means the account's primary profile (or an anonymous chat).
        body: profileId ? JSON.stringify({ profile_id: profileId }) : undefined,
      });
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
          {
            id: data.session_id,
            title: null,
            started_at: now,
            last_active_at: now,
            profile_id: profileId ?? me?.primary_profile_id ?? null,
          },
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
        // Together: the greeting and the profile chip need `/me` as soon as
        // the list does, and the database is a round trip away either way.
        const [res, meNow] = await Promise.all([fetchWithAuth("/sessions"), fetchMe()]);
        if (meNow) setMe(meNow);
        if (res.ok) {
          const data: SessionSummary[] = await res.json();
          setSessions(data);
          if (data.length > 0) {
            setSessionId(data[0].id);
            setActiveProfileId(data[0].profile_id ?? null);
          } else createNewSession();
        }
      } catch (err) {
        console.error("Failed to load sessions", err);
      }
    };
    restore();
  }, []);

  useEffect(() => {
    fetchRecordSource().then(setRecordSource);
  }, []);

  // The conversation's employees follow the conversation.
  useEffect(() => {
    // Replaced wholesale by what the new conversation holds (empty for a new one).
    if (!sessionId || recordSource !== "pbms") return;
    let stale = false;
    fetchPeople(sessionId).then((held) => {
      if (stale) return;
      setPeople(held.people);
      setActivePeople(held.active ?? []);
    });
    return () => {
      stale = true;
    };
  }, [sessionId, recordSource]);

  // Load messages when the conversation changes.
  useEffect(() => {
    if (!sessionId || createdHere.current.has(sessionId)) return;
    let stale = false;
    const loadMessages = async () => {
      try {
        // Together: a reloaded chat's chips come from its checkpoint.
        const [res, held] = await Promise.all([
          fetchWithAuth(`/sessions/${sessionId}/messages`),
          fetchScenarios(sessionId),
        ]);
        if (stale) return;
        if (res.ok) setMessages(await res.json());
        setScenarios(held);
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
    setPanel(null);
    setScenarios([]);
    setPeople([]);
    setActivePeople([]);
    setActiveProfileId(sessions.find((s) => s.id === id)?.profile_id ?? null);
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
        case "progress":
          // Kept whatever the elapsed time says: `visibleSteps` decides what
          // the member sees, so a turn that turns out to be slow can still
          // show the steps that happened before anyone knew it would be.
          if (!sawAnswerToken) {
            setSteps((prior) => [
              ...prior,
              { text: String(data.text ?? ""), at: Number(data.at ?? 0) },
            ]);
          }
          break;
        case "view":
          // Rendered before generation began, so it is on screen while the
          // sentence explaining it is still being written.
          if (!sawAnswerToken) {
            setViews((prior) => [
              ...prior,
              { slot: String(data.slot ?? ""), markdown: String(data.markdown ?? "") },
            ]);
          }
          break;
        case "token":
          sawAnswerToken = true;
          setAgentStatus("");
          // The answer carries the tables itself from here.
          setSteps([]);
          setViews([]);
          aiContent += String(data.delta ?? "");
          update({ content: aiContent });
          break;
        case "replace":
          // Verification rejected what was streamed; swap it out entirely.
          aiContent = String(data.text ?? "");
          update({ content: aiContent });
          break;
        case "people":
          // Who this turn is about, decided before any answer text arrives.
          setActivePeople(Array.isArray(data.active) ? (data.active as string[]) : []);
          break;
        case "done":
          setAgentStatus("");
          if (Array.isArray(data.scenarios)) setScenarios(data.scenarios as Scenario[]);
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

  const removeScenario = async (label?: string) => {
    if (!sessionId) return;
    const remaining = await forgetScenario(sessionId, label);
    if (remaining) setScenarios(remaining);
  };

  const refreshMe = async () => {
    const next = await fetchMe();
    if (next) setMe(next);
  };

  const activeProfile =
    me?.profiles.find((p) => p.id === (activeProfileId ?? me.primary_profile_id)) ?? null;
  const labelById = new Map((me?.profiles ?? []).map((p) => [p.id, p.label]));
  const editing =
    panel?.kind === "profile-edit"
      ? (me?.profiles.find((p) => p.id === panel.profileId) ?? null)
      : null;

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
          // Wrapped: passed directly, the click event would arrive as a profile id.
          onNewSession={() => createNewSession()}
          signedIn={signedIn}
          profileLabel={
            labelById.size > 1
              ? (s) => (s.profile_id && labelById.get(s.profile_id)) || s.profile_label || null
              : undefined
          }
          footer={
            signedIn && me && activeProfile ? (
              <ProfileMenu
                me={me}
                active={activeProfile}
                onSwitch={(id) => createNewSession(id)}
                // A panel opens over the chat, which the drawer covers on a phone.
                onEdit={(id) => {
                  setPanel({ kind: "profile-edit", profileId: id });
                  closeSidebarOnPhone();
                }}
                onNew={() => {
                  setPanel({ kind: "profile-new" });
                  closeSidebarOnPhone();
                }}
                onLogout={() => {
                  auth.signOut();
                  router.push("/login");
                }}
              />
            ) : null
          }
        />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <ChatHeader
          onToggleSidebar={() => setSidebar((prev) => !(prev ?? isDesktop()))}
          onNewChat={() => createNewSession()}
          showNewChatOnDesktop={sidebar === false}
          people={
            recordSource === "pbms" ? (
              <PeopleChips
                people={people}
                active={activePeople}
                disabled={isStreaming}
                onRemove={async (ref) => {
                  if (!sessionId) return;
                  const held = await removePerson(sessionId, ref);
                  if (held) {
                    setPeople(held.people);
                    setActivePeople((active) => active.filter((r) => r !== ref));
                  }
                }}
              />
            ) : undefined
          }
        />

        <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <EmptyState
              signedIn={signedIn}
              onPick={send}
              disabled={!sessionId}
              name={me?.display_name}
              profile={signedIn ? activeProfile : null}
            />
          ) : (
            <div className="mx-auto w-full max-w-[720px] space-y-8 px-4 py-8">
              {messages.map((m) => (
                <ChatMessage
                  key={m.id}
                  message={m}
                  isStreaming={m.id === streamingMessageId}
                  onOpenSection={(section) => setPanel({ kind: "section", section })}
                  onOpenCalculation={(card) => setPanel({ kind: "calculation", card })}
                  onChoosePerson={async (person, suggestion) => {
                    if (!sessionId) return false;
                    // Someone already in the conversation (a which-one choice) is
                    // chosen by asking again about them by name; nothing to add.
                    if (suggestion.kind === "which_one") return true;
                    const held = await addPerson(sessionId, person.ref);
                    if (held) setPeople(held.people);
                    return Boolean(held);
                  }}
                  onResend={(question) => void send(question)}
                />
              ))}
              <WorkingNote status={agentStatus} steps={steps} views={views} />
            </div>
          )}
        </div>

        <div className="mx-auto w-full max-w-[720px] px-4 pb-4">
          <ScenarioChips scenarios={scenarios} onRemove={removeScenario} disabled={isStreaming} />
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

      {panel && (
        <Drawer
          title={
            panel.kind === "section"
              ? "Statute text"
              : panel.kind === "calculation"
                ? "Calculation"
                : panel.kind === "profile-edit"
                  ? "Edit profile"
                  : "New profile"
          }
          onClose={() => setPanel(null)}
        >
          {panel.kind === "section" ? (
            <EvidencePanel key={panel.section} section={panel.section} />
          ) : panel.kind === "calculation" ? (
            <CalculationPanel card={panel.card} />
          ) : panel.kind === "profile-edit" ? (
            editing && (
              <ProfilePanel
                key={editing.id}
                profile={editing}
                isPrimary={editing.id === me?.primary_profile_id}
                onSaved={async () => {
                  await refreshMe();
                  setPanel(null);
                }}
                onMakePrimary={async () => {
                  await setPrimaryProfile(editing.id);
                  await refreshMe();
                }}
              />
            )
          ) : (
            <NewProfilePanel
              onCreated={async (profile) => {
                await refreshMe();
                setPanel(null);
                createNewSession(profile.id);
              }}
            />
          )}
        </Drawer>
      )}
    </div>
  );
}
