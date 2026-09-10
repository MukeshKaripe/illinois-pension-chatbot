/**
 * What the agent is doing while the member waits.
 *
 * The server sends lowercase stage labels ("searching the Pension Code") from
 * `agent/graph.py::STAGE_LABELS`, and a preamble sentence before any of them.
 * Both are shown as one calm line — capitalised, ending in an ellipsis.
 */
export function StatusLine({ text }: { text: string }) {
  if (!text) return null;
  const trimmed = text.trim().replace(/[.…]+$/, "");
  const label = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return (
    <p role="status" aria-live="polite" className="shimmer text-sm">
      {label}…
    </p>
  );
}
