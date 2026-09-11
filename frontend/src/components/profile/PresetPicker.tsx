"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import type { Persona } from "@/lib/api";
import { groupPresets } from "@/lib/funds";
import { SupportBadge } from "./SupportBadge";

interface PresetPickerProps {
  personas: Persona[];
  chosen: string | null;
  onChoose: (persona: Persona) => void;
  /** Two preset columns on the sign-up page; one in the narrow drawer. */
  columns?: 1 | 2;
}

/**
 * "Who do you work for?" — search, then employer groups, then each fund's
 * presets (spec §6). Members know their employer, not which Article covers
 * them; that gap is the whole reason `agent/coverage.py` exists.
 */
export function PresetPicker({ personas, chosen, onChoose, columns = 2 }: PresetPickerProps) {
  const [query, setQuery] = useState("");
  const groups = groupPresets(personas, query);

  return (
    <div className="space-y-6">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search employers: teacher, police, county…"
          aria-label="Search employers"
          className="w-full rounded-lg border border-input bg-background py-2.5 pl-9 pr-3.5 text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
        />
      </div>

      {personas.length > 0 && groups.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No employer matches “{query}”. Try the kind of work, like “teacher” or “police”.
        </p>
      )}

      {groups.map(({ group, funds }) => (
        <section key={group} className="space-y-4">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{group}</h3>
          {funds.map((fund) => (
            <div key={fund.article} className="space-y-2">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-sm font-medium">{fund.member_label}</span>
                <span className="text-xs tabular-nums text-muted-foreground">Article {fund.article}</span>
                <SupportBadge level={fund.support_level} />
              </div>
              <div className={`grid gap-2 ${columns === 2 ? "sm:grid-cols-2" : ""}`}>
                {fund.presets.map((option) => (
                  <button
                    key={option.key}
                    type="button"
                    onClick={() => onChoose(option)}
                    aria-pressed={chosen === option.key}
                    className={`rounded-lg border p-3 text-left ${
                      chosen === option.key
                        ? "border-primary bg-primary/5"
                        : "border-border bg-card hover:border-ring"
                    }`}
                  >
                    <span className="block text-sm font-medium">{option.label}</span>
                    <span className="mt-0.5 block text-xs tabular-nums text-muted-foreground">
                      Age {option.age} · {Number(option.total_service_years)} years · {option.employment_status}
                    </span>
                    <span className="mt-1.5 block text-sm leading-relaxed text-muted-foreground">
                      {option.summary}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
