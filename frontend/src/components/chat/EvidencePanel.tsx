"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/api";

type Section = { section: string; title: string; article: string; citation: string; text: string };

/**
 * The text of one section, set as the statute rather than as the assistant.
 *
 * A plain fetch with no credential headers: statute text is public, and the
 * endpoint reads no member data. Keyed by section in the parent, so opening a
 * different section starts from a clean loading state.
 */
export function EvidencePanel({ section }: { section: string }) {
  const [data, setData] = useState<Section | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stale = false;
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/sections/${encodeURIComponent(section)}`);
        if (!res.ok) {
          throw new Error(
            res.status === 404 ? "This section is not in the index." : "Could not load this section.",
          );
        }
        const json: Section = await res.json();
        if (!stale) setData(json);
      } catch (err) {
        if (!stale) setError(err instanceof Error ? err.message : "Could not load this section.");
      }
    };
    load();
    return () => {
      stale = true;
    };
  }, [section]);

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs font-medium tabular-nums text-muted-foreground">40 ILCS 5/{section}</p>
        <h3 className="mt-1 text-lg font-semibold">{data?.title || `Section ${section}`}</h3>
        {data?.article && <p className="text-sm text-muted-foreground">Article {data.article}</p>}
      </div>

      {error ? (
        <p role="alert" className="text-sm text-destructive">{error}</p>
      ) : !data ? (
        <p className="shimmer text-sm">Loading the statute text…</p>
      ) : (
        <div className="space-y-3 border-l-2 border-primary pl-4 font-serif text-[15.5px] leading-relaxed">
          {data.text.split("\n\n").map((paragraph, index) => (
            <p key={index}>{paragraph}</p>
          ))}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        The Illinois Pension Code as indexed by this assistant. Check the official statute before
        relying on it.
      </p>
    </div>
  );
}
