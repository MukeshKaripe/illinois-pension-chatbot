import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  calculationSummary, cardHeadline, formatMoney, sourceLabel, whatIfQuestions, type CalculationCardData,
} from "@/lib/card";

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
interface CalculationPanelProps {
  card: CalculationCardData;
  /** Ask a what-if in the conversation: same engine and verification as typing it. */
  onAsk?: (question: string) => void;
  onOpenSection?: (section: string) => void;
  /** An answer is still being written; a new question would be refused. */
  busy?: boolean;
}

/** A printable page holding only the calculation summary. */
function printSummary(text: string) {
  const page = window.open("", "_blank", "width=720,height=900");
  if (!page) return;
  const pre = page.document.createElement("pre");
  pre.textContent = text;
  pre.style.cssText = "font: 14px/1.5 system-ui, sans-serif; white-space: pre-wrap; margin: 32px;";
  page.document.title = "Pension calculation";
  page.document.body.appendChild(pre);
  page.focus();
  page.print();
}

export function CalculationPanel({ card, onAsk, onOpenSection, busy }: CalculationPanelProps) {
  const headline = cardHeadline(card);
  const [copied, setCopied] = useState(false);
  const summary = calculationSummary(card);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(summary);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="space-y-6">
      <section>
        {card.person && <p className="text-sm font-semibold">{card.person}</p>}
        <p className="text-xs text-muted-foreground">
          {card.estimate ? "Estimate" : "Calculated"}
          {headline.label ? ` · ${headline.label}` : ""}
        </p>
        <p className="mt-1 text-2xl font-semibold tabular-nums">
          {formatMoney(headline.annual_amount)}{" "}
          <span className="text-sm font-normal text-muted-foreground">a year</span>
        </p>
        {headline.monthly_amount && (
          <p className="tabular-nums text-muted-foreground">{formatMoney(headline.monthly_amount)} a month</p>
        )}
        {card.not_payable_now && (
          <p className="mt-1 text-sm text-muted-foreground">Retiring now: not payable yet</p>
        )}
      </section>

      {(card.formula_name || card.section) && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Formula</h3>
          {card.formula_name && <p className="text-sm">{card.formula_name}</p>}
          {card.section && (
            <button
              type="button"
              onClick={() => onOpenSection?.(card.section as string)}
              className="mt-2 rounded-md border border-border px-2.5 py-1 text-xs font-medium tabular-nums text-primary hover:bg-muted"
            >
              Read 40 ILCS 5/{card.section}{card.subsection ?? ""}
            </button>
          )}
        </section>
      )}

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

      {onAsk && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">What if they retire…</h3>
          <div className="flex flex-wrap gap-2">
            {whatIfQuestions(card).map((ask) => (
              <button
                key={ask.label}
                type="button"
                disabled={busy}
                onClick={() => onAsk(ask.question)}
                className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-primary hover:bg-muted disabled:opacity-50"
              >
                {ask.label}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">Asked in the conversation, so the answer is worked and checked like any other.</p>
        </section>
      )}

      {card.scenarios.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">What-ifs so far</h3>
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

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => void copy()}
          className="rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted"
        >
          {copied ? "Copied" : "Copy summary"}
        </button>
        <button
          type="button"
          onClick={() => printSummary(summary)}
          className="rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted"
        >
          Print
        </button>
      </div>

      <p className="text-xs text-muted-foreground">
        Figures come from the calculation engine, not from the written answer.
        {card.estimate ? " This is an estimate, not a benefit determination." : ""}
      </p>
    </div>
  );
}
