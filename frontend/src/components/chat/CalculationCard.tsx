import { Calculator } from "lucide-react";
import { cardBadge, cardHeadline, formatMoney, sourceLabel, type CalculationCardData } from "@/lib/card";
import { EXPERIMENTAL_NOTE } from "@/lib/funds";

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
  const badge = cardBadge(card);
  const headline = cardHeadline(card);
  return (
    <div className="mb-4 rounded-lg border border-border bg-card p-4">
      {card.person && <div className="mb-1 text-sm font-semibold">{card.person}</div>}
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-xs font-medium text-primary">
          <Calculator className="size-4" />
          {card.formula_name || "Calculation"}
        </span>
        {badge && (
          <span
            title={card.support_level === "experimental" ? EXPERIMENTAL_NOTE : undefined}
            className="rounded border border-border px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground"
          >
            {badge}
          </span>
        )}
      </div>

      {headline.label && <div className="mt-3 text-xs text-muted-foreground">{headline.label}</div>}
      <div className={`${headline.label ? "mt-1" : "mt-3"} flex flex-wrap items-baseline gap-x-6 gap-y-1 tabular-nums`}>
        <div>
          <span className="text-2xl font-semibold">{formatMoney(headline.annual_amount)}</span>
          <span className="ml-1 text-sm text-muted-foreground">a year</span>
        </div>
        {headline.monthly_amount && (
          <div>
            <span className="text-lg font-medium">{formatMoney(headline.monthly_amount)}</span>
            <span className="ml-1 text-sm text-muted-foreground">a month</span>
          </div>
        )}
      </div>

      {headline.others.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-sm tabular-nums text-muted-foreground">
          {headline.others.map((other) => (
            <li key={other.label}>
              {other.label}: <span className="font-medium text-foreground">{formatMoney(other.annual_amount)}</span> a year
            </li>
          ))}
        </ul>
      )}

      {card.inputs.length > 0 && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
          {card.inputs.slice(0, CARD_INPUTS).map((input) => (
            <div key={input.name}>
              <dt className="text-xs text-muted-foreground">{input.label}</dt>
              <dd className="tabular-nums">
                {input.value}
                {input.source && (
                  <span className="ml-1 text-[11px] text-muted-foreground">
                    · {sourceLabel(input.source, card.record_label)}
                  </span>
                )}
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
