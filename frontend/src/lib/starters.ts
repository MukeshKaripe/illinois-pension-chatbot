/**
 * Starter prompts for the empty chat.
 *
 * Fixed strings, never a model call. Signed-in members see these too until
 * Phase 3 brings their profile to the client and the rules in spec §4 can pick
 * prompts from its facts.
 */
export const ANONYMOUS_STARTERS: readonly string[] = [
  "What does Article 11 cover?",
  "How is a pension calculated for a Chicago laborer?",
  "What is the difference between Tier 1 and Tier 2?",
  "Estimate a pension for someone aged 60 with 25 years of service.",
];
