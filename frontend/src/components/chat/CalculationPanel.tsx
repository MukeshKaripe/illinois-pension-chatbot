import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatMoney, sourceLabel, type CalculationCardData } from "@/lib/card";

/** Headings for the Python-rendered blocks the drawer carries (agent/card.py). */
const BLOCK_TITLES: Record<string, string> = {
  WORKED_STEPS: "How it was worked out",
  COMPARISON: "Comparison",
  ASSUMPTIONS: "Assumptions",
};

/**
 * The whole calculation: every input with its source, the scenarios, and the
 * worked steps and assumptions exactly as the server rendered them for the
 * answer — carried, not re-derived, so the drawer cannot disagree with it.
 */
export function CalculationPanel({ card }: { card: CalculationCardData }) {
  return (
    <div className="space-y-6">
      <section>
        <p className="text-xs text-muted-foreground">
          {card.estimate ? "Estimate" : "Calculated"}
          {card.section ? ` · 40 ILCS 5/${card.section}${card.subsection ?? ""}` : ""}
        </p>
        <p className="mt-1 text-2xl font-semibold tabular-nums">
          {formatMoney(card.annual_amount)}{" "}
          <span className="text-sm font-normal text-muted-foreground">a year</span>
        </p>
        {card.monthly_amount && (
          <p className="tabular-nums text-muted-foreground">{formatMoney(card.monthly_amount)} a month</p>
        )}
      </section>

      {card.inputs.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Inputs</h3>
          <table className="w-full text-sm">
            <tbody>
              {card.inputs.map((input) => (
                <tr key={input.name} className="border-b border-border last:border-0">
                  <td className="py-1.5 pr-3">{input.label}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{input.value}</td>
                  <td className="py-1.5 text-right text-xs text-muted-foreground">
                    {sourceLabel(input.source, card.record_label)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {card.scenarios.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Scenarios</h3>
          <ul className="space-y-1 text-sm">
            {card.scenarios.map((scenario) => (
              <li key={scenario.label} className="flex justify-between gap-4 tabular-nums">
                <span>{scenario.label}</span>
                <span>{formatMoney(scenario.annual_amount)} a year</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {Object.entries(card.blocks).map(([slot, markdown]) => (
        <section key={slot}>
          <h3 className="mb-2 text-sm font-semibold">{BLOCK_TITLES[slot] ?? slot}</h3>
          <div className="prose answer max-w-none">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // eslint-disable-next-line @typescript-eslint/no-unused-vars
                table: ({ node, ...props }) => (
                  <div className="overflow-x-auto">
                    <table {...props} />
                  </div>
                ),
              }}
            >
              {markdown}
            </ReactMarkdown>
          </div>
        </section>
      ))}

      <p className="text-xs text-muted-foreground">
        Figures come from the calculation engine, not from the written answer.
        {card.estimate ? " This is an estimate, not a benefit determination." : ""}
      </p>
    </div>
  );
}
