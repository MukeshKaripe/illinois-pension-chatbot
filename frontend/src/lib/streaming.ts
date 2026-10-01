/**
 * Which part of a streaming answer to render as markdown.
 *
 * Markdown is re-parsed on every token. A paragraph renders correctly as it
 * grows, so it is never held back -- holding back everything after the last
 * blank line left bold, lists and links as raw text for a second or two.
 * Only a table still being written is shown as plain text, because a
 * half-written table parses as literal pipes until its rows close.
 */
export function splitStreaming(content: string, isStreaming: boolean): { settled: string; tail: string } {
  if (!isStreaming) return { settled: content, tail: "" };
  const split = content.lastIndexOf("\n\n");
  const block = split >= 0 ? content.slice(split + 2) : content;
  if (!block.trimStart().startsWith("|")) return { settled: content, tail: "" };
  return split >= 0
    ? { settled: content.slice(0, split), tail: content.slice(split) }
    : { settled: "", tail: content };
}

const isRow = (line: string | undefined) => line !== undefined && line.trim().startsWith("|");
const isSeparator = (line: string | undefined) =>
  line !== undefined && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line);

/**
 * Drop a table header that has no rows of its own because another table
 * starts straight after it.
 *
 * The comparison table is rendered by code and put in place of a marker, and
 * the model sometimes writes its own header line and separator just before
 * the marker. Markdown then reads the two as one table: the code's header
 * becomes a row, its separator another, and the employees' rows lose columns.
 */
export function dropOrphanTableHeaders(markdown: string): string {
  const lines = markdown.split("\n");
  const kept: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const orphan =
      isRow(lines[i]) && isSeparator(lines[i + 1]) && isRow(lines[i + 2]) && isSeparator(lines[i + 3]) &&
      (i === 0 || !isRow(lines[i - 1]));
    if (orphan) {
      i += 1;
      continue;
    }
    kept.push(lines[i]);
  }
  return kept.join("\n");
}
