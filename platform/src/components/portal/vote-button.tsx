"use client";

import * as React from "react";
import { ChevronUp } from "lucide-react";
import { cn, compactNumber } from "@/lib/utils";

/**
 * Optimistic vote toggle used across the portal + widget. Works for both
 * signed-in users and anonymous guests (server sets the guest cookie).
 */
export function VoteButton({
  orgSlug,
  postId,
  voted: initialVoted,
  count: initialCount,
  layout = "stack",
  className,
  onChange,
}: {
  orgSlug: string;
  postId: string;
  voted: boolean;
  count: number;
  /** "stack" = vertical card button, "inline" = compact pill. */
  layout?: "stack" | "inline";
  className?: string;
  onChange?: (voted: boolean, count: number) => void;
}) {
  const [voted, setVoted] = React.useState(initialVoted);
  const [count, setCount] = React.useState(initialCount);
  const [busy, setBusy] = React.useState(false);

  // Keep in sync when the server re-renders with fresh data (router.refresh).
  React.useEffect(() => setVoted(initialVoted), [initialVoted]);
  React.useEffect(() => setCount(initialCount), [initialCount]);

  async function toggle(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    const nextVoted = !voted;
    const nextCount = Math.max(0, count + (nextVoted ? 1 : -1));
    setVoted(nextVoted);
    setCount(nextCount);
    try {
      const res = await fetch(
        `/api/p/${encodeURIComponent(orgSlug)}/posts/${encodeURIComponent(postId)}/vote`,
        { method: "POST" }
      );
      const json = await res.json();
      if (json?.ok) {
        setVoted(json.data.voted);
        setCount(json.data.voteCount);
        onChange?.(json.data.voted, json.data.voteCount);
      } else {
        setVoted(voted);
        setCount(count);
      }
    } catch {
      setVoted(voted);
      setCount(count);
    } finally {
      setBusy(false);
    }
  }

  const base =
    "select-none border transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-accent/50";
  const active = voted
    ? "border-accent/50 bg-accent/15 text-accent-soft"
    : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink";

  if (layout === "inline") {
    return (
      <button
        type="button"
        onClick={toggle}
        disabled={busy}
        aria-pressed={voted}
        aria-label={voted ? "Remove your vote" : "Vote for this idea"}
        className={cn(
          base,
          active,
          "inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-xs font-medium",
          className
        )}
      >
        <ChevronUp size={13} aria-hidden />
        {compactNumber(count)}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
      aria-pressed={voted}
      aria-label={voted ? "Remove your vote" : "Vote for this idea"}
      className={cn(
        base,
        active,
        "flex h-14 w-11 shrink-0 flex-col items-center justify-center gap-0.5 rounded-lg text-xs font-semibold",
        className
      )}
    >
      <ChevronUp size={15} aria-hidden />
      {compactNumber(count)}
    </button>
  );
}
