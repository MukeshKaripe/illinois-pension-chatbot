import { test } from "node:test";
import assert from "node:assert/strict";
import { cardBadge, cardFrom, formatMoney, isStopped, sourceLabel } from "./card.ts";

const card = {
  type: "card",
  section: "11-134",
  estimate: true,
  annual_amount: "32126.78",
  monthly_amount: "2677.23",
  inputs: [],
  scenarios: [],
  blocks: {},
};

test("finds the card among the audit trail's other entries", () => {
  const found = cardFrom([{ type: "recipe" }, card, { type: "verification" }]);
  assert.equal(found?.section, "11-134");
});

test("no card when the trail has none, or is not a list", () => {
  assert.equal(cardFrom([{ type: "recipe" }]), null);
  assert.equal(cardFrom(undefined), null);
  assert.equal(cardFrom({ type: "card" }), null);
});

test("a stopped answer says so in its trail", () => {
  assert.equal(isStopped([{ type: "stopped" }]), true);
  assert.equal(isStopped([card]), false);
  assert.equal(isStopped(null), false);
});

test("money reads as dollars, whatever shape it arrives in", () => {
  assert.equal(formatMoney("32126.78"), "$32,126.78");
  assert.equal(formatMoney("$32,126.78"), "$32,126.78");
  assert.equal(formatMoney(""), "—");
  assert.equal(formatMoney(null), "—");
  assert.equal(formatMoney("not a number"), "not a number");
});

test("sources read in the member's words", () => {
  assert.equal(sourceLabel("record"), "Record");
  assert.equal(sourceLabel("stated"), "You said");
  assert.equal(sourceLabel("statutory_parameter"), "Set by the Code");
  assert.equal(sourceLabel("something-new"), "something-new");
  // Unknown origin: no label at all, rather than a guess.
  assert.equal(sourceLabel(null), "");
});

test("the badge says estimate, and experimental when the fund is", () => {
  assert.equal(cardBadge({ estimate: true, support_level: "experimental" }), "Estimate · experimental for this fund");
  assert.equal(cardBadge({ estimate: true, support_level: "checked" }), "Estimate");
  assert.equal(cardBadge({ estimate: false, support_level: "experimental" }), "Experimental for this fund");
  assert.equal(cardBadge({ estimate: false, support_level: null }), null);
});
