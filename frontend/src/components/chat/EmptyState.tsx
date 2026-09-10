import { ANONYMOUS_STARTERS } from "@/lib/starters";

interface EmptyStateProps {
  signedIn: boolean;
  onPick: (prompt: string) => void;
  /** True while the conversation is still being created. */
  disabled: boolean;
}

export function EmptyState({ signedIn, onPick, disabled }: EmptyStateProps) {
  return (
    <div className="mx-auto flex min-h-full w-full max-w-[720px] flex-col justify-center px-4 py-12">
      <h2 className="font-serif text-4xl tracking-tight">Hello</h2>
      <p className="mt-2 text-muted-foreground">
        Ask about your pension under the Illinois Pension Code — what a Section says, who it
        covers, or roughly what a pension would come to.
      </p>

      <div className="mt-8 grid gap-2 sm:grid-cols-2">
        {ANONYMOUS_STARTERS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            disabled={disabled}
            onClick={() => onPick(prompt)}
            className="rounded-lg border border-border bg-card px-4 py-3 text-left text-sm hover:bg-muted disabled:opacity-50"
          >
            {prompt}
          </button>
        ))}
      </div>

      {!signedIn && (
        <p className="mt-6 text-sm text-muted-foreground">This is a temporary conversation.</p>
      )}
    </div>
  );
}
