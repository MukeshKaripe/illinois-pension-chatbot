import Link from "next/link";
import { format } from "date-fns";
import { Plus, MessageSquare, LogOut, UserPlus } from "lucide-react";

interface SidebarProps {
  sessions: any[];
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
  return (
    <div className="flex flex-col h-full bg-neutral-950/80">
      <div className="p-4 border-b border-neutral-800">
        <button
          onClick={onNewSession}
          className="w-full flex items-center justify-center space-x-2 bg-blue-600 hover:bg-blue-700 text-white py-2.5 px-4 rounded-xl transition-all font-medium text-sm shadow-lg shadow-blue-900/20"
        >
          <Plus size={16} />
          <span>New Session</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-1 scroll-smooth">
        {/*
          An anonymous visitor has no cross-visit history to list, and this is a
          product decision rather than a gap: there is no identity to
          authenticate a list against beyond the token this one browser holds.
          The current conversation survives a reload; earlier ones are what an
          account is actually for, so say that instead of showing an empty list.
        */}
        {!signedIn ? (
          <div className="px-2 mt-2 text-sm text-neutral-400 space-y-3">
            <p className="font-medium text-neutral-300">Not signed in</p>
            <p className="text-neutral-500 leading-relaxed">
              You can ask anything about the Illinois Pension Code, and work out
              an estimate from figures you give in the conversation.
            </p>
            <p className="text-neutral-500 leading-relaxed">
              Signing in adds a member record, so figures come from it rather
              than from what you type, and keeps your past conversations.
            </p>
            <Link
              href="/signup"
              className="mt-1 flex items-center justify-center space-x-2 border border-neutral-700 hover:border-neutral-500 text-neutral-200 py-2 px-4 rounded-xl transition-colors text-sm font-medium"
            >
              <UserPlus size={16} />
              <span>Create an account</span>
            </Link>
            <Link
              href="/login"
              className="block text-center text-xs text-neutral-500 hover:text-neutral-300 transition-colors"
            >
              or sign in
            </Link>
          </div>
        ) : null}
        {signedIn ? (
          <div className="text-xs font-semibold text-neutral-500 uppercase tracking-wider mb-3 px-2 mt-2">
            Recent Chats
          </div>
        ) : null}
        {sessions.map((session) => (
          <button
            key={session.id}
            onClick={() => onSelectSession(session.id)}
            className={`w-full flex items-start text-left p-3 rounded-xl transition-all ${
              session.id === currentSessionId
                ? "bg-neutral-800/80 text-neutral-100 shadow-sm"
                : "text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200"
            }`}
          >
            <MessageSquare size={16} className="mt-0.5 shrink-0" />
            <div className="ml-3 overflow-hidden">
              <div className="text-sm font-medium truncate">
                {session.title || "New Conversation"}
              </div>
              <div className="text-xs text-neutral-500 mt-1">
                {format(new Date(session.last_active_at), "MMM d, yyyy h:mm a")}
              </div>
            </div>
          </button>
        ))}
      </div>

      {signedIn ? (
        <div className="p-4 border-t border-neutral-800">
          <button
            onClick={onLogout}
            className="w-full flex items-center space-x-3 text-neutral-400 hover:text-red-400 p-2 rounded-lg hover:bg-red-950/20 transition-colors"
          >
            <LogOut size={16} />
            <span className="text-sm font-medium">Log Out</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}
