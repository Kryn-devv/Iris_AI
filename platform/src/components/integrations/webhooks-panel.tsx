"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input, Label, FieldError } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/misc";
import { CopyButton } from "./copy-button";

export type WebhookRow = {
  id: string;
  url: string;
  events: string[];
  active: boolean;
};

const ALL_EVENTS = [
  "post.created",
  "post.status_changed",
  "vote.added",
  "changelog.published",
  "survey.response",
];

export function WebhooksPanel({
  orgSlug,
  webhooks,
  canManage,
}: {
  orgSlug: string;
  webhooks: WebhookRow[];
  canManage: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [url, setUrl] = React.useState("");
  const [events, setEvents] = React.useState<string[]>(["post.created"]);
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [newSecret, setNewSecret] = React.useState<string | null>(null);
  const [testResult, setTestResult] = React.useState<Record<string, string>>({});

  async function create() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/orgs/${orgSlug}/webhooks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, events }),
      });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error?.message ?? "Failed to create webhook");
      setNewSecret(json.data.secret);
      setUrl("");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(false);
    }
  }

  async function toggle(id: string, active: boolean) {
    await fetch(`/api/orgs/${orgSlug}/webhooks/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    });
    router.refresh();
  }

  async function remove(id: string) {
    if (!confirm("Delete this webhook?")) return;
    await fetch(`/api/orgs/${orgSlug}/webhooks/${id}`, { method: "DELETE" });
    router.refresh();
  }

  async function test(id: string) {
    setTestResult((s) => ({ ...s, [id]: "…" }));
    const res = await fetch(`/api/orgs/${orgSlug}/webhooks/${id}/test`, { method: "POST" });
    const json = await res.json();
    setTestResult((s) => ({
      ...s,
      [id]: json.ok && json.data.delivered ? `✓ ${json.data.status}` : `✗ ${json.data?.status ?? "failed"}`,
    }));
  }

  return (
    <div>
      {webhooks.length === 0 ? (
        <EmptyState
          title="No webhooks yet"
          description="Get signed HTTP callbacks when feedback arrives, statuses change, or releases publish."
          action={
            canManage ? (
              <Button size="sm" onClick={() => setOpen(true)}>
                <Plus size={13} /> Add webhook
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <ul className="divide-y divide-line/60">
            {webhooks.map((w) => (
              <li key={w.id} className="flex flex-wrap items-center gap-2 py-2.5">
                <code className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{w.url}</code>
                <span className="flex flex-wrap gap-1">
                  {w.events.map((e) => (
                    <Badge key={e}>{e}</Badge>
                  ))}
                </span>
                {canManage && (
                  <span className="flex items-center gap-1.5">
                    <button
                      onClick={() => toggle(w.id, !w.active)}
                      className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                        w.active ? "bg-success/15 text-success" : "bg-line/50 text-ink-faint"
                      }`}
                    >
                      {w.active ? "Active" : "Paused"}
                    </button>
                    <button
                      onClick={() => test(w.id)}
                      title="Send test event"
                      className="text-ink-faint hover:text-aurora"
                    >
                      <Send size={13} />
                    </button>
                    {testResult[w.id] && (
                      <span className="font-mono text-[10px] text-ink-muted">{testResult[w.id]}</span>
                    )}
                    <button
                      onClick={() => remove(w.id)}
                      title="Delete webhook"
                      className="text-ink-faint hover:text-danger"
                    >
                      <Trash2 size={13} />
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
          {canManage && (
            <Button size="sm" variant="outline" className="mt-3" onClick={() => setOpen(true)}>
              <Plus size={13} /> Add webhook
            </Button>
          )}
        </>
      )}

      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
          setNewSecret(null);
        }}
        title={newSecret ? "Webhook created" : "Add webhook"}
        description={
          newSecret
            ? "Store this signing secret now — it is shown only once."
            : "Payloads are JSON, signed with HMAC-SHA256 in the X-Signature header."
        }
      >
        {newSecret ? (
          <div className="space-y-3">
            <code className="block break-all rounded-lg border border-line bg-void/60 p-3 font-mono text-xs text-aurora">
              {newSecret}
            </code>
            <div className="flex justify-end gap-2">
              <CopyButton text={newSecret} label="Copy secret" />
              <Button size="sm" onClick={() => { setOpen(false); setNewSecret(null); }}>
                Done
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <Label htmlFor="wh-url">Endpoint URL (https)</Label>
              <Input
                id="wh-url"
                placeholder="https://example.com/hooks/novaris"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
            <div>
              <Label>Events</Label>
              <div className="flex flex-wrap gap-1.5">
                {ALL_EVENTS.map((e) => {
                  const on = events.includes(e);
                  return (
                    <button
                      key={e}
                      onClick={() =>
                        setEvents((cur) => (on ? cur.filter((x) => x !== e) : [...cur, e]))
                      }
                      className={`rounded-full border px-2.5 py-1 text-[11px] ${
                        on
                          ? "border-accent/40 bg-accent/15 text-accent-soft"
                          : "border-line text-ink-faint hover:text-ink-muted"
                      }`}
                    >
                      {e}
                    </button>
                  );
                })}
              </div>
            </div>
            <FieldError>{error}</FieldError>
            <div className="flex justify-end">
              <Button size="sm" onClick={create} loading={saving} disabled={!url || events.length === 0}>
                Create webhook
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}
