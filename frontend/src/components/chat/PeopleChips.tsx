import { X } from "lucide-react";
import { chipLabel, type Person } from "@/lib/subject";

interface PeopleChipsProps {
  people: Person[];
  /** Refs the current turn is about; highlighted as soon as the server decides. */
  active: string[];
  onRemove: (ref: string) => void;
  disabled?: boolean;
}

/**
 * The employees this conversation is about (spec §4), in the header.
 *
 * Answers about one person need not repeat the name, so this is where the
 * employer sees who a question went to: the chips of the current turn's people
 * are filled, the rest outlined. A person's internal reference is only ever
 * handed back to remove them -- never shown.
 */
export function PeopleChips({ people, active, onRemove, disabled }: PeopleChipsProps) {
  if (!people.length) {
    return <span className="text-xs text-muted-foreground">Name an employee in your question</span>;
  }
  return (
    <div className="flex flex-wrap items-center justify-end gap-1" aria-label="Employees in this chat">
      {people.map((person) => {
        const on = active.includes(person.ref);
        return (
          <span
            key={person.ref}
            className={`flex items-center gap-1 rounded-full border py-0.5 pl-2.5 pr-1 text-xs ${
              on ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card text-foreground"
            }`}
            aria-current={on ? "true" : undefined}
          >
            <span className="max-w-[14rem] truncate">{chipLabel(person)}</span>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onRemove(person.ref)}
              aria-label={`Remove ${person.display_name ?? "this employee"}`}
              className={`rounded-full p-0.5 disabled:opacity-50 ${
                on ? "hover:bg-primary-foreground/20" : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              <X className="size-3" />
            </button>
          </span>
        );
      })}
    </div>
  );
}
