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
  /** The profile a conversation uses, shown as a second line when an account
   *  has more than one; absent otherwise. */
  profileLabel?: (session: SessionSummary) => string | null;
}

export function Sidebar({
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  onLogout,
  signedIn,
  profileLabel,
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
                  const profile = profileLabel?.(session);
                  return (
                    <li key={session.id}>
                      <button
                        type="button"
                        onClick={() => onSelectSession(session.id)}
                        aria-current={current ? "page" : undefined}
                        className={`relative w-full rounded-md px-2.5 py-1.5 text-left text-sm ${
                          current
                            ? "bg-sidebar-accent text-foreground before:absolute before:inset-y-1.5 before:left-0 before:w-0.5 before:rounded-full before:bg-brass"
                            : "text-foreground/80 hover:bg-sidebar-accent"
                        }`}
                      >
                        <span className="block truncate">{session.title || "New conversation"}</span>
                        {profile && (
                          <span className="block truncate text-xs text-muted-foreground">{profile}</span>
                        )}
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
