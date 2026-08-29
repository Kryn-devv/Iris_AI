"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Archive, GitMerge, Pin, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input, Select, Label, FieldError } from "@/components/ui/input";
import { POST_STATUS } from "@/lib/status";
import { cn } from "@/lib/utils";
import { MergeDialog } from "./merge-dialog";
import { apiFetch, type CategoryOption, type TagOption } from "./types";

export type PostMetaData = {
  id: string;
  status: string;
  categoryId: string | null;
  tagIds: string[];
  impact: number | null;
  effort: number | null;
  revenueImpact: number | null;
  pinned: boolean;
  archived: boolean;
  merged: boolean;
};

const SCALE = [1, 2, 3, 4, 5];

/**
 * Detail-page sidebar: status / category / tags / impact / effort / revenue
 * editors (MEMBER+), pin/archive/delete (ADMIN+) and the merge flow.
 */
export function PostMetaSidebar({
  orgSlug,
  post,
  categories: initialCategories,
  tags: initialTags,
  canEdit,
  canAdmin,
}: {
  orgSlug: string;
  post: PostMetaData;
  categories: CategoryOption[];
  tags: TagOption[];
  canEdit: boolean;
  canAdmin: boolean;
}) {
  const router = useRouter();
  const [tags, setTags] = React.useState(initialTags);
  const [tagIds, setTagIds] = React.useState<Set<string>>(new Set(post.tagIds));
  const [revenue, setRevenue] = React.useState(
    post.revenueImpact != null ? String(post.revenueImpact) : ""
  );
  const [newTag, setNewTag] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [mergeOpen, setMergeOpen] = React.useState(false);
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);

  const patch = async (data: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/orgs/${orgSlug}/posts/${post.id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      });
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  };

  const toggleTag = async (id: string) => {
    const next = new Set(tagIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setTagIds(next);
    await patch({ tagIds: Array.from(next) });
  };

  const addTag = async () => {
    const name = newTag.trim();
    if (!name) return;
    try {
      const { tag } = await apiFetch<{ tag: TagOption }>(
        `/api/orgs/${orgSlug}/tags`,
        { method: "POST", body: JSON.stringify({ name }) }
      );
      setTags((prev) =>
        prev.some((t) => t.id === tag.id) ? prev : [...prev, tag]
      );
      setNewTag("");
      if (!tagIds.has(tag.id)) await toggleTag(tag.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create tag");
    }
  };

  const saveRevenue = async () => {
    const value = revenue.trim() === "" ? null : Math.max(0, Number(revenue));
    if (value !== null && !Number.isFinite(value)) return;
    await patch({ revenueImpact: value === null ? null : Math.round(value) });
  };

  const remove = async () => {
    setDeleting(true);
    try {
      await apiFetch(`/api/orgs/${orgSlug}/posts/${post.id}`, {
        method: "DELETE",
      });
      router.push(`/app/${orgSlug}/feedback`);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <Label htmlFor="pm-status">Status</Label>
        <Select
          id="pm-status"
          value={post.status}
          disabled={!canEdit || busy}
          onChange={(e) => void patch({ status: e.target.value })}
        >
          {Object.entries(POST_STATUS).map(([value, meta]) => (
            <option key={value} value={value}>
              {meta.label}
            </option>
          ))}
        </Select>
      </div>

      <div>
        <Label htmlFor="pm-category">Category</Label>
        <Select
          id="pm-category"
          value={post.categoryId ?? ""}
          disabled={!canEdit || busy}
          onChange={(e) =>
            void patch({ categoryId: e.target.value || null })
          }
        >
          <option value="">No category</option>
          {initialCategories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </Select>
      </div>

      <div>
        <Label>Tags</Label>
        <div className="flex flex-wrap gap-1.5">
          {tags.map((t) => {
            const active = tagIds.has(t.id);
            return (
              <button
                key={t.id}
                type="button"
                disabled={!canEdit || busy}
                aria-pressed={active}
                onClick={() => void toggleTag(t.id)}
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors disabled:opacity-60",
                  active
                    ? "border-accent/50 bg-accent/15 text-accent-soft"
                    : "border-line text-ink-muted hover:border-line-strong hover:text-ink"
                )}
              >
                {t.name}
              </button>
            );
          })}
          {tags.length === 0 && (
            <span className="text-xs text-ink-faint">No tags yet</span>
          )}
        </div>
        {canEdit && (
          <div className="mt-2 flex gap-2">
            <Input
              value={newTag}
              onChange={(e) => setNewTag(e.target.value)}
              placeholder="New tag…"
              maxLength={40}
              className="h-8 text-xs"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void addTag();
                }
              }}
            />
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!newTag.trim() || busy}
              onClick={() => void addTag()}
            >
              Add
            </Button>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor="pm-impact">Impact (1–5)</Label>
          <Select
            id="pm-impact"
            value={post.impact != null ? String(post.impact) : ""}
            disabled={!canEdit || busy}
            onChange={(e) =>
              void patch({
                impact: e.target.value ? Number(e.target.value) : null,
              })
            }
          >
            <option value="">—</option>
            {SCALE.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="pm-effort">Effort (1–5)</Label>
          <Select
            id="pm-effort"
            value={post.effort != null ? String(post.effort) : ""}
            disabled={!canEdit || busy}
            onChange={(e) =>
              void patch({
                effort: e.target.value ? Number(e.target.value) : null,
              })
            }
          >
            <option value="">—</option>
            {SCALE.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <div>
        <Label htmlFor="pm-revenue">Revenue impact ($)</Label>
        <Input
          id="pm-revenue"
          type="number"
          min={0}
          step={100}
          value={revenue}
          disabled={!canEdit || busy}
          onChange={(e) => setRevenue(e.target.value)}
          onBlur={() => void saveRevenue()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void saveRevenue();
            }
          }}
          placeholder="e.g. 5000"
        />
      </div>

      <FieldError>{error}</FieldError>

      {canEdit && !post.merged && (
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={() => setMergeOpen(true)}
        >
          <GitMerge size={13} aria-hidden /> Merge as duplicate
        </Button>
      )}

      {canAdmin && (
        <div className="space-y-2 border-t border-line pt-3">
          <div className="grid grid-cols-2 gap-2">
            <Button
              variant={post.pinned ? "secondary" : "outline"}
              size="sm"
              disabled={busy}
              onClick={() => void patch({ pinned: !post.pinned })}
            >
              <Pin size={13} aria-hidden />
              {post.pinned ? "Unpin" : "Pin"}
            </Button>
            <Button
              variant={post.archived ? "secondary" : "outline"}
              size="sm"
              disabled={busy}
              onClick={() => void patch({ archived: !post.archived })}
            >
              <Archive size={13} aria-hidden />
              {post.archived ? "Unarchive" : "Archive"}
            </Button>
          </div>
          <Button
            variant="danger"
            size="sm"
            className="w-full"
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2 size={13} aria-hidden /> Delete post
          </Button>
        </div>
      )}

      <MergeDialog
        orgSlug={orgSlug}
        postId={post.id}
        open={mergeOpen}
        onClose={() => setMergeOpen(false)}
      />

      <Dialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="Delete this post?"
        description="This permanently removes the post along with its votes, comments and attachments. This cannot be undone."
      >
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setDeleteOpen(false)}>
            Cancel
          </Button>
          <Button variant="danger" loading={deleting} onClick={() => void remove()}>
            Delete permanently
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
