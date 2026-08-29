"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { MessageCircle, Trash2 } from "lucide-react";
import { EmptyState } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { timeAgo } from "@/lib/utils";

export type EntryComment = {
  id: string;
  body: string;
  createdAt: string;
  authorName: string | null;
  guestName: string | null;
};

export type ReactionGroup = { emoji: string; count: number };

/**
 * Read-only engagement panel for an entry: reaction totals plus the
 * comments received on the public changelog. ADMIN+ can delete comments.
 */
export function EntryEngagement({
  orgSlug,
  entryId,
  reactions,
  comments: initialComments,
  canModerate,
}: {
  orgSlug: string;
  entryId: string;
  reactions: ReactionGroup[];
  comments: EntryComment[];
  canModerate: boolean;
}) {
  const router = useRouter();
  const [comments, setComments] = React.useState(initialComments);
  const [deletingId, setDeletingId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => setComments(initialComments), [initialComments]);

  const remove = async (commentId: string) => {
    setDeletingId(commentId);
    setError(null);
    try {
      const res = await fetch(
        `/api/orgs/${orgSlug}/changelog/${entryId}/comments/${commentId}`,
        { method: "DELETE" }
      );
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Could not delete the comment");
      }
      setComments((prev) => prev.filter((c) => c.id !== commentId));
      router.refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not delete the comment"
      );
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Reactions
        </h3>
        {reactions.length === 0 ? (
          <p className="text-xs text-ink-faint">No reactions yet.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {reactions.map((r) => (
              <Badge key={r.emoji} tone="neutral" className="text-xs">
                <span aria-hidden>{r.emoji}</span>
                {r.count}
              </Badge>
            ))}
          </div>
        )}
      </div>
      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Comments ({comments.length})
        </h3>
        {error && (
          <p role="alert" className="mb-2 text-xs text-danger">
            {error}
          </p>
        )}
        {comments.length === 0 ? (
          <EmptyState
            icon={<MessageCircle size={22} aria-hidden />}
            title="No comments yet"
            description="Comments left on the public changelog show up here."
            className="py-8"
          />
        ) : (
          <ul className="space-y-2">
            {comments.map((comment) => (
              <li
                key={comment.id}
                className="rounded-lg border border-line bg-surface px-3 py-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-medium text-ink">
                    {comment.authorName ?? comment.guestName ?? "Anonymous"}
                    <span className="ml-2 font-normal text-ink-faint">
                      {timeAgo(comment.createdAt)}
                    </span>
                  </p>
                  {canModerate && (
                    <button
                      type="button"
                      aria-label="Delete comment"
                      disabled={deletingId === comment.id}
                      onClick={() => remove(comment.id)}
                      className="rounded p-1 text-ink-faint hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                    >
                      <Trash2 size={13} aria-hidden />
                    </button>
                  )}
                </div>
                <p className="mt-1 whitespace-pre-wrap text-sm text-ink-muted">
                  {comment.body}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
