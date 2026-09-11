/** Shapes the chat UI holds. The server's JSON is read into these at the edge. */

import type { CalculationCardData } from "./card";

export type Citation = { section: string; title?: string };

export type Message = {
  id: string;
  /** "user" for the member; anything else is the assistant. */
  role: string;
  content: string;
  created_at: string;
  citations?: Citation[];
  verified_citations?: string[];
  tool_calls?: unknown[];
  guardrail_passed?: boolean;
  /** The member pressed Stop before the answer finished. */
  stopped?: boolean;
};

export type SessionSummary = {
  id: string;
  title: string | null;
  started_at?: string;
  last_active_at: string;
};

/** What the side drawer is showing, if anything. */
export type Panel =
  | { kind: "section"; section: string }
  | { kind: "calculation"; card: CalculationCardData };
