"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { visibleSteps, type ProgressStep } from "@/lib/progress";

/**
 * What is happening, and what has already been worked out, while the answer is
 * still being written.
 *
 * Two things sit here and both are earned rather than decorative. The steps
 * appear only once the member has been made to wait — `visibleSteps` decides,
 * and says why. The tables appear because they were finished before the prose
 * began: a figure the engine computed is ready seconds before the sentence
 * explaining it, and there is no reason to make somebody wait for the sentence
 * to see the number.
 *
 * Both disappear the moment the answer starts arriving, because the answer
 * carries the tables itself.
 */
export function WorkingNote({
  status,
  steps,
  views,
}: {
  status: string;
  steps: ProgressStep[];
  views: { slot: string; markdown: string }[];
}) {
  const shown = visibleSteps(steps);
  if (!status && shown.length === 0 && views.length === 0) return null;

  return (
    <div className="space-y-3">
      {shown.length > 0 ? (
        <ol role="status" aria-live="polite" className="space-y-1 text-sm text-muted-foreground">
          {shown.map((step, index) => (
            <li
              key={`${step.text}-${index}`}
              // The last one is what is happening now; the rest are done.
              className={index === shown.length - 1 ? "shimmer" : undefined}
            >
              {step.text}
            </li>
          ))}
        </ol>
      ) : (
        status && (
          <p role="status" aria-live="polite" className="shimmer text-sm">
            {status.trim().replace(/[.…]+$/, "")}…
          </p>
        )
      )}

      {views.map((view) => (
        <div key={view.slot} className="prose answer max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{view.markdown}</ReactMarkdown>
        </div>
      ))}
    </div>
  );
}
