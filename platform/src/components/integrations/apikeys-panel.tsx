"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input, Label, FieldError } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/misc";
import { timeAgo } from "@/lib/utils";
import { CopyButton } from "./copy-button";

export type ApiKeyRow = {
  id: string;
  name: string;
  prefix: string;
  lastUsedAt: string | null;
  createdAt: string;
};

export function ApiKeysPanel({
  orgSlug,
  apiKeys,
  canManage,
}: {
  orgSlug: string;
  apiKeys: ApiKeyRow[];
  canManage: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [newKey, setNewKey] = React.useState<string | null>(null);

  async function create() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/orgs/${orgSlug}/apikeys`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error?.message ?? "Failed to create key");
      setNewKey(json.data.key);
      setName("");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(false);
    }
  }

  async function revoke(id: string) {
    if (!confirm("Revoke this API key? Requests using it will stop working immediately.")) return;
    await fetch(`/api/orgs/${orgSlug}/apikeys/${id}`, { method: "DELETE" });
    router.refresh();
  }

  return (
    <div>
      {apiKeys.length === 0 ? (
        <EmptyState
          icon={<KeyRound size={18} />}
          title="No API keys"
          description="Create a key to read and submit feedback through the public REST API."
          action={
            canManage ? (
              <Button size="sm" onClick={() => setOpen(true)}>
                <Plus size={13} /> Create key
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <ul className="divide-y divide-line/60">
            {apiKeys.map((k) => (
              <li key={k.id} className="flex items-center gap-3 py-2.5 text-sm">
                <span className="min-w-0 flex-1 truncate text-ink">{k.name}</span>
                <code className="font-mono text-xs text-ink-muted">{k.prefix}…</code>
                <span className="w-28 text-right text-[11px] text-ink-faint">
                  {k.lastUsedAt ? `used ${timeAgo(k.lastUsedAt)}` : "never used"}
                </span>
                {canManage && (
                  <button
                    onClick={() => revoke(k.id)}
                    title="Revoke key"
                    className="text-ink-faint hover:text-danger"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </li>
            ))}
          </ul>
          {canManage && (
            <Button size="sm" variant="outline" className="mt-3" onClick={() => setOpen(true)}>
              <Plus size={13} /> Create key
            </Button>
          )}
        </>
      )}

      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
          setNewKey(null);
        }}
        title={newKey ? "API key created" : "Create API key"}
        description={
          newKey
            ? "Copy this key now — it is shown only once and stored hashed."
            : "Keys authorize the public REST API via an Authorization: Bearer header."
        }
      >
        {newKey ? (
          <div className="space-y-3">
            <code className="block break-all rounded-lg border border-line bg-void/60 p-3 font-mono text-xs text-aurora">
              {newKey}
            </code>
            <div className="flex justify-end gap-2">
              <CopyButton text={newKey} label="Copy key" />
              <Button size="sm" onClick={() => { setOpen(false); setNewKey(null); }}>
                Done
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <Label htmlFor="key-name">Key name</Label>
              <Input
                id="key-name"
                placeholder="Production server"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <FieldError>{error}</FieldError>
            <div className="flex justify-end">
              <Button size="sm" onClick={create} loading={saving} disabled={!name.trim()}>
                Create key
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}
