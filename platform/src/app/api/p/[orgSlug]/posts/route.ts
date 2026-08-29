import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { db } from "@/lib/db";
import { ensureGuestId, getCurrentUser, getGuestId } from "@/lib/auth/session";
import { analyzePostText } from "@/lib/ai/analyze";
import { findSimilar } from "@/lib/similarity";
import { priorityScore } from "@/lib/priority";
import { recordActivity, dispatchWebhooks } from "@/lib/events";
import {
  getPortalOrg,
  listPublicPosts,
  publicPostWhere,
  toPortalPost,
  votedPostIds,
  PORTAL_POST_SELECT,
} from "@/components/portal/data";
import { PORTAL_SORTS, type PortalSort } from "@/components/portal/types";
import type { PostStatus } from "@prisma/client";

type Ctx = { params: Promise<{ orgSlug: string }> };

const STATUSES: PostStatus[] = [
  "OPEN",
  "UNDER_CONSIDERATION",
  "PLANNED",
  "IN_PROGRESS",
  "SHIPPED",
  "CLOSED",
];

/**
 * GET /api/p/[orgSlug]/posts — public post list.
 *   ?similarTo=<text>       duplicate suggestions for a draft title
 *   ?q=&status=&sort=&limit list with search / filter / sort
 * Only public-board, non-archived, non-merged posts ever leave this endpoint.
 */
export async function GET(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const org = await getPortalOrg(orgSlug);
    if (!org) return fail(404, "Portal not found");

    const user = await getCurrentUser();
    const guestId = user ? null : await getGuestId();
    const url = new URL(req.url);

    const similarTo = url.searchParams.get("similarTo")?.slice(0, 300).trim();
    if (similarTo) {
      if (similarTo.length < 3) return ok({ posts: [] });
      const candidates = await db.post.findMany({
        where: publicPostWhere(org.id),
        orderBy: { createdAt: "desc" },
        take: 300,
        select: { id: true, title: true, body: true },
      });
      const matches = findSimilar({ title: similarTo }, candidates, {
        threshold: 0.16,
        limit: 5,
      });
      if (matches.length === 0) return ok({ posts: [] });
      const rows = await db.post.findMany({
        where: { ...publicPostWhere(org.id), id: { in: matches.map((m) => m.id) } },
        select: PORTAL_POST_SELECT,
      });
      const voted = await votedPostIds(
        rows.map((r) => r.id),
        user?.id ?? null,
        guestId
      );
      const byId = new Map(rows.map((r) => [r.id, r]));
      const posts = matches
        .map((m) => {
          const row = byId.get(m.id);
          if (!row) return null;
          return { ...toPortalPost(row, voted.has(row.id)), score: m.score };
        })
        .filter((p) => p !== null);
      return ok({ posts });
    }

    const q = url.searchParams.get("q")?.slice(0, 200) || undefined;
    const statusRaw = url.searchParams.get("status");
    const status = STATUSES.includes(statusRaw as PostStatus)
      ? (statusRaw as PostStatus)
      : undefined;
    const sortRaw = url.searchParams.get("sort");
    const sort: PortalSort = PORTAL_SORTS.includes(sortRaw as PortalSort)
      ? (sortRaw as PortalSort)
      : "trending";
    const limit = Math.min(
      Math.max(Number(url.searchParams.get("limit")) || 30, 1),
      50
    );

    const rows = await listPublicPosts(org.id, { q, status, sort, limit });
    const voted = await votedPostIds(
      rows.map((r) => r.id),
      user?.id ?? null,
      guestId
    );
    return ok({ posts: rows.map((r) => toPortalPost(r, voted.has(r.id))) });
  });
}

const CreateBody = z.object({
  title: z.string().trim().min(3, "Give your idea a short title").max(200),
  body: z.string().trim().max(5000).default(""),
  type: z.enum(["FEEDBACK", "FEATURE_REQUEST"]).default("FEEDBACK"),
  categoryId: z.string().max(64).nullish(),
  guestName: z.string().trim().max(80).optional(),
  guestEmail: z.string().trim().max(200).optional(),
  source: z.enum(["PORTAL", "WIDGET"]).default("PORTAL"),
});

const RATE_LIMIT_WINDOW_MS = 10 * 60_000;
const RATE_LIMIT_MAX = 5;

/**
 * POST /api/p/[orgSlug]/posts — guest/user idea submission from the portal or
 * widget. AI-enriched (sentiment, summary, category suggestion), scored, and
 * lightly rate-limited (5 posts / 10 min per user or guest cookie).
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const org = await getPortalOrg(orgSlug);
    if (!org) return fail(404, "Portal not found");

    const body = await parseBody(req, CreateBody);
    if (body.guestEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.guestEmail)) {
      return fail(400, "That email address doesn't look valid");
    }
    const bodyText = body.body ?? "";
    const postType = body.type ?? "FEEDBACK";
    const postSource = body.source ?? "PORTAL";

    const user = await getCurrentUser();
    const guestId = user ? null : await ensureGuestId();

    // Light rate limit. Posts don't store the guest cookie id, so guest volume
    // is counted via the activity feed (post.created entries carry guestId).
    const since = new Date(Date.now() - RATE_LIMIT_WINDOW_MS);
    const recent = user
      ? await db.post.count({
          where: { orgId: org.id, authorId: user.id, createdAt: { gte: since } },
        })
      : await db.activity.count({
          where: {
            orgId: org.id,
            type: "post.created",
            createdAt: { gte: since },
            meta: { path: ["guestId"], equals: guestId! },
          },
        });
    if (recent >= RATE_LIMIT_MAX) {
      return fail(429, "You're sharing ideas fast — please wait a few minutes.");
    }

    const categories = await db.category.findMany({
      where: { orgId: org.id },
      select: { id: true, name: true },
    });
    let categoryId =
      body.categoryId && categories.some((c) => c.id === body.categoryId)
        ? body.categoryId
        : null;

    const analysis = await analyzePostText(body.title, bodyText, categories);
    if (!categoryId) categoryId = analysis.suggestedCategoryId;

    const now = new Date();
    const post = await db.post.create({
      data: {
        orgId: org.id,
        title: body.title,
        body: bodyText,
        type: postType,
        status: "OPEN",
        source: postSource,
        authorId: user?.id ?? null,
        guestName: user ? null : body.guestName?.trim() || null,
        guestEmail: user ? null : body.guestEmail?.trim() || null,
        categoryId,
        sentiment: analysis.sentiment,
        sentimentScore: analysis.sentimentScore,
        aiSummary: analysis.aiSummary,
        priorityScore: priorityScore({
          voteCount: 0,
          commentCount: 0,
          sentimentScore: analysis.sentimentScore,
          createdAt: now,
        }),
      },
      select: { id: true, title: true, type: true, status: true, source: true },
    });

    await recordActivity(
      org.id,
      "post.created",
      {
        postId: post.id,
        title: post.title,
        source: post.source,
        ...(guestId ? { guestId } : {}),
      },
      user?.id
    );
    await dispatchWebhooks(org.id, "post.created", {
      id: post.id,
      title: post.title,
      type: post.type,
      status: post.status,
      source: post.source,
      url: `/p/${org.slug}/posts/${post.id}`,
    });

    return ok({ id: post.id }, { status: 201 });
  });
}
