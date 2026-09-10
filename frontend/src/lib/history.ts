/** Sidebar history, grouped the way chat products do: Today, Yesterday, Earlier. */

export type HistoryLabel = "Today" | "Yesterday" | "Earlier";
export type HistoryGroup<T> = { label: HistoryLabel; items: T[] };

const ORDER: readonly HistoryLabel[] = ["Today", "Yesterday", "Earlier"];

const startOfDay = (d: Date) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

export function groupSessions<T extends { last_active_at: string }>(
  sessions: readonly T[],
  now: Date,
): HistoryGroup<T>[] {
  const today = startOfDay(now);
  const yesterday = startOfDay(
    new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1),
  );

  const buckets: Record<HistoryLabel, T[]> = { Today: [], Yesterday: [], Earlier: [] };
  const newestFirst = [...sessions].sort(
    (a, b) => Date.parse(b.last_active_at) - Date.parse(a.last_active_at),
  );
  for (const session of newestFirst) {
    const t = Date.parse(session.last_active_at);
    buckets[t >= today ? "Today" : t >= yesterday ? "Yesterday" : "Earlier"].push(session);
  }

  return ORDER.filter((label) => buckets[label].length > 0).map((label) => ({
    label,
    items: buckets[label],
  }));
}
