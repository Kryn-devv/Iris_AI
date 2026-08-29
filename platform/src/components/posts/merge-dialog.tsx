"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, FieldError } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { POST_STATUS } from "@/lib/status";
import type { PostStatus } from "@prisma/client";
import { cn } from "@/lib/utils";
import { apiFetch } from "./types";

type Candidate = {
  id: string;
  title: string;
  status: string;
  voteCount: number;
};

/**
 * Merge-as-duplicate flow: search the org's posts, pick a canonical target,
 * confirm. Votes move over (voter conflicts skipped) server-side.
 */
export function MergeDialog({
  orgSlug,
  postId,
  open,
  onClose,
}: {
  orgSlug: string;
  postId: string;
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const [query, setQuery] = React.useState("");
  const [results, setResults] = React.useState<Candidate[]>([]);
  const [selected, setSelected] = React.useState<Candidate | null>(null);
  const [searching, setSearching] = React.useState(false);
  const [merging, setMerging] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    const handle = setTimeout(async () => {
      setSearching(true);
      try {
        const qs = new URLSearchParams({ exclude: postId, take: "10" });
        if (query.trim()) qs.set("q", query.trim());
        const data = await apiFetch<{ posts: Candidate[] }>(
          `/api/orgs/${orgSlug}/posts?${qs.toString()}`
        );
        setResults(data.posts);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [open, query, orgSlug, postId]);

  const merge = async () => {
    if (!selected) return;
    setMerging(true);
    setError(null);
    try {
      await apiFetch<{ targetId: string }>(
        `/api/orgs/${orgSlug}/posts/${postId}/merge`,
        { method: "POST", body: JSON.stringify({ targetId: selected.id }) }
      );
      onClose();
      router.push(`/app/${orgSlug}/feedback/${selected.id}`);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Merge failed");
    } finally {
      setMerging(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Merge as duplicate"
      description="This post will be marked as a duplicate; its votes move to the post you pick (existing voters are not double counted)."
    >
      <div className="space-y-3">
        <div className="relative">
          <Search
            size={14}
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search posts to merge into…"
            aria-label="Search merge target"
            className="pl-8"
            autoFocus
          />
        </div>
        <ul className="max-h-56 space-y-1 overflow-y-auto" aria-label="Merge candidates">
          {results.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => setSelected(p)}
                aria-pressed={selected?.id === p.id}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors",
                  selected?.id === p.id
                    ? "border-accent/60 bg-accent/10 text-ink"
                    : "border-line text-ink-muted hover:border-line-strong hover:text-ink"
                )}
              >
                <span className="min-w-0 flex-1 truncate">{p.title}</span>
                <Badge tone={POST_STATUS[p.status as PostStatus]?.tone ?? "neutral"}>
                  {POST_STATUS[p.status as PostStatus]?.label ?? p.status}
                </Badge>
                <span className="shrink-0 text-xs text-ink-faint">
                  {p.voteCount} votes
                </span>
              </button>
            </li>
          ))}
          {!searching && results.length === 0 && (
            <li className="px-3 py-4 text-center text-xs text-ink-faint">
              No matching posts
            </li>
          )}
        </ul>
        <FieldError>{error}</FieldError>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!selected} loading={merging} onClick={() => void merge()}>
            Merge into selected
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
