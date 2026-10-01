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
  const priced = (card.scenarios ?? []).filter((s) => s.annual_amount);
  const today = { label: "Retiring now", annual_amount: card.annual_amount, monthly_amount: card.monthly_amount };
  if (priced.length === 1) {
    const only = priced[0];
    return { label: only.label, annual_amount: only.annual_amount, monthly_amount: only.monthly_amount,
             others: card.annual_amount ? [today] : [] };
  }
  return { label: null, annual_amount: card.annual_amount, monthly_amount: card.monthly_amount, others: priced };
}
