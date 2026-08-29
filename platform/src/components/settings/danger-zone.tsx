"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label, FieldError } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** OWNER-only: delete the workspace after typing its exact name. */
export function DangerZone({
  orgSlug,
  orgName,
}: {
  orgSlug: string;
  orgName: string;
}) {
  const router = useRouter();
  const [confirmName, setConfirmName] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const match = confirmName.trim() === orgName;

  async function destroy(e: React.FormEvent) {
    e.preventDefault();
    if (!match) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/orgs/${orgSlug}/settings`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmName: confirmName.trim() }),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Could not delete workspace");
      }
      router.push("/app");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete workspace");
      setBusy(false);
    }
  }

  return (
    <Card className="border-danger/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-danger">
          <TriangleAlert size={15} />
          Delete workspace
        </CardTitle>
        <CardDescription>
          Permanently deletes <span className="font-medium text-ink">{orgName}</span>{" "}
          with all its boards, feedback, votes, comments, surveys, and changelog
          entries. This cannot be undone.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={destroy} noValidate className="space-y-4">
          <div className="max-w-md">
            <Label htmlFor="confirm-name">
              Type <span className="font-semibold text-ink">{orgName}</span> to
              confirm
            </Label>
            <Input
              id="confirm-name"
              value={confirmName}
              onChange={(e) => setConfirmName(e.target.value)}
              placeholder={orgName}
              autoComplete="off"
            />
            <FieldError>{error}</FieldError>
          </div>
          <Button
            type="submit"
            variant="danger"
            disabled={!match}
            loading={busy}
          >
            Delete this workspace forever
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
