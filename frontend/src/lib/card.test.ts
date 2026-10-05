import { test } from "node:test";
import assert from "node:assert/strict";
import {
  calculationSummary, cardBadge, cardFrom, cardHeadline, cardsFrom, formatMoney, isStopped, receiptText, sourceLabel,
  whatIfQuestions, type CalculationCardData,
} from "./card.ts";

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

test("today's figure filed as a scenario is not a what-if", () => {
  const h = cardHeadline({ annual_amount: "51403.06", monthly_amount: "4283.59",
    scenarios: [{ label: "0 more years", annual_amount: "51403.06" }] });
  assert.equal(h.label, null);
  assert.equal(h.others.length, 0);
});

const recordCard: CalculationCardData = {
  ...card,
  type: "card",
  section: "8-138",
  subsection: "(g-1)",
  annual_amount: "51403.06",
  monthly_amount: "4283.59",
  formula_name: "Tier 1 retirement annuity at 60 or over: 2.4% per year, capped at 80% (Section 8-138(g-1))",
  record_label: "PBMS record",
  inputs: [
    { name: "final_average_salary", label: "Final average salary", value: "83435.68", source: "record" },
    { name: "service_years", label: "Years of service", value: "25.67", source: "record" },
  ],
  scenarios: [],
};

test("the receipt is one line: figure, section, where the inputs came from", () => {
  assert.equal(receiptText(recordCard), "$51,403.06 a year · § 8-138(g-1) · from PBMS record");
});

test("a what-if receipt leads with the what-if and keeps today's figure", () => {
  const text = receiptText({ ...recordCard, scenarios: [{ label: "at age 65 (2 more years)", annual_amount: "54851.50" }] });
  assert.equal(text, "at age 65 (2 more years): $54,851.50 a year (now $51,403.06) · § 8-138(g-1) · from PBMS record");
});

test("a receipt says when retiring now does not pay", () => {
  const text = receiptText({ ...recordCard, annual_amount: null, not_payable_now: "retiring at 62 or over",
    record_label: null, inputs: [{ name: "service_years", label: "Years of service", value: "10", source: "stated" }],
    scenarios: [{ label: "at age 66", annual_amount: "16920.00" }] });
  assert.equal(text, "at age 66: $16,920.00 a year · retiring now: not payable yet · § 8-138(g-1) · from what you said");
});

test("what-if questions name the person when there is one", () => {
  const asks = whatIfQuestions({ ...recordCard, person: "TH_FN O'_LN" });
  assert.deepEqual(asks.map((a) => a.label), ["At 60", "At 62", "At 65", "At 67", "+1 year", "+2 years"]);
  assert.equal(asks[2].question, "What if TH_FN O'_LN retires at 65?");
  assert.equal(whatIfQuestions(recordCard)[4].question, "What if they retire 1 year later?");
});

test("the summary carries the formula, inputs with sources, figure and the estimate note", () => {
  const text = calculationSummary(recordCard);
  assert.match(text, /2\.4% per year/);
  assert.match(text, /Final average salary: 83435\.68 \(PBMS record\)/);
  assert.match(text, /\$51,403\.06 a year \(\$4,283\.59 a month\)/);
  assert.match(text, /40 ILCS 5\/8-138\(g-1\)/);
  assert.match(text, /estimate/i);
});
