"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Pencil, Plus } from "lucide-react";
import type { Me, Profile } from "@/lib/api";

interface ProfileMenuProps {
  me: Me;
  /** The profile this chat uses. */
  active: Profile;
  onSwitch: (profileId: string) => void;
  onEdit: (profileId: string) => void;
  onNew: () => void;
}

const years = (profile: Profile) => Number(profile.total_service_years);

/**
 * Which member this chat is about — visible, but quiet (spec §6).
 *
 * Switching starts a new chat rather than changing this one: a conversation's
 * coverage decisions, assumptions and scenarios are bound to one record.
 */
export function ProfileMenu({ me, active, onSwitch, onEdit, onNew }: ProfileMenuProps) {
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
        className="flex max-w-[16rem] items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs hover:bg-muted"
      >
        <span className="truncate font-medium">{active.label}</span>
        <span className="shrink-0 tabular-nums text-muted-foreground">· Art. {active.article_number}</span>
        <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-popover p-2 text-sm shadow-lg"
        >
          <div className="px-2 py-1.5">
            <p className="text-xs text-muted-foreground">This chat uses</p>
            <p className="font-medium">{active.label}</p>
            <p className="text-xs text-muted-foreground">
              {active.fund} · Article {active.article_number} · age {active.age}, {years(active)} years
              of service
            </p>
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
                    {profile.fund} · Article {profile.article_number}
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
        </div>
      )}
    </div>
  );
}
