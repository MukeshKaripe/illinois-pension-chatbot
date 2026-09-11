/**
 * Starter prompts for the empty chat, one per thing this assistant is for.
 *
 * Fixed strings and fixed rules, never a model call. Anonymous visitors get a
 * general four; a signed-in member gets four chosen from their profile's facts
 * (spec §4) — someone close to retirement is asked about retiring, someone
 * early in a career about when eligibility begins.
 */

export type StarterKind = "law" | "eligibility" | "estimate" | "compare";
export type Starter = { kind: StarterKind; label: string; prompt: string };

const PURPOSE: Record<StarterKind, string> = {
  law: "Understand the law",
  eligibility: "Check eligibility",
  estimate: "Estimate a pension",
  compare: "Compare options",
};

const starter = (kind: StarterKind, prompt: string): Starter => ({
  kind,
  label: PURPOSE[kind],
  prompt,
});

export const ANONYMOUS_STARTERS: readonly Starter[] = [
  starter("law", "What does Article 11 cover?"),
  starter("eligibility", "When can a Chicago laborer retire with a full pension?"),
  starter("estimate", "Estimate a pension for someone aged 60 with 25 years of service."),
  starter("compare", "What is the difference between Tier 1 and Tier 2?"),
];

/** The profile facts the rules read. */
export type StarterFacts = {
  employment_status: string;
  age: number;
  total_service_years: string;
  hire_date: string;
};

/** As in `facts.derive_tier`: first joined on or after this date is Tier 2. */
const TIER_2_FROM = "2011-01-01";

/** Fill to four, in this order, after the profile's own. */
const FILLERS: readonly Starter[] = [
  starter("compare", "Compare retiring at 60, 62 and 65."),
  starter("law", "What counts as salary for my pension?"),
  starter("law", "What happens to my pension if I leave early?"),
  // Reached only by a profile no rule speaks to, such as a separated member.
  starter("eligibility", "When could I start drawing a pension?"),
];

export function startersFor(profile: StarterFacts): Starter[] {
  const service = Number(profile.total_service_years);
  const picked: Starter[] = [];

  if (profile.employment_status === "retired") {
    picked.push(starter("law", "How do annual increases work on my pension?"));
  } else if (profile.employment_status === "active") {
    if (profile.age >= 55) picked.push(starter("eligibility", "Can I retire now, and what would I get?"));
    if (service >= 28) picked.push(starter("estimate", "Am I close to the maximum pension?"));
    if (profile.hire_date >= TIER_2_FROM) picked.push(starter("law", "How is Tier 2 different for me?"));
    if (service < 10) picked.push(starter("eligibility", "When will I be eligible for a pension?"));
  }

  for (const filler of FILLERS) {
    if (picked.length >= 4) break;
    picked.push(filler);
  }
  return picked.slice(0, 4);
}
