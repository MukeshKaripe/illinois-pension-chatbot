import { ArrowLeftRight, BadgeCheck, Calculator, Scale } from "lucide-react";
import type { Profile } from "@/lib/api";
import { ANONYMOUS_STARTERS, startersFor, type StarterKind } from "@/lib/starters";

const ICONS: Record<StarterKind, typeof Scale> = {
  law: Scale,
  eligibility: BadgeCheck,
  estimate: Calculator,
  compare: ArrowLeftRight,
};

interface EmptyStateProps {
  signedIn: boolean;
  onPick: (prompt: string) => void;
  /** True while the conversation is still being created. */
  disabled: boolean;
  /** The account's display name, when there is one. */
  name?: string | null;
  /** The profile a new chat uses, when signed in. */
  profile?: Profile | null;
}

export function EmptyState({ signedIn, onPick, disabled, name, profile }: EmptyStateProps) {
  const starters = profile ? startersFor(profile) : ANONYMOUS_STARTERS;

  return (
    <div className="mx-auto flex min-h-full w-full max-w-[720px] flex-col justify-center px-4 py-12">
      <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
        Illinois Pension Code · 40 ILCS 5
      </p>
      <h2 className="mt-3 text-3xl font-semibold tracking-tight">
        Hello{name ? `, ${name}` : ""}
      </h2>
      {profile ? (
        <p className="mt-2 text-muted-foreground">
          {profile.member_label} · {profile.label} · {profile.age}, {Number(profile.total_service_years)} years
          of service
        </p>
      ) : (
        <p className="mt-2 text-muted-foreground">
          Ask about your pension under the Illinois Pension Code — what a Section says, who it
          covers, or roughly what a pension would come to.
        </p>
      )}

      <div className="mt-8 grid gap-3 sm:grid-cols-2">
        {starters.map(({ kind, label, prompt }) => {
          const Icon = ICONS[kind];
          return (
            <button
              key={prompt}
              type="button"
              disabled={disabled}
              onClick={() => onPick(prompt)}
              className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4 text-left hover:border-primary/40 hover:bg-muted/60 disabled:opacity-50"
            >
              <span className="flex items-center gap-2 text-xs font-medium text-primary">
                <Icon className="size-4" />
                {label}
              </span>
              <span className="text-sm">{prompt}</span>
            </button>
          );
        })}
      </div>

      {!signedIn && (
        <p className="mt-6 text-sm text-muted-foreground">This is a temporary conversation.</p>
      )}
    </div>
  );
}
