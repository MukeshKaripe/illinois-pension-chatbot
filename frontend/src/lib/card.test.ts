import { test } from "node:test";
import assert from "node:assert/strict";
import { cardBadge, cardFrom, cardHeadline, cardsFrom, formatMoney, isStopped, sourceLabel } from "./card.ts";

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

test("every card in a message, and the fund's record named on record inputs", () => {
  const calls = [{ type: "card", person: "A" }, { type: "subjects" }, { type: "card", person: "B" }];
  assert.deepEqual(cardsFrom(calls).map((c) => c.person), ["A", "B"]);
  assert.equal(sourceLabel("record", "PBMS record"), "PBMS record");
  assert.equal(sourceLabel("record"), "Record");
});

test("one what-if leads the card, with today's figure beneath", () => {
  const h = cardHeadline({ annual_amount: "51403.06", monthly_amount: "4283.59",
    scenarios: [{ label: "at age 65", annual_amount: "54851.50", monthly_amount: "4570.96" }] });
  assert.equal(h.label, "at age 65");
  assert.equal(h.annual_amount, "54851.50");
  assert.deepEqual(h.others.map((o) => o.annual_amount), ["51403.06"]);
});

test("no what-if, or several, keep today's figure as the headline", () => {
  assert.equal(cardHeadline({ annual_amount: "100", monthly_amount: null, scenarios: [] }).label, null);
  const several = cardHeadline({ annual_amount: "100", monthly_amount: null,
    scenarios: [{ label: "at 62", annual_amount: "90" }, { label: "at 67", annual_amount: "120" }] });
  assert.equal(several.annual_amount, "100");
  assert.equal(several.others.length, 2);
});
