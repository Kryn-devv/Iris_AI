import Link from "next/link";
import { MessageSquare, Pin } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { POST_STATUS } from "@/lib/status";
import { timeAgo } from "@/lib/utils";
import { VoteButton } from "./vote-button";
import type { PortalPost } from "./types";

/** One post row on the public board (server component + vote island). */
export function PostCard({
  orgSlug,
  post,
}: {
  orgSlug: string;
  post: PortalPost;
}) {
  const status = POST_STATUS[post.status];
  return (
    <article className="group flex gap-4 rounded-xl border border-line bg-surface-raised p-4 shadow-card transition-colors hover:border-line-strong">
      <VoteButton
        orgSlug={orgSlug}
        postId={post.id}
        voted={post.voted}
        count={post.voteCount}
      />
      <div className="min-w-0 flex-1">
        <Link
          href={`/p/${orgSlug}/posts/${post.id}`}
          className="block focus:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 rounded"
        >
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink group-hover:text-accent-soft">
            {post.pinned && (
              <Pin size={12} aria-label="Pinned" className="shrink-0 text-accent-soft" />
            )}
            <span className="truncate">{post.title}</span>
          </h3>
          {post.snippet && (
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-ink-muted">
              {post.snippet}
            </p>
          )}
        </Link>
        <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs text-ink-faint">
          <Badge tone={status.tone}>{status.label}</Badge>
          {post.category && (
            <span className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className="h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: post.category.color }}
              />
              {post.category.name}
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <MessageSquare size={12} aria-hidden />
            {post.commentCount}
          </span>
          <span>{timeAgo(post.createdAt)}</span>
        </div>
      </div>
    </article>
  );
}
