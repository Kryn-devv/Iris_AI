import Link from "next/link";
import { MessageSquare, Pin, Archive, ChevronUp } from "lucide-react";
import type { PostSource, PostStatus, Sentiment } from "@prisma/client";
import { Badge } from "@/components/ui/badge";
import { POST_STATUS, SENTIMENT_META } from "@/lib/status";
import { timeAgo, compactNumber } from "@/lib/utils";

export type PostRowData = {
  id: string;
  title: string;
  aiSummary: string | null;
  status: PostStatus;
  type: string;
  sentiment: Sentiment | null;
  source: PostSource;
  voteCount: number;
  commentCount: number;
  pinned: boolean;
  archived: boolean;
  createdAt: Date;
  guestName: string | null;
  author: { name: string } | null;
  category: { name: string; color: string } | null;
  tags: { tag: { id: string; name: string; color: string } }[];
};

const SOURCE_LABEL: Record<PostSource, string> = {
  DASHBOARD: "Dashboard",
  PORTAL: "Portal",
  WIDGET: "Widget",
  IMPORT: "Import",
  API: "API",
  EMAIL: "Email",
};

/** One row in the feedback list (server component). */
export function PostListItem({
  post,
  orgSlug,
}: {
  post: PostRowData;
  orgSlug: string;
}) {
  const status = POST_STATUS[post.status];
  const authorName = post.author?.name ?? post.guestName ?? "Guest";
  return (
    <Link
      href={`/app/${orgSlug}/feedback/${post.id}`}
      className="group flex items-start gap-4 border-b border-line px-4 py-3.5 transition-colors last:border-b-0 hover:bg-surface-overlay/50"
    >
      <div
        className="flex w-12 shrink-0 flex-col items-center rounded-lg border border-line bg-surface py-1.5 text-center"
        aria-label={`${post.voteCount} votes`}
      >
        <ChevronUp size={13} aria-hidden className="text-ink-faint" />
        <span className="text-sm font-semibold text-ink">
          {compactNumber(post.voteCount)}
        </span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {post.pinned && (
            <Pin size={12} aria-label="Pinned" className="shrink-0 text-accent-soft" />
          )}
          {post.archived && (
            <Archive size={12} aria-label="Archived" className="shrink-0 text-ink-faint" />
          )}
          <p className="truncate text-sm font-medium text-ink group-hover:text-accent-soft">
            {post.title}
          </p>
        </div>
        {post.aiSummary && (
          <p className="mt-0.5 truncate text-xs text-ink-muted">
            {post.aiSummary}
          </p>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-ink-faint">
          <Badge tone={status.tone}>{status.label}</Badge>
          {post.sentiment && (
            <Badge tone={SENTIMENT_META[post.sentiment].tone}>
              {SENTIMENT_META[post.sentiment].label}
            </Badge>
          )}
          {post.category && (
            <span className="inline-flex items-center gap-1 text-ink-muted">
              <span
                aria-hidden
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: post.category.color }}
              />
              {post.category.name}
            </span>
          )}
          {post.tags.map(({ tag }) => (
            <span
              key={tag.id}
              className="rounded-full border border-line px-1.5 py-px text-[10px] text-ink-muted"
            >
              {tag.name}
            </span>
          ))}
          <span className="inline-flex items-center gap-1">
            <MessageSquare size={11} aria-hidden />
            {post.commentCount}
          </span>
          <span>{SOURCE_LABEL[post.source]}</span>
          <span>{timeAgo(post.createdAt)}</span>
          <span className="truncate">by {authorName}</span>
        </div>
      </div>
    </Link>
  );
}
