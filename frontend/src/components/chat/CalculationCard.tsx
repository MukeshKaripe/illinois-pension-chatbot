import { Calculator, ChevronRight } from "lucide-react";
import { cardBadge, receiptText, type CalculationCardData } from "@/lib/card";
import { EXPERIMENTAL_NOTE } from "@/lib/funds";

interface CalculationCardProps {
  card: CalculationCardData;
  onOpen: () => void;
  /** Kept for callers; the section opens from the drawer now. */
  onOpenSection?: (section: string) => void;
}

/**
 * The calculation, in one line under the answer.
 *
 * It was a block repeating the answer's figure and four inputs, between the
 * question and the reply. Every number here still comes from the calculation
 * engine through `tool_calls`, not from the prose, and the server still
 * withholds it when verification replaced the answer. The inputs, their
 * sources, the worked steps and the what-ifs are in the drawer it opens.
 */
export function CalculationCard({ card, onOpen }: CalculationCardProps) {
  const badge = cardBadge(card);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="mb-3 flex w-full items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-left text-sm hover:bg-muted"
    >
      <Calculator className="size-4 shrink-0 text-primary" />
      <span className="min-w-0 flex-1 truncate tabular-nums">
        {card.person && <span className="font-semibold">{card.person} · </span>}
        {receiptText(card)}
      </span>
      {badge && (
        <span
          title={card.support_level === "experimental" ? EXPERIMENTAL_NOTE : undefined}
          className="hidden shrink-0 rounded border border-border px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground sm:inline"
        >
          {badge}
        </span>
      )}
      <span className="flex shrink-0 items-center text-xs font-medium text-primary">
        Details
        <ChevronRight className="size-3.5" />
      </span>
    </button>
  );
}
