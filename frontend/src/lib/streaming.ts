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
