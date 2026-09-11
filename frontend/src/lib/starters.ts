/**
 * Starter prompts for the empty chat, one per thing this assistant is for.
 *
 * Fixed strings, never a model call. Signed-in members see these too until
 * Phase 3 brings their profile to the client and the rules in spec §4 can pick
 * prompts from its facts.
 */

export type StarterKind = "law" | "eligibility" | "estimate" | "compare";
export type Starter = { kind: StarterKind; label: string; prompt: string };

export const ANONYMOUS_STARTERS: readonly Starter[] = [
  { kind: "law", label: "Understand the law", prompt: "What does Article 11 cover?" },
  {
    kind: "eligibility",
    label: "Check eligibility",
    prompt: "When can a Chicago laborer retire with a full pension?",
  },
  {
    kind: "estimate",
    label: "Estimate a pension",
    prompt: "Estimate a pension for someone aged 60 with 25 years of service.",
  },
  {
    kind: "compare",
    label: "Compare options",
    prompt: "What is the difference between Tier 1 and Tier 2?",
  },
];
