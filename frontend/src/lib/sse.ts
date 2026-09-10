/**
 * Server-sent events, parsed from a buffer that grows one network read at a time.
 *
 * Frames are separated by a blank line and can be split across reads. Parsing
 * each read on its own drops any event whose frame straddles a chunk boundary,
 * which for a token stream means losing words from the middle of an answer — so
 * an incomplete frame is handed back as `rest` for the next read to finish.
 */

export type SseEvent = { event: string; data: Record<string, unknown> };

export function parseFrame(frame: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;

  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

export function takeFrames(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  let rest = buffer;
  let split = rest.indexOf("\n\n");
  while (split !== -1) {
    const parsed = parseFrame(rest.slice(0, split));
    if (parsed) events.push(parsed);
    rest = rest.slice(split + 2);
    split = rest.indexOf("\n\n");
  }
  return { events, rest };
}
