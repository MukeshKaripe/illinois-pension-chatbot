import { test } from "node:test";
import assert from "node:assert/strict";
import { parseFrame, takeFrames } from "./sse.ts";

test("parses a frame's event name and JSON data", () => {
  assert.deepEqual(parseFrame('event: token\ndata: {"delta":"Hi"}'), {
    event: "token",
    data: { delta: "Hi" },
  });
});

test("defaults the event name to message", () => {
  assert.deepEqual(parseFrame('data: {"a":1}'), { event: "message", data: { a: 1 } });
});

test("drops a frame with no data or with invalid JSON", () => {
  assert.equal(parseFrame("event: ping"), null);
  assert.equal(parseFrame("data: {nope"), null);
});

test("keeps an incomplete frame as the remainder until it closes", () => {
  const first = takeFrames('event: token\ndata: {"delta":"Hel');
  assert.deepEqual(first.events, []);

  const second = takeFrames(first.rest + 'lo"}\n\nevent: done\ndata: {}\n\n');
  assert.deepEqual(second.events, [
    { event: "token", data: { delta: "Hello" } },
    { event: "done", data: {} },
  ]);
  assert.equal(second.rest, "");
});
