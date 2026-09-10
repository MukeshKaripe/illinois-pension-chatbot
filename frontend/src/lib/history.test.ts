import { test } from "node:test";
import assert from "node:assert/strict";
import { groupSessions } from "./history.ts";

// Local times on both sides, so the result does not depend on the machine's zone.
const now = new Date(2026, 8, 10, 15, 0);
const at = (day: number, hour: number, minute = 0) =>
  new Date(2026, 8, day, hour, minute).toISOString();

test("buckets by local calendar day", () => {
  const groups = groupSessions(
    [
      { id: "a", last_active_at: at(10, 9) },
      { id: "b", last_active_at: at(9, 23, 59) },
      { id: "c", last_active_at: at(1, 12) },
    ],
    now,
  );
  assert.deepEqual(
    groups.map((g) => [g.label, g.items.map((i) => i.id)]),
    [["Today", ["a"]], ["Yesterday", ["b"]], ["Earlier", ["c"]]],
  );
});

test("midnight belongs to the day it starts", () => {
  const groups = groupSessions([{ id: "m", last_active_at: at(10, 0) }], now);
  assert.equal(groups[0].label, "Today");
});

test("sorts newest first within a group", () => {
  const groups = groupSessions(
    [
      { id: "old", last_active_at: at(10, 8) },
      { id: "new", last_active_at: at(10, 14) },
    ],
    now,
  );
  assert.deepEqual(groups[0].items.map((i) => i.id), ["new", "old"]);
});

test("omits empty groups", () => {
  const groups = groupSessions([{ id: "c", last_active_at: at(1, 12) }], now);
  assert.deepEqual(groups.map((g) => g.label), ["Earlier"]);
});

test("no sessions, no groups", () => {
  assert.deepEqual(groupSessions([], now), []);
});
