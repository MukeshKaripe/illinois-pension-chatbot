import { test } from "node:test";
import assert from "node:assert/strict";
import { linkSections, sectionFromHref } from "./sections.ts";

test("links a verified section however the answer names it", () => {
  assert.equal(
    linkSections("See § 11-134 and Section 11-134.", ["11-134"]),
    "See [§ 11-134](#section-11-134) and [Section 11-134](#section-11-134).",
  );
});

test("leaves sections the answer did not rest on alone", () => {
  assert.equal(linkSections("Section 11-167 also applies.", ["11-134"]), "Section 11-167 also applies.");
  assert.equal(linkSections("Section 11-134", []), "Section 11-134");
});

test("keeps the subsection outside the link", () => {
  assert.equal(
    linkSections("Under Section 11-134(a)", ["11-134"]),
    "Under [Section 11-134](#section-11-134)(a)",
  );
});

test("does not link a bare citation or re-link a link", () => {
  assert.equal(linkSections("40 ILCS 5/11-134", ["11-134"]), "40 ILCS 5/11-134");
  const linked = "[Section 11-134](#section-11-134)";
  assert.equal(linkSections(linked, ["11-134"]), linked);
});

test("reads the section back out of a link", () => {
  assert.equal(sectionFromHref("#section-1-113.4a"), "1-113.4a");
  assert.equal(sectionFromHref("https://example.com"), null);
  assert.equal(sectionFromHref(undefined), null);
});
