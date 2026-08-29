"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Bell, BellRing } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * One-shot "notify members" action for a published entry.
 * Disabled once notifiedAt is set; shows the notified count afterwards.
 */
export function NotifyButton({
  orgSlug,
  entryId,
  published,
  notifiedAt,
  memberCount,
}: {
  orgSlug: string;
  entryId: string;
  published: boolean;
  notifiedAt: string | null;
  memberCount: number;
}) {
  const router = useRouter();
  const [busy, setBusy] = React.useState(false);
  const [justNotified, setJustNotified] = React.useState<number | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  if (!published) return null;

  const done = Boolean(notifiedAt) || justNotified !== null;
  const count = justNotified ?? memberCount;

  const notify = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/orgs/${orgSlug}/changelog/${entryId}/notify`,
        { method: "POST" }
      );
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Could not notify members");
      }
      setJustNotified(json.data.notified as number);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not notify members");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="secondary"
        size="sm"
        disabled={done}
        loading={busy}
        onClick={notify}
      >
        {done ? (
          <>
            <BellRing size={13} aria-hidden className="text-success" />
            Notified {count} member{count === 1 ? "" : "s"}
          </>
        ) : (
          <>
            <Bell size={13} aria-hidden />
            Notify users
          </>
        )}
      </Button>
      {error && (
        <span role="alert" className="text-[11px] text-danger">
          {error}
        </span>
      )}
    </div>
  );
}
