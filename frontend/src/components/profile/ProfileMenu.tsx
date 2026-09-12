"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronUp, LogOut, Pencil, Plus } from "lucide-react";
import type { Me, Profile } from "@/lib/api";
import { EXPERIMENTAL_NOTE } from "@/lib/funds";
import { SupportBadge } from "./SupportBadge";

interface ProfileMenuProps {
  me: Me;
  /** The profile this chat uses. */
  active: Profile;
  onSwitch: (profileId: string) => void;
  onEdit: (profileId: string) => void;
  onNew: () => void;
  onLogout: () => void;
}

const years = (profile: Profile) => Number(profile.total_service_years);

/**
 * Which member this chat is about — visible, but quiet (spec §6).
 *
 * Switching starts a new chat rather than changing this one: a conversation's
 * coverage decisions, assumptions and scenarios are bound to one record.
 *
 * Lives at the foot of the sidebar and opens upward, so identity sits where a
 * reader of any other assistant already looks for it. Sign-out is the last item
 * inside it rather than a button of its own: it is something you do to the
 * account, and the account is what this row names.
 */
export function ProfileMenu({ me, active, onSwitch, onEdit, onNew, onLogout }: ProfileMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const others = me.profiles.filter((profile) => profile.id !== active.id);
  const pick = (action: () => void) => () => {
    setOpen(false);
    action();
  };
  const item = "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted";

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-sidebar-accent"
      >
        <span
          aria-hidden
          className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground"
        >
          {active.label.trim().charAt(0).toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{active.label}</span>
          <span className="block truncate text-xs tabular-nums text-muted-foreground">
            Article {active.article_number}
          </span>
        </span>
        <ChevronUp
          className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute bottom-full left-0 z-50 mb-2 w-full rounded-lg border border-border bg-popover p-2 text-sm shadow-lg"
        >
          <div className="px-2 py-1.5">
            <p className="text-xs text-muted-foreground">This chat uses</p>
            <p className="flex flex-wrap items-center gap-2 font-medium">
              {active.label}
              <SupportBadge level={active.support_level} />
            </p>
            <p className="text-xs text-muted-foreground">
              {active.member_label} · Article {active.article_number} · age {active.age},{" "}
              {years(active)} years of service
            </p>
            {active.support_level === "experimental" && (
              <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{EXPERIMENTAL_NOTE}</p>
            )}
          </div>

          <button type="button" role="menuitem" onClick={pick(() => onEdit(active.id))} className={item}>
            <Pencil className="size-3.5" />
            Edit this profile
          </button>

          {others.length > 0 && (
            <>
              <div className="my-1 border-t border-border" />
              <p className="px-2 pt-1 text-xs text-muted-foreground">Start a chat as</p>
              {others.map((profile) => (
                <button
                  key={profile.id}
                  type="button"
                  role="menuitem"
                  onClick={pick(() => onSwitch(profile.id))}
                  className="flex w-full flex-col rounded-md px-2 py-1.5 text-left hover:bg-muted"
                >
                  <span className="font-medium">{profile.label}</span>
                  <span className="text-xs text-muted-foreground">
                    {profile.member_label} · Article {profile.article_number}
                    {profile.support_level === "experimental" && " · experimental"}
                  </span>
                </button>
              ))}
            </>
          )}

          <div className="my-1 border-t border-border" />
          <button type="button" role="menuitem" onClick={pick(onNew)} className={item}>
            <Plus className="size-3.5" />
            New profile
          </button>
          <p className="px-2 pt-1 text-[11px] leading-snug text-muted-foreground">
            Switching starts a new chat. This one stays in your history.
          </p>

          <div className="my-1 border-t border-border" />
          <button type="button" role="menuitem" onClick={pick(onLogout)} className={item}>
            <LogOut className="size-3.5" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
