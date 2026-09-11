import { test } from "node:test";
import assert from "node:assert/strict";
import { startersFor } from "./starters.ts";

const prompts = (profile: Parameters<typeof startersFor>[0]) => startersFor(profile).map((s) => s.prompt);

test("close to retirement asks about retiring and the maximum", () => {
  assert.deepEqual(
    prompts({ employment_status: "active", age: 59, total_service_years: "31.00", hire_date: "1995-06-05" }),
    [
      "Can I retire now, and what would I get?",
      "Am I close to the maximum pension?",
      "Compare retiring at 60, 62 and 65.",
      "What counts as salary for my pension?",
    ],
  );
});

test("early career asks about Tier 2 and when eligibility begins", () => {
  assert.deepEqual(
    prompts({ employment_status: "active", age: 34, total_service_years: "6.00", hire_date: "2020-09-01" }),
    [
      "How is Tier 2 different for me?",
      "When will I be eligible for a pension?",
      "Compare retiring at 60, 62 and 65.",
      "What counts as salary for my pension?",
    ],
  );
});

test("an annuity in payment is asked about differently", () => {
  assert.deepEqual(
    prompts({ employment_status: "retired", age: 62, total_service_years: "28.00", hire_date: "1997-01-20" }),
    [
      "How do annual increases work on my pension?",
      "Compare retiring at 60, 62 and 65.",
      "What counts as salary for my pension?",
      "What happens to my pension if I leave early?",
    ],
  );
});

test("always four, never repeated, each with a purpose", () => {
  for (const profile of [
    { employment_status: "active", age: 56, total_service_years: "21.50", hire_date: "2005-03-01" },
    { employment_status: "separated", age: 45, total_service_years: "12.00", hire_date: "2010-01-01" },
  ]) {
    const starters = startersFor(profile);
    assert.equal(starters.length, 4);
    assert.equal(new Set(starters.map((s) => s.prompt)).size, 4);
    assert.ok(starters.every((s) => s.label && s.kind));
  }
});
