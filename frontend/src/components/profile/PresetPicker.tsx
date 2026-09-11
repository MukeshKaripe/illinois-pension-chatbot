import type { Persona } from "@/lib/api";

interface PresetPickerProps {
  personas: Persona[];
  chosen: string | null;
  onChoose: (persona: Persona) => void;
}

/**
 * Presets grouped by fund — who the member works for — with the Article after
 * it. Members know their employer, not which Article covers them; that gap is
 * the whole reason `agent/coverage.py` exists.
 */
export function PresetPicker({ personas, chosen, onChoose }: PresetPickerProps) {
  const groups = new Map<string, Persona[]>();
  for (const option of personas) {
    const heading = `${option.fund} · Article ${option.article_number}`;
    groups.set(heading, [...(groups.get(heading) ?? []), option]);
  }

  return (
    <div className="space-y-6">
      {[...groups.entries()].map(([heading, options]) => (
        <div key={heading} className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {heading}
          </h3>
          <div className="grid gap-3">
            {options.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => onChoose(option)}
                aria-pressed={chosen === option.key}
                className={`rounded-xl border p-4 text-left ${
                  chosen === option.key
                    ? "border-primary bg-primary/5"
                    : "border-border bg-card hover:border-ring"
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium">{option.label}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    Tier {option.tier} · age {option.age}
                  </span>
                </div>
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{option.summary}</p>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
