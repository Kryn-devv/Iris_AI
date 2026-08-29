"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { REACTION_EMOJIS, type ReactionEmoji } from "./types";

/**
 * Emoji reaction bar on a changelog entry. One reaction per emoji per
 * viewer (user or guest cookie) — clicking toggles, updates optimistically.
 */
export function ChangelogReactions({
  orgSlug,
  entryId,
  counts: initialCounts,
  mine: initialMine,
}: {
  orgSlug: string;
  entryId: string;
  counts: Partial<Record<ReactionEmoji, number>>;
  mine: ReactionEmoji[];
}) {
  const [counts, setCounts] = React.useState<Record<string, number>>(() => ({
    ...initialCounts,
  }));
  const [mine, setMine] = React.useState<Set<string>>(() => new Set(initialMine));
  const busy = React.useRef<Set<string>>(new Set());

  async function toggle(emoji: ReactionEmoji) {
    if (busy.current.has(emoji)) return;
    busy.current.add(emoji);
    const had = mine.has(emoji);
    setMine((prev) => {
      const next = new Set(prev);
      if (had) next.delete(emoji);
      else next.add(emoji);
      return next;
    });
    setCounts((prev) => ({
      ...prev,
      [emoji]: Math.max(0, (prev[emoji] ?? 0) + (had ? -1 : 1)),
    }));
    try {
      const res = await fetch(
        `/api/p/${encodeURIComponent(orgSlug)}/changelog/${encodeURIComponent(entryId)}/reactions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ emoji }),
        }
      );
      const json = await res.json();
      if (json?.ok) {
        setCounts((prev) => ({ ...prev, [emoji]: json.data.count }));
        setMine((prev) => {
          const next = new Set(prev);
          if (json.data.reacted) next.add(emoji);
          else next.delete(emoji);
          return next;
        });
      } else {
        // Revert on failure.
        setMine((prev) => {
          const next = new Set(prev);
          if (had) next.add(emoji);
          else next.delete(emoji);
          return next;
        });
        setCounts((prev) => ({
          ...prev,
          [emoji]: Math.max(0, (prev[emoji] ?? 0) + (had ? 1 : -1)),
        }));
      }
    } catch {
      setMine((prev) => {
        const next = new Set(prev);
        if (had) next.add(emoji);
        else next.delete(emoji);
        return next;
      });
      setCounts((prev) => ({
        ...prev,
        [emoji]: Math.max(0, (prev[emoji] ?? 0) + (had ? 1 : -1)),
      }));
    } finally {
      busy.current.delete(emoji);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="React to this update">
      {REACTION_EMOJIS.map((emoji) => {
        const count = counts[emoji] ?? 0;
        const active = mine.has(emoji);
        return (
          <button
            key={emoji}
            type="button"
            onClick={() => toggle(emoji)}
            aria-pressed={active}
            aria-label={`React with ${emoji}`}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-full border px-2.5 text-sm transition-colors",
              active
                ? "border-accent/50 bg-accent/15"
                : "border-line bg-surface hover:border-line-strong"
            )}
          >
            <span aria-hidden>{emoji}</span>
            {count > 0 && (
              <span className={cn("text-xs font-medium", active ? "text-accent-soft" : "text-ink-faint")}>
                {count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
