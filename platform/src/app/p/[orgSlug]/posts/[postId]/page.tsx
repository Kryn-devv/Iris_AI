import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, MessageSquare } from "lucide-react";
import { db } from "@/lib/db";
import { findSimilar } from "@/lib/similarity";
import { POST_STATUS } from "@/lib/status";
import { timeAgo } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Avatar } from "@/components/ui/misc";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getPortalOrg,
  getViewer,
  publicPostWhere,
  toPortalComment,
  votedPostIds,
} from "@/components/portal/data";
import { VoteButton } from "@/components/portal/vote-button";
import { CommentForm } from "@/components/portal/comment-form";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ orgSlug: string; postId: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { orgSlug, postId } = await params;
  const org = await getPortalOrg(orgSlug);
  if (!org) return {};
  const post = await db.post.findFirst({
    where: { ...publicPostWhere(org.id), id: postId },
    select: { title: true, aiSummary: true },
  });
  if (!post) return {};
  return { title: post.title, description: post.aiSummary ?? undefined };
}

export default async function PortalPostPage({ params }: Props) {
  const { orgSlug, postId } = await params;
  const org = await getPortalOrg(orgSlug);
  if (!org) notFound();

  // Merged duplicates redirect to their canonical post.
  const stub = await db.post.findFirst({
    where: { id: postId, orgId: org.id },
    select: { mergedIntoId: true },
  });
  if (stub?.mergedIntoId) {
    redirect(`/p/${org.slug}/posts/${stub.mergedIntoId}`);
  }

  const post = await db.post.findFirst({
    where: { ...publicPostWhere(org.id), id: postId },
    select: {
      id: true,
      title: true,
      body: true,
      status: true,
      type: true,
      voteCount: true,
      commentCount: true,
      createdAt: true,
      guestName: true,
      author: { select: { name: true } },
      category: { select: { name: true, color: true } },
      attachments: {
        select: { id: true, url: true, filename: true, mimeType: true },
      },
      comments: {
        orderBy: { createdAt: "asc" },
        take: 200,
        select: {
          id: true,
          body: true,
          isTeam: true,
          guestName: true,
          createdAt: true,
          author: { select: { name: true } },
        },
      },
    },
  });
  if (!post) notFound();

  const { user, guestId } = await getViewer();

  const candidates = await db.post.findMany({
    where: { ...publicPostWhere(org.id), id: { not: post.id } },
    orderBy: { createdAt: "desc" },
    take: 300,
    select: { id: true, title: true, body: true },
  });
  const similar = findSimilar({ title: post.title, body: post.body }, candidates, {
    threshold: 0.2,
    limit: 5,
  });
  const similarRows = similar.length
    ? await db.post.findMany({
        where: {
          ...publicPostWhere(org.id),
          id: { in: similar.map((s) => s.id) },
        },
        select: { id: true, title: true, voteCount: true, status: true },
      })
    : [];
  const similarById = new Map(similarRows.map((r) => [r.id, r]));

  const voted = await votedPostIds([post.id], user?.id ?? null, guestId);
  const status = POST_STATUS[post.status];
  const authorName = post.author?.name ?? post.guestName?.trim() ?? "Anonymous";
  const comments = post.comments.map(toPortalComment);
  const images = post.attachments.filter((a) => a.mimeType.startsWith("image/"));

  return (
    <div className="space-y-6">
      <Link
        href={`/p/${org.slug}`}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted transition-colors hover:text-ink"
      >
        <ArrowLeft size={13} aria-hidden />
        All feedback
      </Link>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_280px]">
        <div className="min-w-0 space-y-6">
          <article className="rounded-xl border border-line bg-surface-raised p-5 shadow-card sm:p-6">
            <div className="flex gap-4">
              <VoteButton
                orgSlug={org.slug}
                postId={post.id}
                voted={voted.has(post.id)}
                count={post.voteCount}
              />
              <div className="min-w-0 flex-1">
                <h1 className="font-display text-lg font-semibold tracking-tight text-ink sm:text-xl">
                  {post.title}
                </h1>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-faint">
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
                  <span>
                    by <span className="text-ink-muted">{authorName}</span>
                  </span>
                  <span>{timeAgo(post.createdAt)}</span>
                </div>
              </div>
            </div>
            {post.body && (
              <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
                {post.body}
              </p>
            )}
            {images.length > 0 && (
              <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
                {images.map((a) => (
                  <a
                    key={a.id}
                    href={a.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block overflow-hidden rounded-lg border border-line"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={a.url}
                      alt={a.filename}
                      className="h-28 w-full object-cover transition-transform hover:scale-[1.03]"
                    />
                  </a>
                ))}
              </div>
            )}
          </article>

          <section aria-label="Comments" className="space-y-4">
            <h2 className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink">
              <MessageSquare size={14} aria-hidden />
              Comments
              <span className="font-normal text-ink-faint">({comments.length})</span>
            </h2>
            {comments.length > 0 && (
              <ul className="space-y-3">
                {comments.map((c) => (
                  <li
                    key={c.id}
                    className="flex gap-3 rounded-xl border border-line bg-surface-raised p-3.5"
                  >
                    <Avatar name={c.name} size={28} className="mt-0.5 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="flex flex-wrap items-center gap-1.5 text-xs">
                        <span className="font-medium text-ink">{c.name}</span>
                        {c.isTeam && <Badge tone="accent">Team</Badge>}
                        <span className="text-ink-faint">{timeAgo(c.createdAt)}</span>
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
                        {c.body}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <div className="rounded-xl border border-line bg-surface-raised p-4">
              <CommentForm orgSlug={org.slug} postId={post.id} signedIn={!!user} />
            </div>
          </section>
        </div>

        <aside className="space-y-4 lg:sticky lg:top-6 lg:self-start">
          <Card>
            <CardHeader>
              <CardTitle>Similar ideas</CardTitle>
            </CardHeader>
            <CardContent>
              {similar.length === 0 ? (
                <p className="text-xs text-ink-faint">
                  Nothing similar yet — this one stands alone.
                </p>
              ) : (
                <ul className="space-y-2.5">
                  {similar.map((s) => {
                    const row = similarById.get(s.id);
                    if (!row) return null;
                    const meta = POST_STATUS[row.status];
                    return (
                      <li key={s.id}>
                        <Link
                          href={`/p/${org.slug}/posts/${s.id}`}
                          className="group block"
                        >
                          <p className="line-clamp-2 text-xs font-medium text-ink-muted transition-colors group-hover:text-accent-soft">
                            {row.title}
                          </p>
                          <p className="mt-1 flex items-center gap-2 text-[11px] text-ink-faint">
                            <Badge tone={meta.tone}>{meta.label}</Badge>
                            {row.voteCount} votes
                          </p>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
