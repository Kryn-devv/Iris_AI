import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { analyzePostText } from "@/lib/ai/analyze";
import { priorityScore } from "@/lib/priority";
import { recordActivity, dispatchWebhooks } from "@/lib/events";
import {
  buildPostWhere,
  buildPostOrderBy,
  parsePostFilters,
  saveAttachmentFiles,
  POSTS_PAGE_SIZE,
  MAX_ATTACHMENTS,
} from "./helpers";

type Ctx = { params: Promise<{ orgSlug: string }> };

/**
 * GET /api/orgs/[orgSlug]/posts — filterable, paginated list.
 * Supports: status, type, category, tag, sentiment, source, q, sort,
 * archived=1, exclude (post id), page, take (<=50).
 */
export async function GET(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug);
    const url = new URL(req.url);
    const sp: Record<string, string | undefined> = {};
    url.searchParams.forEach((v, k) => (sp[k] = v));
    const filters = parsePostFilters(sp);
    filters.exclude = sp.exclude;

    const page = Math.max(1, Number(sp.page) || 1);
    const take = Math.min(50, Math.max(1, Number(sp.take) || POSTS_PAGE_SIZE));
    const where = buildPostWhere(org.id, filters);

    const [total, posts] = await Promise.all([
      db.post.count({ where }),
      db.post.findMany({
        where,
        orderBy: buildPostOrderBy(filters.sort),
        skip: (page - 1) * take,
        take,
        select: {
          id: true,
          title: true,
          status: true,
          type: true,
          voteCount: true,
          commentCount: true,
          priorityScore: true,
          createdAt: true,
        },
      }),
    ]);
    return ok({ total, page, take, posts });
  });
}

const AttachmentSchema = z.object({
  filename: z.string().max(200),
  // ~500KB binary is ~683KB of base64; leave headroom for the data: prefix.
  dataUrl: z.string().max(720_000),
});

const CreateBody = z.object({
  title: z.string().trim().min(1).max(200),
  body: z.string().trim().min(1).max(10_000),
  type: z.enum(["FEEDBACK", "FEATURE_REQUEST"]).optional(),
  boardId: z.string().optional(),
  categoryId: z.string().nullish(),
  tagIds: z.array(z.string()).max(20).optional(),
  attachments: z.array(AttachmentSchema).max(MAX_ATTACHMENTS).optional(),
});

/** POST /api/orgs/[orgSlug]/posts — create feedback from the dashboard. */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org, user } = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, CreateBody);
    const postType = body.type ?? "FEEDBACK";
    const tagIds = body.tagIds ?? [];
    const rawAttachments = body.attachments ?? [];

    // Resolve board: explicit (must belong to org) or the General default.
    let boardId: string | null = null;
    if (body.boardId) {
      const board = await db.board.findFirst({
        where: { id: body.boardId, orgId: org.id },
        select: { id: true },
      });
      if (!board) return fail(400, "Unknown board");
      boardId = board.id;
    } else {
      const general =
        (await db.board.findFirst({
          where: { orgId: org.id, slug: "general" },
          select: { id: true },
        })) ??
        (await db.board.findFirst({
          where: { orgId: org.id },
          orderBy: { createdAt: "asc" },
          select: { id: true },
        })) ??
        (await db.board.create({
          data: { orgId: org.id, name: "General", slug: "general" },
          select: { id: true },
        }));
      boardId = general.id;
    }

    const categories = await db.category.findMany({
      where: { orgId: org.id },
      select: { id: true, name: true },
    });

    // Validate client-picked category/tags belong to this org.
    let categoryId: string | null = null;
    if (body.categoryId) {
      if (!categories.some((c) => c.id === body.categoryId)) {
        return fail(400, "Unknown category");
      }
      categoryId = body.categoryId;
    }
    const orgTags = tagIds.length
      ? await db.tag.findMany({
          where: { orgId: org.id, id: { in: tagIds } },
          select: { id: true },
        })
      : [];

    // AI enrichment: sentiment, one-line summary, suggested category.
    const analysis = await analyzePostText(body.title, body.body, categories);
    if (!categoryId && analysis.suggestedCategoryId) {
      categoryId = analysis.suggestedCategoryId;
    }

    const attachments = await saveAttachmentFiles(rawAttachments);
    const createdAt = new Date();
    const score = priorityScore({
      voteCount: 0,
      commentCount: 0,
      sentimentScore: analysis.sentimentScore,
      impact: null,
      effort: null,
      revenueImpact: null,
      createdAt,
    });

    const post = await db.post.create({
      data: {
        orgId: org.id,
        boardId,
        type: postType,
        title: body.title,
        body: body.body,
        source: "DASHBOARD",
        authorId: user.id,
        categoryId,
        sentiment: analysis.sentiment,
        sentimentScore: analysis.sentimentScore,
        aiSummary: analysis.aiSummary,
        priorityScore: score,
        tags: { create: orgTags.map((t) => ({ tagId: t.id })) },
        attachments: { create: attachments },
      },
      select: { id: true, title: true, type: true, status: true },
    });

    await recordActivity(
      org.id,
      "post.created",
      { postId: post.id, title: post.title, type: post.type },
      user.id
    );
    await dispatchWebhooks(org.id, "post.created", {
      postId: post.id,
      title: post.title,
      type: post.type,
      status: post.status,
      source: "DASHBOARD",
    });

    return ok({ id: post.id }, { status: 201 });
  });
}
