import { test } from "node:test";
import assert from "node:assert/strict";
import { editsFrom, groupPresets, valuesFrom } from "./funds.ts";
import type { FieldSpec, Persona } from "./api.ts";

const FIELDS: FieldSpec[] = [
  { name: "hire_date", label: "Start date", type: "date", min: null, max: null, options: [] },
  { name: "total_service_years", label: "Years of service", type: "years", min: "0", max: null, options: [] },
  { name: "current_annual_salary", label: "Annual salary", type: "money", min: "0.01", max: "1000000", options: [] },
];

const preset = (
  key: string,
  article: number,
  member_label: string,
  employer_group: string,
  label = "Mid-career, still working",
): Persona => ({
  key,
  article_number: article,
  member_label,
  employer_group,
  support_level: article === 11 || article === 12 ? "checked" : "experimental",
  fields: FIELDS,
  label,
  summary: `${member_label}, mid-career.`,
  tier: 1,
  age: 50,
  birth_date: "1976-01-01",
  hire_date: "2001-01-01",
  employment_status: "active",
  total_service_years: "24.00",
  current_annual_salary: "90000.00",
});

// In the server's order: the pilot presets first, then one per other fund.
const PRESETS = [
  preset("article-11-standard", 11, "Chicago laborer", "City of Chicago"),
  preset("article-11-near-retirement", 11, "Chicago laborer", "City of Chicago", "Close to retirement"),
  preset("article-12-standard", 12, "Chicago Park District employee", "City of Chicago"),
  preset("article-2-standard", 2, "General Assembly member", "State of Illinois"),
  preset("article-3-standard", 3, "Police officer outside Chicago", "Local and regional"),
  preset("article-5-standard", 5, "Chicago police officer", "City of Chicago"),
  preset("article-9-standard", 9, "Cook County employee", "Cook County"),
  preset("article-16-standard", 16, "Teacher outside Chicago", "State of Illinois"),
  preset("article-17-standard", 17, "Chicago Public Schools teacher", "City of Chicago"),
];

test("groups follow the employer order, funds by Article, presets as served", () => {
  const groups = groupPresets(PRESETS);
  assert.deepEqual(
    groups.map((g) => g.group),
    ["City of Chicago", "Cook County", "State of Illinois", "Local and regional"],
  );
  assert.deepEqual(
    groups[0].funds.map((f) => f.article),
    [5, 11, 12, 17],
  );
  assert.deepEqual(
    groups[0].funds[1].presets.map((p) => p.key),
    ["article-11-standard", "article-11-near-retirement"],
  );
  assert.equal(groups[0].funds[0].support_level, "experimental");
});

test("search finds a fund by the words a member would use", () => {
  const teachers = groupPresets(PRESETS, "teacher");
  assert.deepEqual(
    teachers.flatMap((g) => g.funds.map((f) => f.article)),
    [17, 16],
  );
  assert.deepEqual(
    groupPresets(PRESETS, "park").flatMap((g) => g.funds.map((f) => f.article)),
    [12],
  );
  // Every word must match, in any field: the group, the fund, the preset.
  assert.deepEqual(
    groupPresets(PRESETS, "chicago police").flatMap((g) => g.funds.map((f) => f.article)),
    [5, 3],
  );
  assert.deepEqual(groupPresets(PRESETS, "astronaut"), []);
});

test("a preset's values fill the form, as strings", () => {
  assert.deepEqual(valuesFrom(FIELDS, PRESETS[0]), {
    hire_date: "2001-01-01",
    total_service_years: "24.00",
    current_annual_salary: "90000.00",
  });
});

test("only touched fields are sent, and a number retyped the same is not an edit", () => {
  const original = valuesFrom(FIELDS, PRESETS[0]);
  assert.deepEqual(editsFrom(FIELDS, original, { ...original }), {});
  assert.deepEqual(
    editsFrom(FIELDS, original, { ...original, current_annual_salary: "90000", hire_date: "2003-05-01" }),
    { hire_date: "2003-05-01" },
  );
  // A cleared field keeps the stored value rather than sending an empty one.
  assert.deepEqual(editsFrom(FIELDS, original, { ...original, total_service_years: "" }), {});
  assert.deepEqual(editsFrom(FIELDS, original, { ...original, total_service_years: "25.5" }), {
    total_service_years: "25.5",
  });
});
