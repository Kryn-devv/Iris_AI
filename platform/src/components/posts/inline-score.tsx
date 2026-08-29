"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "./types";
import { cn } from "@/lib/utils";

/**
 * Inline impact/effort editor for the prioritization table (MEMBER+).
 * PATCHes the post and refreshes so the recomputed score renders.
 */
export function InlineScoreSelect({
  orgSlug,
  postId,
  field,
  value,
  disabled,
}: {
  orgSlug: string;
  postId: string;
  field: "impact" | "effort";
  value: number | null;
  disabled?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = React.useState(false);

  const onChange = async (raw: string) => {
    setBusy(true);
    try {
      await apiFetch(`/api/orgs/${orgSlug}/posts/${postId}`, {
        method: "PATCH",
        body: JSON.stringify({ [field]: raw ? Number(raw) : null }),
      });
      router.refresh();
    } catch {
      // revert happens naturally on refresh; nothing to do
    } finally {
      setBusy(false);
    }
  };

  if (disabled) {
    return (
      <span className="text-sm text-ink-muted">{value ?? "—"}</span>
    );
  }

  return (
    <select
      aria-label={`Set ${field}`}
      value={value != null ? String(value) : ""}
      disabled={busy}
      onChange={(e) => void onChange(e.target.value)}
      className={cn(
        "h-7 rounded-md border border-line bg-surface px-1.5 text-xs text-ink",
        "focus:border-accent/60 focus:outline-none focus:ring-1 focus:ring-accent/40",
        "disabled:opacity-50"
      )}
    >
      <option value="">—</option>
      {[1, 2, 3, 4, 5].map((n) => (
        <option key={n} value={n}>
          {n}
        </option>
      ))}
    </select>
  );
}
