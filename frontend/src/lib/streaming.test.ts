import { test } from "node:test";
import assert from "node:assert/strict";
import { splitStreaming } from "./streaming.ts";

test("a paragraph being written renders as markdown at once", () => {
  const text = "First paragraph.\n\nThe **reduced** annuity is";
  assert.deepEqual(splitStreaming(text, true), { settled: text, tail: "" });
});

test("a table still being written is held back as plain text", () => {
  const text = "Intro.\n\n| Step | Amount |\n|---|---:|\n| Age | 6";
  assert.deepEqual(splitStreaming(text, true), {
    settled: "Intro.",
    tail: "\n\n| Step | Amount |\n|---|---:|\n| Age | 6",
  });
});

test("a table held back at the very start has nothing settled", () => {
  const text = "| Step | Amount |\n|---|";
  assert.deepEqual(splitStreaming(text, true), { settled: "", tail: text });
});

test("a finished message is never split", () => {
  const text = "Intro.\n\n| Step | Amount |\n|---|---:|";
  assert.deepEqual(splitStreaming(text, false), { settled: text, tail: "" });
});
