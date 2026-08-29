"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input, Textarea, Label, FieldError } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export type GeneralSettings = {
  name: string;
  portalEnabled: boolean;
  portalHeadline: string | null;
  portalIntro: string | null;
  brandColor: string | null;
};

const DEFAULT_COLOR = "#8b8bf5";

/** Workspace general settings form (ADMIN+). */
export function GeneralSettingsForm({
  orgSlug,
  initial,
}: {
  orgSlug: string;
  initial: GeneralSettings;
}) {
  const router = useRouter();
  const [name, setName] = React.useState(initial.name);
  const [portalEnabled, setPortalEnabled] = React.useState(initial.portalEnabled);
  const [portalHeadline, setPortalHeadline] = React.useState(
    initial.portalHeadline ?? ""
  );
  const [portalIntro, setPortalIntro] = React.useState(initial.portalIntro ?? "");
  const [brandColor, setBrandColor] = React.useState(
    initial.brandColor ?? DEFAULT_COLOR
  );
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const res = await fetch(`/api/orgs/${orgSlug}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          portalEnabled,
          portalHeadline,
          portalIntro,
          brandColor,
        }),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Could not save settings");
      }
      setSaved(true);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save settings");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate>
      <Card>
        <CardHeader>
          <CardTitle>Workspace</CardTitle>
          <CardDescription>
            Name and public portal appearance for this workspace.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="max-w-md">
            <Label htmlFor="org-name">Workspace name</Label>
            <Input
              id="org-name"
              required
              maxLength={60}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="flex items-start gap-3 rounded-lg border border-line bg-surface p-3">
            <input
              id="portal-enabled"
              type="checkbox"
              checked={portalEnabled}
              onChange={(e) => setPortalEnabled(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-current text-accent"
            />
            <div>
              <label
                htmlFor="portal-enabled"
                className="block text-sm font-medium text-ink"
              >
                Public portal enabled
              </label>
              <p className="text-xs text-ink-muted">
                When off, the public feedback portal at /p/{orgSlug} is hidden.
              </p>
            </div>
          </div>

          <div className="max-w-md">
            <Label htmlFor="portal-headline">Portal headline</Label>
            <Input
              id="portal-headline"
              maxLength={120}
              value={portalHeadline}
              onChange={(e) => setPortalHeadline(e.target.value)}
              placeholder="Help us build a better product"
            />
          </div>

          <div className="max-w-md">
            <Label htmlFor="portal-intro">Portal intro</Label>
            <Textarea
              id="portal-intro"
              maxLength={600}
              value={portalIntro}
              onChange={(e) => setPortalIntro(e.target.value)}
              placeholder="Tell visitors what kind of feedback you're looking for."
            />
          </div>

          <div>
            <Label htmlFor="brand-color">Brand color</Label>
            <div className="flex items-center gap-3">
              <input
                id="brand-color"
                type="color"
                value={brandColor}
                onChange={(e) => setBrandColor(e.target.value)}
                aria-label="Brand color"
                className="h-9 w-12 cursor-pointer rounded-lg border border-line bg-surface p-1"
              />
              <Input
                aria-label="Brand color hex value"
                value={brandColor}
                onChange={(e) => setBrandColor(e.target.value)}
                pattern="^#[0-9a-fA-F]{6}$"
                className="w-32 font-mono"
              />
            </div>
          </div>

          <div className="flex items-center gap-3 pt-1">
            <Button type="submit" loading={busy}>
              Save changes
            </Button>
            {saved && !busy && (
              <span className="text-xs text-success">Saved.</span>
            )}
          </div>
          <FieldError>{error}</FieldError>
        </CardContent>
      </Card>
    </form>
  );
}
