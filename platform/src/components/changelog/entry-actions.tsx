"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Pencil, Trash2, Upload, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";

/**
 * Row / page actions for a changelog entry: edit link, publish/unpublish
 * (MEMBER+ — only rendered for them) and delete (ADMIN+ via canDelete).
 */
export function EntryActions({
  orgSlug,
  entryId,
  entryTitle,
  published,
  canDelete,
  showEditLink = true,
}: {
  orgSlug: string;
  entryId: string;
  entryTitle: string;
  published: boolean;
  canDelete: boolean;
  showEditLink?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = React.useState<"publish" | "delete" | null>(null);
  const [confirming, setConfirming] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const call = async (
    kind: "publish" | "delete",
    input: RequestInfo,
    init: RequestInit
  ) => {
    setBusy(kind);
    setError(null);
    try {
      const res = await fetch(input, init);
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Action failed");
      }
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
      return false;
    } finally {
      setBusy(null);
    }
  };

  const togglePublish = async () => {
    const done = await call(
      "publish",
      `/api/orgs/${orgSlug}/changelog/${entryId}/publish`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ published: !published }),
      }
    );
    if (done) router.refresh();
  };

  const remove = async () => {
    const done = await call(
      "delete",
      `/api/orgs/${orgSlug}/changelog/${entryId}`,
      { method: "DELETE" }
    );
    setConfirming(false);
    if (done) {
      router.push(`/app/${orgSlug}/changelog`);
      router.refresh();
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      {error && (
        <span role="alert" className="text-[11px] text-danger">
          {error}
        </span>
      )}
      {showEditLink && (
        <Link
          href={`/app/${orgSlug}/changelog/${entryId}`}
          aria-label={`Edit ${entryTitle}`}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-ink-muted hover:bg-surface-overlay hover:text-ink"
        >
          <Pencil size={13} aria-hidden />
          Edit
        </Link>
      )}
      <Button
        variant="secondary"
        size="sm"
        loading={busy === "publish"}
        onClick={togglePublish}
      >
        {published ? (
          <>
            <Undo2 size={13} aria-hidden />
            Unpublish
          </>
        ) : (
          <>
            <Upload size={13} aria-hidden />
            Publish
          </>
        )}
      </Button>
      {canDelete && (
        <>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Delete ${entryTitle}`}
            className="text-danger hover:bg-danger/10 hover:text-danger"
            onClick={() => setConfirming(true)}
          >
            <Trash2 size={13} aria-hidden />
          </Button>
          <Dialog
            open={confirming}
            onClose={() => setConfirming(false)}
            title="Delete changelog entry"
            description={`“${entryTitle}” and all of its reactions and comments will be permanently removed.`}
          >
            <div className="flex justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setConfirming(false)}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                size="sm"
                loading={busy === "delete"}
                onClick={remove}
              >
                Delete entry
              </Button>
            </div>
          </Dialog>
        </>
      )}
    </div>
  );
}
