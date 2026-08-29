"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input, Label, FieldError } from "@/components/ui/input";

/** "Name your workspace" — creates the org and jumps into its dashboard. */
export function OnboardingForm() {
  const router = useRouter();
  const [name, setName] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/orgs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Could not create workspace");
      }
      router.push(`/app/${json.data.org.slug as string}/dashboard`);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create workspace");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="ws-name">Workspace name</Label>
        <Input
          id="ws-name"
          required
          maxLength={60}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Acme Inc."
          autoFocus
        />
        <FieldError>{error}</FieldError>
      </div>
      <Button type="submit" loading={busy} className="w-full">
        Create workspace
      </Button>
    </form>
  );
}
