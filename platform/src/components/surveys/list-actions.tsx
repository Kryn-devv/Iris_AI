"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Copy, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FieldError } from "@/components/ui/input";

/** Row actions on the surveys list: duplicate (MEMBER+) and delete (ADMIN+). */
export function SurveyListActions({
  orgSlug,
  surveyId,
  surveyName,
  responseCount,
  canDelete,
}: {
  orgSlug: string;
  surveyId: string;
  surveyName: string;
  responseCount: number;
  canDelete: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = React.useState<"duplicate" | "delete" | null>(null);
  const [confirming, setConfirming] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const duplicate = async () => {
    setBusy("duplicate");
    setError(null);
    try {
      const res = await fetch(
        `/api/orgs/${orgSlug}/surveys/${surveyId}/duplicate`,
        { method: "POST" }
      );
      const json = await res.json();
      if (!json.ok) {
        setError(json.error?.message ?? "Could not duplicate");
        return;
      }
      router.push(`/app/${orgSlug}/surveys/${json.data.id}`);
      router.refresh();
    } catch {
      setError("Network error");
    } finally {
      setBusy(null);
    }
  };

  const destroy = async () => {
    setBusy("delete");
    setError(null);
    try {
      const res = await fetch(`/api/orgs/${orgSlug}/surveys/${surveyId}`, {
        method: "DELETE",
      });
      const json = await res.json();
      if (!json.ok) {
        setError(json.error?.message ?? "Could not delete");
        return;
      }
      setConfirming(false);
      router.refresh();
    } catch {
      setError("Network error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        aria-label={`Duplicate ${surveyName}`}
        title="Duplicate"
        disabled={busy !== null}
        onClick={duplicate}
        className="rounded-md p-1.5 text-ink-faint transition-colors hover:bg-surface-overlay hover:text-ink disabled:opacity-40"
      >
        <Copy size={14} aria-hidden />
      </button>
      {canDelete && (
        <button
          type="button"
          aria-label={`Delete ${surveyName}`}
          title="Delete"
          disabled={busy !== null}
          onClick={() => setConfirming(true)}
          className="rounded-md p-1.5 text-ink-faint transition-colors hover:bg-surface-overlay hover:text-danger disabled:opacity-40"
        >
          <Trash2 size={14} aria-hidden />
        </button>
      )}
      {error && !confirming && (
        <span className="text-[11px] text-danger">{error}</span>
      )}
      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={`Delete "${surveyName}"?`}
        description={
          responseCount > 0
            ? `This permanently deletes the survey and its ${responseCount} response${responseCount === 1 ? "" : "s"}. This cannot be undone.`
            : "This permanently deletes the survey. This cannot be undone."
        }
      >
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            loading={busy === "delete"}
            onClick={destroy}
          >
            Delete survey
          </Button>
        </div>
        <FieldError>{error}</FieldError>
      </Dialog>
    </div>
  );
}
