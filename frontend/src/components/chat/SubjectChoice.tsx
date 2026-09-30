"use client";

import { useState } from "react";
import { byDepartment, candidateLabel, MAX_LISTED, type Choice, type Person } from "@/lib/subject";

interface SubjectChoiceProps {
  choice: Choice;
  onChoose: (person: Person) => void;
  /** The ref chosen from this card, once one has been. */
  chosenRef?: string;
  disabled?: boolean;
}

/**
 * The people one name matched, to choose from (spec §4.2). Nobody is chosen for
 * the user: a name is shared by up to 109 active members. More than MAX_LISTED
 * and the department comes first. The internal reference is never rendered.
 */
export function SubjectChoice({ choice, onChoose, chosenRef, disabled }: SubjectChoiceProps) {
  const [department, setDepartment] = useState<string | null>(null);
  // Nothing to choose: the message itself says why (not found, or not answering).
  if (choice.unavailable || choice.candidates.length === 0) return null;

  const groups = byDepartment(choice.candidates);
  const pickDepartmentFirst = choice.candidates.length > MAX_LISTED && department === null;
  const shown = department === null ? choice.candidates : groups.find((g) => g.department === department)?.candidates ?? [];
  const locked = disabled || Boolean(chosenRef);

  return (
    <div className="mt-3 rounded-lg border border-border bg-card p-2 text-sm">
      {pickDepartmentFirst ? (
        <div className="flex flex-wrap gap-1.5">
          {groups.map((g) => (
            <button
              key={g.department}
              type="button"
              disabled={locked}
              onClick={() => setDepartment(g.department)}
              className="rounded-md border border-border px-2.5 py-1 text-xs hover:bg-muted disabled:opacity-50"
            >
              {g.department} <span className="tabular-nums text-muted-foreground">({g.candidates.length})</span>
            </button>
          ))}
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {shown.map((person) => {
            const chosen = chosenRef === person.ref;
            return (
              <li key={person.ref} className="flex items-center justify-between gap-3 px-1 py-1.5">
                <span className="min-w-0">
                  <span className="font-medium">{person.display_name}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{candidateLabel(person)}</span>
                </span>
                {chosen ? (
                  <span className="shrink-0 text-xs font-medium text-primary">Chosen</span>
                ) : (
                  <button
                    type="button"
                    disabled={locked}
                    onClick={() => onChoose(person)}
                    className="shrink-0 rounded-md border border-primary px-2.5 py-0.5 text-xs font-medium text-primary hover:bg-primary hover:text-primary-foreground disabled:opacity-50"
                  >
                    {choice.candidates.length === 1 ? "Use this employee" : "Use"}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {department !== null && choice.candidates.length > MAX_LISTED && !chosenRef && (
        <button
          type="button"
          onClick={() => setDepartment(null)}
          className="mt-1 px-1 text-xs text-primary hover:underline"
        >
          Back to departments
        </button>
      )}
    </div>
  );
}
