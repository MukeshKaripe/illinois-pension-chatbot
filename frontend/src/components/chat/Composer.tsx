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
