import { Calculator } from "lucide-react";
import { formatMoney, sourceLabel, type CalculationCardData } from "@/lib/card";

interface CalculationCardProps {
  card: CalculationCardData;
  onOpen: () => void;
  onOpenSection: (section: string) => void;
}

/** Inputs shown on the card itself; the drawer lists them all. */
const CARD_INPUTS = 4;

/**
 * The headline figure, above the answer that explains it.
 *
 * Every number here came from the calculation engine through `tool_calls`, not
 * from the answer's prose — the server withholds the card entirely when
 * verification replaced the answer.
 */
export function CalculationCard({ card, onOpen, onOpenSection }: CalculationCardProps) {
  const section = card.section;
  return (
    <div className="mb-4 rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-xs font-medium text-primary">
          <Calculator className="size-4" />
          {card.formula_name || "Calculation"}
        </span>
        {card.estimate && (
          <span className="rounded border border-border px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">
            Estimate
          </span>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-1 tabular-nums">
        <div>
          <span className="text-2xl font-semibold">{formatMoney(card.annual_amount)}</span>
          <span className="ml-1 text-sm text-muted-foreground">a year</span>
        </div>
        {card.monthly_amount && (
          <div>
            <span className="text-lg font-medium">{formatMoney(card.monthly_amount)}</span>
            <span className="ml-1 text-sm text-muted-foreground">a month</span>
          </div>
        )}
      </div>

      {card.inputs.length > 0 && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
          {card.inputs.slice(0, CARD_INPUTS).map((input) => (
            <div key={input.name}>
              <dt className="text-xs text-muted-foreground">{input.label}</dt>
              <dd className="tabular-nums">
                {input.value}{" "}
                <span className="text-[11px] text-muted-foreground">· {sourceLabel(input.source)}</span>
              </dd>
            </div>
          ))}
        </dl>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {section && (
          <button
            type="button"
            onClick={() => onOpenSection(section)}
            className="rounded-md border border-border px-2.5 py-1 text-xs font-medium tabular-nums text-primary hover:bg-muted"
          >
            § {section}
            {card.subsection ?? ""}
          </button>
        )}
        <button
          type="button"
          onClick={onOpen}
          className="rounded-md px-2.5 py-1 text-xs font-medium text-primary hover:bg-muted"
        >
          View calculation →
        </button>
      </div>
    </div>
  );
}
