import type { SupportLevel } from "@/lib/api";
import { EXPERIMENTAL_NOTE } from "@/lib/funds";

/** Marks a fund the assistant knows less well; a checked fund needs no badge. */
export function SupportBadge({ level }: { level: SupportLevel | null | undefined }) {
  if (level !== "experimental") return null;
  return (
    <span
      title={EXPERIMENTAL_NOTE}
      className="rounded border border-border px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground"
    >
      Experimental
    </span>
  );
}
