"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { FieldError } from "@/components/ui/input";

/** Accept button on /invite/[token] for a signed-in user. */
export function AcceptInviteButton({
  orgSlug,
  token,
}: {
  orgSlug: string;
  token: string;
}) {
  const router = useRouter();
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/orgs/${orgSlug}/invites/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Could not accept the invite");
      }
      router.push(`/app/${json.data.orgSlug as string}/dashboard`);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not accept the invite");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <Button onClick={accept} loading={busy} className="w-full">
        Accept invite
      </Button>
      <FieldError>{error}</FieldError>
    </div>
  );
}
