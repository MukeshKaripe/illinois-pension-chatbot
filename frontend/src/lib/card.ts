/**
 * The calculation card, as the server sends it inside a message's `tool_calls`
 * (see `agent/card.py`), and the few helpers that read it.
 *
 * The card and the "stopped" marker both live in the audit trail, so a live
 * message and one reloaded from history are read the same way.
 */

import type { SupportLevel } from "./api.ts";

/** `source` is null when nobody knows where the value came from. */
export type CardInput = { name: string; label: string; value: string; source: string | null };

export type CardScenario = {
  label: string;
  annual_amount?: string | null;
  monthly_amount?: string | null;
};

export type CalculationCardData = {
  type: "card";
  formula_name?: string | null;
  section?: string | null;
  subsection?: string | null;
  estimate: boolean;
  annual_amount?: string | null;
  monthly_amount?: string | null;
  inputs: CardInput[];
  scenarios: CardScenario[];
  blocks: Record<string, string>;
  /** How well the assistant knows the fund the figure was computed under. */
  support_level?: SupportLevel | null;
  /** "PBMS record" when the record inputs came from the fund's own records. */
  record_label?: string | null;
  /** Whose figure, when the turn compared several people. */
  person?: string | null;
  /** Why retiring today has no figure (Tier 2 before 62), when only a what-if was priced. */
  not_payable_now?: string | null;
};

/** The card's badge: an estimate, and whether its fund is experimental. */
export function cardBadge(card: Pick<CalculationCardData, "estimate" | "support_level">): string | null {
  const experimental = card.support_level === "experimental";
  if (card.estimate) return experimental ? "Estimate · experimental for this fund" : "Estimate";
  return experimental ? "Experimental for this fund" : null;
}

const entries = (toolCalls: unknown): Record<string, unknown>[] =>
  Array.isArray(toolCalls)
    ? toolCalls.filter((t): t is Record<string, unknown> => typeof t === "object" && t !== null)
    : [];

/** Every calculation card in a message: one per person when a turn compared several. */
export function cardsFrom(toolCalls: unknown): CalculationCardData[] {
  return entries(toolCalls).filter((t) => t.type === "card") as unknown as CalculationCardData[];
}

export function cardFrom(toolCalls: unknown): CalculationCardData | null {
  return cardsFrom(toolCalls)[0] ?? null;
}

export function isStopped(toolCalls: unknown): boolean {
  return entries(toolCalls).some((t) => t.type === "stopped");
}

/** Dollars, whether the server sent "32126.78" or "$32,126.78". */
export function formatMoney(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(String(value).replace(/[$,\s]/g, ""));
  if (!Number.isFinite(amount)) return String(value);
  return amount.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

/** The worksheet's provenance classes, in the member's words. */
const SOURCE_LABELS: Record<string, string> = {
  record: "Record",
  stated: "You said",
  hypothetical: "Scenario",
  assumed: "Assumed",
  derived: "Derived",
  // Not an assumption: the Code states it, and the evidence drawer has the
  // section. Labelling it "Assumed" told a member to distrust the most
  // checkable number on the card.
  statutory_parameter: "Set by the Code",
  withheld: "On file, not used",
  stand_in: "Your value, standing in",
};

/** Empty for an unknown origin: the card shows no label rather than a guess. */
export const sourceLabel = (source: string | null | undefined, recordLabel?: string | null): string =>
  source === "record" && recordLabel ? recordLabel : source ? (SOURCE_LABELS[source] ?? source) : "";

/**
 * Which figure the card leads with. A turn that asked one what-if ("and if
 * they worked until 65?") leads with that figure and keeps today's beneath it;
 * the card used to show only today's, under an answer about age 65. Several
 * what-ifs keep today's as the headline and list each below.
 */
export function cardHeadline(card: Pick<CalculationCardData, "annual_amount" | "monthly_amount" | "scenarios">): {
  label: string | null;
  annual_amount?: string | null;
  monthly_amount?: string | null;
  others: CardScenario[];
} {
  // A "retire now" answer files today's figure as a scenario too ("0 more
  // years"); it is not a what-if, and leading with it shows the figure twice.
  const priced = (card.scenarios ?? []).filter(
    (s) => s.annual_amount && Number(s.annual_amount) !== Number(card.annual_amount),
  );
  const today = { label: "Retiring now", annual_amount: card.annual_amount, monthly_amount: card.monthly_amount };
  if (priced.length === 1) {
    const only = priced[0];
    return { label: only.label, annual_amount: only.annual_amount, monthly_amount: only.monthly_amount,
             others: card.annual_amount ? [today] : [] };
  }
  return { label: null, annual_amount: card.annual_amount, monthly_amount: card.monthly_amount, others: priced };
}

/** Where the card's inputs came from, in a few words: the fund's record first, then the user. */
function provenance(card: Pick<CalculationCardData, "inputs" | "record_label">): string | null {
  const sources = new Set(card.inputs.map((input) => input.source));
  if (sources.has("record")) return `from ${card.record_label ?? "the record"}`;
  if (sources.has("stated")) return "from what you said";
  return null;
}

/** "§ 8-138(g-1)", or nothing when the card names no section. */
function sectionRef(card: Pick<CalculationCardData, "section" | "subsection">): string | null {
  return card.section ? `§ ${card.section}${card.subsection ?? ""}` : null;
}

/**
 * The calculation in one line under the answer. The card used to repeat the
 * answer's figure and inputs in a block that got in the way of the
 * conversation; everything it held is a click away in the drawer.
 */
export function receiptText(card: CalculationCardData): string {
  const headline = cardHeadline(card);
  let figure = `${formatMoney(headline.annual_amount)} a year`;
  if (headline.label) {
    figure = `${headline.label}: ${figure}`;
    if (card.annual_amount) figure += ` (now ${formatMoney(card.annual_amount)})`;
  }
  const parts = [figure];
  if (card.not_payable_now) parts.push("retiring now: not payable yet");
  for (const part of [sectionRef(card), provenance(card)]) if (part) parts.push(part);
  return parts.join(" · ");
}

export type WhatIf = { label: string; question: string };

/**
 * The what-ifs offered in the drawer, as questions asked in the conversation:
 * the same engine and verification as typing them, and the answer is on record.
 */
export function whatIfQuestions(card: Pick<CalculationCardData, "person">): WhatIf[] {
  const who = card.person;
  const at = (age: number) => (who ? `What if ${who} retires at ${age}?` : `What if they retire at ${age}?`);
  const later = (years: number) => {
    const span = `${years} year${years === 1 ? "" : "s"} later`;
    return who ? `What if ${who} retires ${span}?` : `What if they retire ${span}?`;
  };
  return [
    ...[60, 62, 65, 67].map((age) => ({ label: `At ${age}`, question: at(age) })),
    { label: "+1 year", question: later(1) },
    { label: "+2 years", question: later(2) },
  ];
}

/** A plain-text record of the calculation, for copying into a file or an email. */
export function calculationSummary(card: CalculationCardData): string {
  const lines: string[] = [];
  if (card.person) lines.push(card.person);
  if (card.formula_name) lines.push(card.formula_name);
  if (card.section) lines.push(`40 ILCS 5/${card.section}${card.subsection ?? ""}`);
  lines.push("");
  if (card.annual_amount) {
    lines.push(`Retiring now: ${formatMoney(card.annual_amount)} a year`
      + (card.monthly_amount ? ` (${formatMoney(card.monthly_amount)} a month)` : ""));
  } else if (card.not_payable_now) {
    lines.push("Retiring now: not payable yet");
  }
  for (const scenario of card.scenarios.filter((s) => s.annual_amount)) {
    lines.push(`${scenario.label}: ${formatMoney(scenario.annual_amount)} a year`
      + (scenario.monthly_amount ? ` (${formatMoney(scenario.monthly_amount)} a month)` : ""));
  }
  if (card.inputs.length) {
    lines.push("", "Inputs:");
    for (const input of card.inputs) {
      const source = sourceLabel(input.source, card.record_label);
      lines.push(`  ${input.label}: ${input.value}${source ? ` (${source})` : ""}`);
    }
  }
  lines.push("", card.estimate
    ? "This is an estimate; the retirement fund makes the binding determination."
    : "Calculated from the Illinois Pension Code.");
  return lines.join("\n");
}
