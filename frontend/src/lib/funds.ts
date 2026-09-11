/**
 * Presets by employer, and the profile form's values — the pure half of the
 * picker and the edit form (spec §6, Phase 3b).
 *
 * Members know who they work for, not which Article covers them, so the picker
 * leads with the employer group and the fund's member label; the Article
 * number follows. The form is rendered from each fund's `fields`, so its
 * bounds are the server's.
 */
import type { FieldSpec, Persona, ProfileEdits, SupportLevel } from "./api.ts";

export const GROUP_ORDER = [
  "City of Chicago",
  "Cook County",
  "State of Illinois",
  "Local and regional",
] as const;

/** Said wherever an experimental fund is chosen or used. */
export const EXPERIMENTAL_NOTE =
  "Experimental for this fund: answers cite its statute, but a figure may be unavailable or not yet checked.";

export type FundPresets = {
  article: number;
  member_label: string;
  support_level: SupportLevel;
  presets: Persona[];
};
export type EmployerGroup = { group: string; funds: FundPresets[] };

const rank = (group: string) => {
  const index = (GROUP_ORDER as readonly string[]).indexOf(group);
  return index === -1 ? GROUP_ORDER.length : index;
};

const searchable = (p: Persona) =>
  `${p.member_label} ${p.employer_group} ${p.label} ${p.summary} article ${p.article_number}`.toLowerCase();

/** Employer groups in order, funds by Article, presets as the server sent them. */
export function groupPresets(personas: Persona[], query = ""): EmployerGroup[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const groups = new Map<string, Map<number, FundPresets>>();
  for (const preset of personas) {
    const text = searchable(preset);
    if (!words.every((word) => text.includes(word))) continue;
    const funds = groups.get(preset.employer_group) ?? new Map<number, FundPresets>();
    groups.set(preset.employer_group, funds);
    const fund = funds.get(preset.article_number) ?? {
      article: preset.article_number,
      member_label: preset.member_label,
      support_level: preset.support_level,
      presets: [],
    };
    fund.presets.push(preset);
    funds.set(preset.article_number, fund);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([group, funds]) => ({
      group,
      funds: [...funds.values()].sort((a, b) => a.article - b.article),
    }));
}

/** Form values, one string per field, keyed by `FieldSpec.name`. */
export type FieldValues = Record<string, string>;

export function valuesFrom(fields: FieldSpec[], source: object): FieldValues {
  const record = source as Record<string, unknown>;
  const extra = (record.extra_facts ?? {}) as Record<string, unknown>;
  return Object.fromEntries(
    fields.map((field) => {
      const value = field.extra ? extra[field.name] : record[field.name];
      return [field.name, value == null ? "" : String(value)];
    }),
  );
}

const NUMERIC = new Set<FieldSpec["type"]>(["years", "money"]);

/**
 * Only what was touched. An untouched field keeps the stored value rather than
 * being round-tripped through the form, a number retyped as the same amount is
 * not an edit, and a cleared field is left alone rather than sent empty.
 */
export function editsFrom(fields: FieldSpec[], original: FieldValues, values: FieldValues): ProfileEdits {
  const edits: ProfileEdits = {};
  const extra: Record<string, string> = {};
  for (const field of fields) {
    const next = (values[field.name] ?? "").trim();
    if (next === "") continue;
    const before = original[field.name] ?? "";
    const same = NUMERIC.has(field.type) ? Number(next) === Number(before) : next === before;
    if (!same) (field.extra ? extra : edits)[field.name] = next;
  }
  // A fund's own fields travel together, as the server stores them.
  if (Object.keys(extra).length) edits.extra_facts = extra;
  return edits;
}
