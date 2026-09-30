import assert from "node:assert/strict";
import { test } from "node:test";

import {
  allChosen, byDepartment, candidateLabel, chipLabel, MAX_LISTED, suggestionFrom, whichOneQuestion,
} from "./subject.ts";

const p = (ref: string, department: string | null, name = "KA_LS MA_LP") => ({
  ref, display_name: name, first_name: null, last_name: null, employer: "City of Chicago", department, birth_year: 1964,
});

test("chip and candidate labels never include the ref", () => {
  assert.equal(chipLabel(p("M-1", "Finance")), "KA_LS MA_LP · Finance");
  assert.equal(chipLabel(p("M-1", null)), "KA_LS MA_LP · City of Chicago");
  assert.equal(candidateLabel(p("M-1", "Finance")), "Finance · City of Chicago · born 1964");
  assert.ok(!candidateLabel(p("M-1", "Finance")).includes("M-1"));
});

test("more than MAX_LISTED candidates group by department, largest first, no department last", () => {
  const many = [...Array(MAX_LISTED + 3)].map((_, i) => p(`M-${i}`, i % 3 === 0 ? "Streets" : i % 3 === 1 ? "Water" : null));
  assert.deepEqual(byDepartment(many).map((g) => [g.department, g.candidates.length]),
    [["Streets", 5], ["Water", 5], ["No department on file", 5]]);
});

test("a suggestion is read from tool_calls, and 'all chosen' counts only cards with candidates", () => {
  const s = { kind: "choose" as const, question: "q", choices: [
    { first_name: "A", last_name: "B", unavailable: false, candidates: [p("M-1", "F")] },
    { first_name: "C", last_name: "D", unavailable: false, candidates: [] },
  ] };
  assert.equal(suggestionFrom([{ type: "card" }, { type: "subject_suggestion", ...s }])?.question, "q");
  assert.equal(suggestionFrom(undefined), null);
  assert.equal(allChosen(s, {}), false);
  assert.equal(allChosen(s, { 0: "M-1" }), true);
});

test("a which-one choice re-asks the question about the chosen person by name", () => {
  assert.equal(whichOneQuestion("what would she get at 65?", p("M-2", "Water", "QW_XZ PL_MR")),
    "About QW_XZ PL_MR: what would she get at 65?");
});
