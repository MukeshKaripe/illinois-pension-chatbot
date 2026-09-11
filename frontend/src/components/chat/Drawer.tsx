"use client";

import { useEffect } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";

interface DrawerProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

/**
 * The one side panel (spec §5): a section's text or a calculation, beside the
 * chat rather than instead of it. A right-hand panel on desktop; on a phone,
 * where there is no "beside", a bottom sheet over a backdrop.
 */
export function Drawer({ title, onClose, children }: DrawerProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <button
        type="button"
        aria-label="Close panel"
        onClick={onClose}
        className="fixed inset-0 z-40 bg-foreground/20 md:hidden"
      />
      <aside
        role="dialog"
        aria-label={title}
        className="fixed inset-x-0 bottom-0 z-50 flex max-h-[85dvh] flex-col rounded-t-xl border-t border-border bg-card shadow-lg md:static md:inset-auto md:z-auto md:h-full md:max-h-none md:w-[440px] md:shrink-0 md:rounded-none md:border-t-0 md:border-l md:shadow-none"
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
          <h2 className="text-sm font-semibold">{title}</h2>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X className="size-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </aside>
    </>
  );
}
