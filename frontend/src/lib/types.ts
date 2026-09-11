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
  /** The profile the conversation uses (signed in only). */
  profile_id?: string | null;
  profile_label?: string | null;
};

/** A what-if the conversation is holding (spec §7), shown as a chip. */
export type Scenario = {
  label: string;
  annual_amount?: string | null;
  monthly_amount?: string | null;
};

/** What the side drawer is showing, if anything. */
export type Panel =
  | { kind: "section"; section: string }
  | { kind: "calculation"; card: CalculationCardData }
  | { kind: "profile-edit"; profileId: string }
  | { kind: "profile-new" };
