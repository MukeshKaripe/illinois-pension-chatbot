import { X } from "lucide-react";
import { formatMoney } from "@/lib/card";
import type { Scenario } from "@/lib/types";

interface ScenarioChipsProps {
  scenarios: Scenario[];
  /** Forget one scenario by label, or all of them when called without one. */
  onRemove: (label?: string) => void;
  /** While an answer streams, the turn may be writing scenarios itself. */
  disabled: boolean;
}

/**
 * The what-ifs this conversation is holding (spec §7), above the composer.
 *
 * The assistant compares against these on later turns ("put them all in one
 * table"), so they are shown rather than remembered silently, and each can be
 * dropped when the member moves on.
 */
export function ScenarioChips({ scenarios, onRemove, disabled }: ScenarioChipsProps) {
  if (!scenarios.length) return null;
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2" aria-label="Scenarios in this chat">
      <span className="text-xs text-muted-foreground">Scenarios</span>
      {scenarios.map((scenario) => (
        <span
          key={scenario.label}
          className="flex items-center gap-1 rounded-full border border-border bg-card py-0.5 pl-2.5 pr-1 text-xs"
        >
          <span>{scenario.label}</span>
          {scenario.annual_amount && (
            <span className="tabular-nums text-muted-foreground">
              · {formatMoney(scenario.annual_amount)}/yr
            </span>
          )}
          <button
            type="button"
            disabled={disabled}
            onClick={() => onRemove(scenario.label)}
            aria-label={`Forget the scenario ${scenario.label}`}
            className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      {scenarios.length > 1 && (
        <button
          type="button"
          disabled={disabled}
          onClick={() => onRemove()}
          className="text-xs text-primary hover:underline disabled:opacity-50"
        >
          Clear all
        </button>
      )}
    </div>
  );
}
