import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, GitMerge } from "lucide-react";
import { db } from "@/lib/db";
import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { POST_STATUS, SENTIMENT_META } from "@/lib/status";
import { timeAgo } from "@/lib/utils";
import { VoteButton } from "@/components/posts/vote-button";
import { PostMetaSidebar } from "@/components/posts/post-meta";
import {
  CommentsThread,
  type SerializedComment,
} from "@/components/posts/comments";

export const dynamic = "force-dynamic";

const SOURCE_LABEL: Record<string, string> = {
  DASHBOARD: "Dashboard",
  PORTAL: "Portal",
  WIDGET: "Widget",
  IMPORT: "Import",
  API: "API",
  EMAIL: "Email",
};

export default async function PostDetailPage({
  params,
}: {
  params: Promise<{ orgSlug: string; postId: string }>;
}) {
  const { orgSlug, postId } = await params;
  const ctx = await requireOrgPage(orgSlug);

  const post = await db.post.findFirst({
    where: { id: postId, orgId: ctx.org.id },
    include: {
      category: { select: { id: true, name: true, color: true } },
      board: { select: { name: true } },
      author: { select: { name: true, avatarUrl: true } },
      attachments: true,
      tags: { include: { tag: { select: { id: true, name: true, color: true } } } },
      mergedInto: { select: { id: true, title: true } },
      mergedPosts: {
        select: { id: true, title: true, voteCount: true },
        orderBy: { createdAt: "desc" },
      },
      comments: {
        orderBy: { createdAt: "asc" },
        include: { author: { select: { name: true, avatarUrl: true } } },
      },
    },
  });
  if (!post) notFound();

  const [myVote, categories, tags] = await Promise.all([
    db.vote.findFirst({
      where: { postId: post.id, userId: ctx.user.id },
      select: { id: true },
    }),
    db.category.findMany({
      where: { orgId: ctx.org.id },
      orderBy: { name: "asc" },
      select: { id: true, name: true, color: true },
    }),
    db.tag.findMany({
      where: { orgId: ctx.org.id },
      orderBy: { name: "asc" },
      select: { id: true, name: true, color: true },
    }),
  ]);

  const canEdit = roleAtLeast(ctx.role, "MEMBER");
  const canAdmin = roleAtLeast(ctx.role, "ADMIN");
  const status = POST_STATUS[post.status];
  const authorName = post.author?.name ?? post.guestName ?? "Guest";

  const serializedComments: SerializedComment[] = post.comments.map((c) => ({
    id: c.id,
    body: c.body,
    isTeam: c.isTeam,
    parentId: c.parentId,
    createdAt: c.createdAt.toISOString(),
    authorName: c.author?.name ?? c.guestName ?? "Guest",
    avatarUrl: c.author?.avatarUrl ?? null,
  }));

  return (
    <div>
      <Link
        href={`/app/${orgSlug}/feedback`}
        className="mb-4 inline-flex items-center gap-1.5 text-xs font-medium text-ink-faint hover:text-ink"
      >
        <ArrowLeft size={13} aria-hidden /> Back to feedback
      </Link>

      {post.mergedInto && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/5 px-4 py-3 text-sm text-warning">
          <GitMerge size={15} aria-hidden />
          <span>
            This post was merged into{" "}
            <Link
              href={`/app/${orgSlug}/feedback/${post.mergedInto.id}`}
              className="font-medium underline underline-offset-2"
            >
              {post.mergedInto.title}
            </Link>
            .
          </span>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_280px]">
        <div className="min-w-0 space-y-6">
          <div className="flex items-start gap-4">
            <VoteButton
              orgSlug={orgSlug}
              postId={post.id}
              count={post.voteCount}
              voted={Boolean(myVote)}
              disabled={Boolean(post.mergedIntoId)}
            />
            <div className="min-w-0 flex-1">
              <h1 className="font-display text-lg font-semibold tracking-tight text-ink">
                {post.title}
              </h1>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
                <Badge tone={status.tone}>{status.label}</Badge>
                <Badge tone="neutral">
                  {post.type === "FEATURE_REQUEST" ? "Feature request" : "Feedback"}
                </Badge>
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
                {post.board && <span>Board: {post.board.name}</span>}
                <span>{SOURCE_LABEL[post.source] ?? post.source}</span>
                <span>{timeAgo(post.createdAt)}</span>
                <span>by {authorName}</span>
              </div>
              {post.aiSummary && (
                <p className="mt-3 rounded-lg border border-accent/20 bg-accent/5 px-3 py-2 text-xs text-accent-soft">
                  AI summary: {post.aiSummary}
                </p>
              )}
            </div>
          </div>

          <Card>
            <CardContent className="pt-4">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
                {post.body}
              </p>
              {post.attachments.length > 0 && (
                <ul className="mt-4 flex flex-wrap gap-3">
                  {post.attachments.map((a) => (
                    <li key={a.id}>
                      <a href={a.url} target="_blank" rel="noopener noreferrer">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={a.url}
                          alt={a.filename}
                          className="h-32 max-w-[240px] rounded-lg border border-line object-cover transition-opacity hover:opacity-80"
                        />
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {post.mergedPosts.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-1.5">
                  <GitMerge size={13} aria-hidden />
                  Duplicates merged into this ({post.mergedPosts.length})
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1.5">
                  {post.mergedPosts.map((m) => (
                    <li key={m.id} className="text-sm">
                      <Link
                        href={`/app/${orgSlug}/feedback/${m.id}`}
                        className="text-ink-muted hover:text-accent-soft hover:underline"
                      >
                        {m.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          <CommentsThread
            orgSlug={orgSlug}
            postId={post.id}
            comments={serializedComments}
            canComment={canEdit}
          />
        </div>

        <aside aria-label="Post settings">
          <Card>
            <CardContent className="pt-4">
              <PostMetaSidebar
                orgSlug={orgSlug}
                post={{
                  id: post.id,
                  status: post.status,
                  categoryId: post.categoryId,
                  tagIds: post.tags.map((t) => t.tag.id),
                  impact: post.impact,
                  effort: post.effort,
                  revenueImpact: post.revenueImpact,
                  pinned: post.pinned,
                  archived: post.archived,
                  merged: Boolean(post.mergedIntoId),
                }}
                categories={categories}
                tags={tags}
                canEdit={canEdit}
                canAdmin={canAdmin}
              />
              <dl className="mt-4 space-y-1.5 border-t border-line pt-3 text-xs">
                <div className="flex justify-between">
                  <dt className="text-ink-faint">Priority score</dt>
                  <dd className="font-semibold text-ink">
                    {post.priorityScore.toFixed(1)}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-faint">Votes</dt>
                  <dd className="text-ink-muted">{post.voteCount}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-faint">Comments</dt>
                  <dd className="text-ink-muted">{post.commentCount}</dd>
                </div>
                {post.shippedAt && (
                  <div className="flex justify-between">
                    <dt className="text-ink-faint">Shipped</dt>
                    <dd className="text-ink-muted">{timeAgo(post.shippedAt)}</dd>
                  </div>
                )}
              </dl>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
