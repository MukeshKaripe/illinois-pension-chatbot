"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  API_BASE_URL,
  anonymousSession,
  auth,
  credentialHeaders,
  fetchWithAuth,
} from "@/lib/api";
import { ChatMessage } from "./ChatMessage";
import { Sidebar } from "./Sidebar";
import { Send, Menu } from "lucide-react";

export function ChatInterface() {
  const [messages, setMessages] = useState<any[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<any[]>([]);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isTyping, setIsTyping] = useState(false);
  const [agentStatus, setAgentStatus] = useState("");
  const [signedIn, setSignedIn] = useState(false);
  const router = useRouter();

  const createNewSession = async () => {
    try {
      const res = await fetchWithAuth("/sessions", { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        // The backend returns a session_token only for an anonymous session,
        // and it is the only thing that will ever reach this conversation
        // again — row-level security scopes it by nothing else.
        if (data.session_token) {
          anonymousSession.remember(data.session_id, data.session_token);
        }
        const newSession = {
          id: data.session_id,
          title: "New Conversation",
          started_at: new Date().toISOString(),
          last_active_at: new Date().toISOString()
        };
        setSessions([newSession, ...sessions]);
        setSessionId(newSession.id);
        setMessages([]);
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
          const data = await res.json();
          setSessions(data);
          if (data.length > 0) {
            setSessionId(data[0].id);
          } else {
            createNewSession();
          }
        }
      } catch (err) {
        console.error("Failed to load sessions", err);
      }
    };
    restore();
  }, []);

  // Load messages when session changes
  useEffect(() => {
    if (!sessionId) return;
    const loadMessages = async () => {
      try {
        const res = await fetchWithAuth(`/sessions/${sessionId}/messages`);
        if (res.ok) {
          const data = await res.json();
          setMessages(data);
        }
      } catch (err) {
        console.error("Failed to load messages", err);
      }
    };
    loadMessages();
  }, [sessionId]);

  const sendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !sessionId) return;

    const userMessage = {
      id: Date.now().toString(),
      role: "user",
      content: input,
      created_at: new Date().toISOString(),
    };

    setMessages([...messages, userMessage]);
    setInput("");
    setIsTyping(true);
    setAgentStatus("Thinking...");

    // AI Placeholder
    const aiMessageId = (Date.now() + 1).toString();
    setMessages((prev) => [
      ...prev,
      {
        id: aiMessageId,
        role: "ai",
        content: "",
        created_at: new Date().toISOString(),
        citations: [],
        tool_calls: [],
      },
    ]);

    try {
      // Not fetchWithAuth: this needs the raw stream body. The credential
      // headers still have to come from the same place, because a tokenless
      // browser here used to send the literal string "Bearer null" — which is
      // a token as far as the header is concerned, fails to decode, and 401s.
      const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...credentialHeaders(),
        },
        body: JSON.stringify({ content: userMessage.content }),
      });

      if (!response.body) throw new Error("No response body");

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let aiContent = "";
      let buffer = "";
      let sawAnswerToken = false;

      // SSE frames are separated by a blank line and can be split across reads.
      // Parsing each read() chunk on its own — as this did before — drops any
      // event whose frame straddles a chunk boundary, which for a token stream
      // means losing words out of the middle of an answer.
      const applyFrame = (frame: string) => {
        let eventType = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) eventType = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (!dataLines.length) return;

        let data: Record<string, unknown>;
        try {
          data = JSON.parse(dataLines.join("\n"));
        } catch {
          return;
        }

        const setContent = (content: string) =>
          setMessages((prev) =>
            prev.map((m) => (m.id === aiMessageId ? { ...m, content } : m))
          );

        switch (eventType) {
          case "preamble":
            // Shown immediately, before any model call, so the member sees a
            // response within milliseconds rather than staring at a spinner
            // for the several seconds retrieval and generation take.
            setAgentStatus(String(data.text ?? ""));
            break;

          case "status":
            if (!sawAnswerToken) setAgentStatus(String(data.stage ?? ""));
            break;

          case "token":
            sawAnswerToken = true;
            setAgentStatus("");
            aiContent += String(data.delta ?? "");
            setContent(aiContent);
            break;

          case "replace":
            // Verification rejected what was streamed; swap it out entirely.
            aiContent = String(data.text ?? "");
            setContent(aiContent);
            break;

          case "done":
            setAgentStatus("");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === aiMessageId
                  ? {
                      ...m,
                      id: data.message_id ? String(data.message_id) : m.id,
                      citations: (data.citations as unknown[]) ?? [],
                      tool_calls: (data.tool_calls as unknown[]) ?? [],
                      guardrail_passed: data.guardrail_passed !== false,
                    }
                  : m
              )
            );
            break;

          case "error":
            setAgentStatus("");
            aiContent = aiContent || String(data.message ?? "Something went wrong.");
            setContent(aiContent);
            break;
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        let split = buffer.indexOf("\n\n");
        while (split !== -1) {
          applyFrame(buffer.slice(0, split));
          buffer = buffer.slice(split + 2);
          split = buffer.indexOf("\n\n");
        }
      }

      buffer += decoder.decode();
      if (buffer.trim()) applyFrame(buffer);

    } catch (err) {
      console.error("Chat error:", err);
    } finally {
      setIsTyping(false);
    }
  };

  return (
    <div className="flex h-screen bg-neutral-950 text-neutral-50 overflow-hidden">
      {/* Sidebar */}
      <div className={`transition-all duration-300 ${isSidebarOpen ? "w-80" : "w-0"} border-r border-neutral-800 bg-neutral-900/50 backdrop-blur-md overflow-hidden flex flex-col`}>
        <Sidebar
          sessions={sessions}
          currentSessionId={sessionId}
          onSelectSession={setSessionId}
          onNewSession={createNewSession}
          signedIn={signedIn}
          onLogout={() => {
            auth.signOut();
            router.push("/login");
          }}
        />
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col relative h-full">
        {/* Header */}
        <header className="h-16 border-b border-neutral-800 flex items-center px-4 bg-neutral-900/50 backdrop-blur-md shrink-0 z-10">
          <button
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            className="p-2 rounded-md hover:bg-neutral-800 text-neutral-400 hover:text-neutral-50 transition-colors"
          >
            <Menu size={20} />
          </button>
          <h1 className="ml-4 font-semibold text-neutral-200">Pension Assistant</h1>
        </header>

        {/* Decorative ambient glow */}
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[300px] bg-blue-600/10 rounded-full blur-[100px] pointer-events-none" />

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6 scroll-smooth z-10">
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-neutral-500 space-y-4">
              <div className="w-16 h-16 bg-neutral-900 rounded-2xl flex items-center justify-center border border-neutral-800 shadow-xl">
                ✨
              </div>
              <p className="text-lg">Ask a question about the Illinois Pension Code</p>
            </div>
          ) : (
            messages.map((m) => (
              <ChatMessage key={m.id} message={m} />
            ))
          )}
          {isTyping && (
            <div className="flex space-x-3 text-neutral-500 items-center p-4">
              <div className="flex space-x-1">
                <div className="w-1.5 h-1.5 bg-neutral-600 rounded-full animate-bounce" />
                <div className="w-1.5 h-1.5 bg-neutral-600 rounded-full animate-bounce delay-75" />
                <div className="w-1.5 h-1.5 bg-neutral-600 rounded-full animate-bounce delay-150" />
              </div>
              {agentStatus && <span className="text-xs font-medium animate-pulse">{agentStatus}</span>}
            </div>
          )}
        </div>

        {/* Input Area */}
        <div className="p-4 border-t border-neutral-800 bg-neutral-950/80 backdrop-blur-xl shrink-0 z-10">
          <form onSubmit={sendMessage} className="max-w-4xl mx-auto relative">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about calculations, eligibility, or rules..."
              className="w-full bg-neutral-900 border border-neutral-700 rounded-2xl py-4 pl-6 pr-16 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 text-neutral-100 shadow-lg placeholder:text-neutral-500 transition-all"
            />
            <button
              type="submit"
              disabled={!input.trim() || isTyping}
              className="absolute right-2 top-2 bottom-2 aspect-square bg-white text-neutral-950 rounded-xl hover:bg-neutral-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center transition-all shadow-md"
            >
              <Send size={18} className="ml-1" />
            </button>
          </form>
          <div className="text-center mt-3">
            <span className="text-xs text-neutral-500">
              This system provides estimates and summaries based on ILCS. For official calculations, contact the Fund.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
