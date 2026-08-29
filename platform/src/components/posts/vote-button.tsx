"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { apiFetch } from "./types";

/** Toggleable vote button — votes as the current team member. */
export function VoteButton({
  orgSlug,
  postId,
  count,
  voted,
  disabled,
}: {
  orgSlug: string;
  postId: string;
  count: number;
  voted: boolean;
  disabled?: boolean;
}) {
  const router = useRouter();
  const [optimistic, setOptimistic] = React.useState<{
    count: number;
    voted: boolean;
  }>({ count, voted });
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => setOptimistic({ count, voted }), [count, voted]);

  const toggle = async () => {
    if (busy || disabled) return;
    setBusy(true);
    setOptimistic((prev) => ({
      voted: !prev.voted,
      count: prev.count + (prev.voted ? -1 : 1),
    }));
    try {
      const data = await apiFetch<{ voted: boolean; voteCount: number }>(
        `/api/orgs/${orgSlug}/posts/${postId}/vote`,
        { method: "POST" }
      );
      setOptimistic({ voted: data.voted, count: data.voteCount });
      router.refresh();
    } catch {
      setOptimistic({ count, voted });
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={() => void toggle()}
      disabled={disabled || busy}
      aria-pressed={optimistic.voted}
      aria-label={optimistic.voted ? "Remove your vote" : "Vote for this post"}
      className={cn(
        "flex w-16 flex-col items-center rounded-xl border py-2.5 transition-colors disabled:opacity-60",
        optimistic.voted
          ? "border-accent/60 bg-accent/15 text-accent-soft shadow-glow"
          : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink"
      )}
    >
      <ChevronUp size={16} aria-hidden />
      <span className="text-base font-semibold">{optimistic.count}</span>
      <span className="text-[10px] uppercase tracking-wide">
        {optimistic.voted ? "Voted" : "Vote"}
      </span>
    </button>
  );
}
