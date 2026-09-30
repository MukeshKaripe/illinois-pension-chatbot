/** The conversation's people: found by name in the chat, chosen by the user (Phase 1). */

/** One person. `ref` is opaque: sent back to choose or remove, never shown. */
export interface Person {
  ref: string;
  display_name: string | null;
  first_name: string | null;
  last_name: string | null;
  department: string | null;
  employer: string | null;
  birth_year: number | null;
  status?: string | null;
}

export interface Choice {
  first_name: string | null;
  last_name: string | null;
  unavailable: boolean;
  candidates: Person[];
}

export interface Suggestion {
  kind: "choose" | "which_one";
  question: string;
  choices: Choice[];
}

/** More candidates than this and a choice starts with the department. */
export const MAX_LISTED = 12;
const NO_DEPARTMENT = "No department on file";

const joined = (parts: (string | null | undefined)[]) =>
  parts.filter((part): part is string => Boolean(part)).join(" · ");

export const chipLabel = (p: Person): string => joined([p.display_name, p.department ?? p.employer]);

export const candidateLabel = (p: Person): string =>
  joined([p.department, p.employer, p.birth_year ? `born ${p.birth_year}` : null]);

export function byDepartment(people: Person[]): { department: string; candidates: Person[] }[] {
  const groups = new Map<string, Person[]>();
  for (const person of people) {
    const key = person.department ?? NO_DEPARTMENT;
    groups.set(key, [...(groups.get(key) ?? []), person]);
  }
  return [...groups.entries()]
    .map(([department, candidates]) => ({ department, candidates }))
    .sort((a, b) =>
      b.candidates.length - a.candidates.length ||
      Number(a.department === NO_DEPARTMENT) - Number(b.department === NO_DEPARTMENT) ||
      a.department.localeCompare(b.department));
}

export function suggestionFrom(toolCalls: unknown): Suggestion | null {
  if (!Array.isArray(toolCalls)) return null;
  const hit = toolCalls.find(
    (t) => t && typeof t === "object" && (t as { type?: unknown }).type === "subject_suggestion",
  ) as (Suggestion & { type: string }) | undefined;
  if (!hit || !Array.isArray(hit.choices)) return null;
  return { kind: hit.kind, question: hit.question, choices: hit.choices };
}

/** Every card that has someone to choose has had a choice: time to re-send the question. */
export const allChosen = (s: Suggestion, chosen: Record<number, string>): boolean =>
  s.choices.every((c, i) => c.candidates.length === 0 || Boolean(chosen[i]));

/** A which-one choice is answered by asking again about that person, by name. */
export const whichOneQuestion = (question: string, person: Person): string =>
  `About ${person.display_name ?? "them"}: ${question}`;
