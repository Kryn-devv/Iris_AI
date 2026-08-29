"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Textarea, FieldError } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Avatar, EmptyState } from "@/components/ui/misc";
import { timeAgo } from "@/lib/utils";
import { apiFetch } from "./types";

export type SerializedComment = {
  id: string;
  body: string;
  isTeam: boolean;
  parentId: string | null;
  createdAt: string;
  authorName: string;
  avatarUrl: string | null;
};

function CommentBody({
  comment,
  onReply,
  canComment,
}: {
  comment: SerializedComment;
  onReply?: () => void;
  canComment: boolean;
}) {
  return (
    <div className="flex gap-3">
      <Avatar name={comment.authorName} src={comment.avatarUrl} size={28} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-ink">
            {comment.authorName}
          </span>
          {comment.isTeam && <Badge tone="accent">Team</Badge>}
          <span className="text-[11px] text-ink-faint">
            {timeAgo(comment.createdAt)}
          </span>
        </div>
        <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
          {comment.body}
        </p>
        {canComment && onReply && (
          <button
            type="button"
            onClick={onReply}
            className="mt-1 text-[11px] font-medium text-ink-faint hover:text-accent-soft"
          >
            Reply
          </button>
        )}
      </div>
    </div>
  );
}

function ReplyForm({
  orgSlug,
  postId,
  parentId,
  onDone,
  autoFocus,
}: {
  orgSlug: string;
  postId: string;
  parentId?: string;
  onDone?: () => void;
  autoFocus?: boolean;
}) {
  const router = useRouter();
  const [body, setBody] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!body.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/orgs/${orgSlug}/posts/${postId}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: body.trim(), parentId }),
      });
      setBody("");
      onDone?.();
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not post comment");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-2">
      <Textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder={parentId ? "Write a reply…" : "Write a team comment…"}
        aria-label={parentId ? "Reply" : "New comment"}
        maxLength={5000}
        autoFocus={autoFocus}
        className="min-h-[70px]"
      />
      <FieldError>{error}</FieldError>
      <div className="flex justify-end gap-2">
        {parentId && (
          <Button type="button" variant="ghost" size="sm" onClick={onDone}>
            Cancel
          </Button>
        )}
        <Button type="submit" size="sm" loading={busy} disabled={!body.trim()}>
          {parentId ? "Reply" : "Comment"}
        </Button>
      </div>
    </form>
  );
}

/** Comment thread with one level of nesting and a team reply form. */
export function CommentsThread({
  orgSlug,
  postId,
  comments,
  canComment,
}: {
  orgSlug: string;
  postId: string;
  comments: SerializedComment[];
  canComment: boolean;
}) {
  const [replyTo, setReplyTo] = React.useState<string | null>(null);

  const topLevel = comments.filter((c) => !c.parentId);
  const repliesFor = (id: string) => comments.filter((c) => c.parentId === id);

  return (
    <section aria-label="Comments" className="space-y-5">
      <h2 className="text-sm font-semibold text-ink">
        Comments ({comments.length})
      </h2>

      {canComment && (
        <ReplyForm orgSlug={orgSlug} postId={postId} />
      )}

      {topLevel.length === 0 ? (
        <EmptyState
          title="No comments yet"
          description="Start the conversation — comments posted here are marked as coming from your team."
          className="py-8"
        />
      ) : (
        <ul className="space-y-5">
          {topLevel.map((c) => (
            <li key={c.id}>
              <CommentBody
                comment={c}
                canComment={canComment}
                onReply={() => setReplyTo(replyTo === c.id ? null : c.id)}
              />
              {(repliesFor(c.id).length > 0 || replyTo === c.id) && (
                <div className="ml-9 mt-3 space-y-3 border-l border-line pl-4">
                  {repliesFor(c.id).map((r) => (
                    <CommentBody key={r.id} comment={r} canComment={false} />
                  ))}
                  {replyTo === c.id && (
                    <ReplyForm
                      orgSlug={orgSlug}
                      postId={postId}
                      parentId={c.id}
                      autoFocus
                      onDone={() => setReplyTo(null)}
                    />
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
