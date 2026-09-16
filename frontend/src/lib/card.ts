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

export function cardFrom(toolCalls: unknown): CalculationCardData | null {
  const found = entries(toolCalls).find((t) => t.type === "card");
  return found ? (found as unknown as CalculationCardData) : null;
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
export const sourceLabel = (source: string | null | undefined): string =>
  source ? (SOURCE_LABELS[source] ?? source) : "";
