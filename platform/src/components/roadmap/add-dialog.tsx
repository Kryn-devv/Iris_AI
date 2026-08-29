"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ChevronUp, Plus, Search } from "lucide-react";
import type { PostStatus } from "@prisma/client";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { EmptyState, Spinner } from "@/components/ui/misc";
import { POST_STATUS, ROADMAP_STATUSES } from "@/lib/status";
import { compactNumber } from "@/lib/utils";

type Candidate = {
  id: string;
  title: string;
  status: PostStatus;
  type: "FEEDBACK" | "FEATURE_REQUEST";
  voteCount: number;
  category: { name: string; color: string } | null;
};

export function AddToRoadmapButton({ orgSlug }: { orgSlug: string }) {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [q, setQ] = React.useState("");
  const [statusFilter, setStatusFilter] = React.useState("");
  const [target, setTarget] = React.useState<PostStatus>("UNDER_CONSIDERATION");
  const [posts, setPosts] = React.useState<Candidate[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [addingId, setAddingId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [addedAny, setAddedAny] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    const t = window.setTimeout(async () => {
      try {
        const search = new URLSearchParams();
        if (q.trim()) search.set("q", q.trim());
        if (statusFilter) search.set("status", statusFilter);
        const res = await fetch(
          `/api/orgs/${orgSlug}/roadmap/candidates?${search.toString()}`
        );
        const json = await res.json().catch(() => null);
        if (!cancelled) {
          if (json?.ok) {
            setPosts(json.data.posts as Candidate[]);
            setError(null);
          } else {
            setError(json?.error?.message ?? "Could not load posts");
          }
        }
      } catch {
        if (!cancelled) setError("Could not load posts");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [open, q, statusFilter, orgSlug]);

  const close = () => {
    setOpen(false);
    if (addedAny) {
      setAddedAny(false);
      router.refresh();
    }
  };

  const add = async (post: Candidate) => {
    setAddingId(post.id);
    setError(null);
    try {
      const res = await fetch(`/api/orgs/${orgSlug}/roadmap/${post.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ showOnRoadmap: true, status: target }),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Could not add the post");
      }
      setPosts((prev) => prev.filter((p) => p.id !== post.id));
      setAddedAny(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the post");
    } finally {
      setAddingId(null);
    }
  };

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <Plus size={14} aria-hidden />
        Add to roadmap
      </Button>
      <Dialog
        open={open}
        onClose={close}
        title="Add posts to the roadmap"
        description="Pick a target column, then add posts that are not on the roadmap yet."
        className="max-w-2xl"
      >
        <div className="mb-3 grid gap-3 sm:grid-cols-3">
          <div className="sm:col-span-1">
            <Label htmlFor="rm-target">Target column</Label>
            <Select
              id="rm-target"
              value={target}
              onChange={(e) => setTarget(e.target.value as PostStatus)}
            >
              {ROADMAP_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {POST_STATUS[s].label}
                </option>
              ))}
            </Select>
          </div>
          <div className="sm:col-span-1">
            <Label htmlFor="rm-status">Current status</Label>
            <Select
              id="rm-status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">Any status</option>
              {(Object.keys(POST_STATUS) as PostStatus[]).map((s) => (
                <option key={s} value={s}>
                  {POST_STATUS[s].label}
                </option>
              ))}
            </Select>
          </div>
          <div className="sm:col-span-1">
            <Label htmlFor="rm-search">Search</Label>
            <div className="relative">
              <Search
                size={14}
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
              />
              <Input
                id="rm-search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search posts…"
                className="pl-8"
              />
            </div>
          </div>
        </div>
        {error && (
          <p role="alert" className="mb-2 text-xs text-danger">
            {error}
          </p>
        )}
        <div className="max-h-80 space-y-1 overflow-y-auto pr-1">
          {loading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : posts.length === 0 ? (
            <EmptyState
              title="No matching posts"
              description="Every matching post is already on the roadmap, or nothing matches your search."
              className="py-8"
            />
          ) : (
            posts.map((post) => (
              <div
                key={post.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-line bg-surface px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-ink">{post.title}</p>
                  <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink-muted">
                    <Badge tone={POST_STATUS[post.status].tone}>
                      {POST_STATUS[post.status].label}
                    </Badge>
                    {post.category && (
                      <span className="inline-flex items-center gap-1">
                        <span
                          aria-hidden
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ backgroundColor: post.category.color }}
                        />
                        {post.category.name}
                      </span>
                    )}
                    <span className="inline-flex items-center gap-0.5">
                      <ChevronUp size={11} aria-hidden />
                      {compactNumber(post.voteCount)}
                    </span>
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  loading={addingId === post.id}
                  onClick={() => add(post)}
                >
                  Add
                </Button>
              </div>
            ))
          )}
        </div>
      </Dialog>
    </>
  );
}
