import "server-only";
import { cache } from "react";
import type { PostStatus, Prisma, PrismaClient } from "@prisma/client";
import { db } from "@/lib/db";
import { getPublicOrg } from "@/lib/auth/guards";
import { getCurrentUser, getGuestId } from "@/lib/auth/session";
import { priorityScore } from "@/lib/priority";
import type { PortalComment, PortalPost, PortalSort } from "./types";

/**
 * Server-side data helpers for the public portal + widget vertical.
 * Everything here is tenant-scoped: callers pass an org id that was resolved
 * through `getPortalOrg` (portal-enabled orgs only), and every query filters
 * on it. Only public-board posts are ever visible to guests.
 */

/** Cached-per-request lookup of a portal-enabled org by slug. */
export const getPortalOrg = cache(async (orgSlug: string) => getPublicOrg(orgSlug));

/** Who is looking at the portal right now (member, known guest, or nobody). */
export const getViewer = cache(async () => {
  const user = await getCurrentUser();
  const guestId = user ? null : await getGuestId();
  return { user, guestId };
});

/**
 * Base where-clause for guest-visible posts: the org's posts on public boards
 * (a null boardId counts as public), never archived, never merged duplicates.
 */
export function publicPostWhere(orgId: string): Prisma.PostWhereInput {
  return {
    orgId,
    archived: false,
    mergedIntoId: null,
    OR: [{ boardId: null }, { board: { isPublic: true } }],
  };
}

export const PORTAL_POST_SELECT = {
  id: true,
  title: true,
  body: true,
  aiSummary: true,
  status: true,
  type: true,
  voteCount: true,
  commentCount: true,
  pinned: true,
  createdAt: true,
  category: { select: { name: true, color: true } },
} satisfies Prisma.PostSelect;

export type RawPortalPost = Prisma.PostGetPayload<{
  select: typeof PORTAL_POST_SELECT;
}>;

/** Recent-weighted demand score used by the "trending" sort. */
export function trendingScore(p: {
  voteCount: number;
  commentCount: number;
  createdAt: Date;
}): number {
  const ageDays = Math.max(0, (Date.now() - p.createdAt.getTime()) / 86400_000);
  return (p.voteCount + p.commentCount * 0.4 + 1) / Math.pow(ageDays + 2, 1.35);
}

export async function listPublicPosts(
  orgId: string,
  opts: {
    q?: string;
    status?: PostStatus;
    sort?: PortalSort;
    limit?: number;
  } = {}
): Promise<RawPortalPost[]> {
  const { q, status, sort = "trending" } = opts;
  const limit = Math.min(Math.max(opts.limit ?? 30, 1), 100);
  const where: Prisma.PostWhereInput = { ...publicPostWhere(orgId) };
  if (status) where.status = status;
  if (q) {
    where.AND = [
      {
        OR: [
          { title: { contains: q, mode: "insensitive" } },
          { body: { contains: q, mode: "insensitive" } },
        ],
      },
    ];
  }

  if (sort === "new") {
    return db.post.findMany({
      where,
      orderBy: [{ pinned: "desc" }, { createdAt: "desc" }, { id: "desc" }],
      take: limit,
      select: PORTAL_POST_SELECT,
    });
  }
  if (sort === "top") {
    return db.post.findMany({
      where,
      orderBy: [{ pinned: "desc" }, { voteCount: "desc" }, { createdAt: "desc" }],
      take: limit,
      select: PORTAL_POST_SELECT,
    });
  }
  // Trending: recent-weighted votes, computed over a recency window in memory.
  const pool = await db.post.findMany({
    where,
    orderBy: { createdAt: "desc" },
    take: 400,
    select: PORTAL_POST_SELECT,
  });
  return pool
    .sort(
      (a, b) =>
        Number(b.pinned) - Number(a.pinned) || trendingScore(b) - trendingScore(a)
    )
    .slice(0, limit);
}

/** Ids of the given posts the current viewer (user or guest) has voted on. */
export async function votedPostIds(
  postIds: string[],
  userId: string | null,
  guestId: string | null
): Promise<Set<string>> {
  if (postIds.length === 0 || (!userId && !guestId)) return new Set();
  const votes = await db.vote.findMany({
    where: {
      postId: { in: postIds },
      ...(userId ? { userId } : { guestId: guestId! }),
    },
    select: { postId: true },
  });
  return new Set(votes.map((v) => v.postId));
}

export function toPortalPost(p: RawPortalPost, voted: boolean): PortalPost {
  const snippetSource = (p.aiSummary?.trim() || p.body).replace(/\s+/g, " ").trim();
  return {
    id: p.id,
    title: p.title,
    snippet:
      snippetSource.length > 200 ? `${snippetSource.slice(0, 200)}…` : snippetSource,
    status: p.status,
    type: p.type,
    voteCount: p.voteCount,
    commentCount: p.commentCount,
    pinned: p.pinned,
    createdAt: p.createdAt.toISOString(),
    category: p.category,
    voted,
  };
}

type RawComment = {
  id: string;
  body: string;
  isTeam: boolean;
  guestName: string | null;
  createdAt: Date;
  author: { name: string } | null;
};

export function toPortalComment(c: RawComment): PortalComment {
  return {
    id: c.id,
    name: c.author?.name ?? c.guestName?.trim() ?? "Anonymous",
    isTeam: c.isTeam,
    body: c.body,
    createdAt: c.createdAt.toISOString(),
  };
}

type DbLike = Prisma.TransactionClient | PrismaClient;

/**
 * Recompute and persist a post's priorityScore from its stored signals.
 * Local twin of the dashboard helper — kept here so this vertical never
 * depends on files owned by the feedback vertical.
 */
export async function recomputePortalPriority(
  tx: DbLike,
  postId: string
): Promise<number> {
  const post = await tx.post.findUnique({ where: { id: postId } });
  if (!post) return 0;
  const score = priorityScore({
    voteCount: post.voteCount,
    commentCount: post.commentCount,
    sentimentScore: post.sentimentScore,
    impact: post.impact,
    effort: post.effort,
    revenueImpact: post.revenueImpact,
    createdAt: post.createdAt,
  });
  await tx.post.update({ where: { id: postId }, data: { priorityScore: score } });
  return score;
}
