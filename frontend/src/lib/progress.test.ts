import assert from "node:assert/strict";
import { test } from "node:test";

import { PROGRESS_AFTER_SECONDS, visibleSteps } from "./progress.ts";

test("a fast turn shows no steps", () => {
  assert.deepEqual(visibleSteps([{ text: "searching", at: 1.2 }]), []);
});

test("a slow turn shows what it is doing", () => {
  const steps = [
    { text: "searching the Code for annuity reduction", at: 1.2 },
    { text: "reading Section 11-134", at: 5.4 },
  ];
  assert.deepEqual(
    visibleSteps(steps).map((s) => s.text),
    ["searching the Code for annuity reduction", "reading Section 11-134"],
  );
});

test("crossing the threshold reveals what came before it", () => {
  // Once the member has been made to wait, they are owed the whole story --
  // not just the part that happened after the four-second mark.
  const steps = [
    { text: "one", at: 0.5 },
    { text: "two", at: 4.5 },
  ];
  assert.equal(visibleSteps(steps).length, 2);
});

test("no steps is not a slow turn", () => {
  assert.deepEqual(visibleSteps([]), []);
});

test("the threshold is four seconds", () => {
  assert.equal(PROGRESS_AFTER_SECONDS, 4);
  assert.equal(visibleSteps([{ text: "x", at: 4 }]).length, 1);
  assert.equal(visibleSteps([{ text: "x", at: 3.99 }]).length, 0);
});

test("the threshold is overridable for tests", () => {
  assert.equal(visibleSteps([{ text: "x", at: 2 }], { after: 1 }).length, 1);
});
