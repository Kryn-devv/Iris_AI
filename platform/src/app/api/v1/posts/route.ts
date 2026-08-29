import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { db } from "@/lib/db";
import { analyzePostText } from "@/lib/ai/analyze";
import { priorityScore } from "@/lib/priority";
import { recordActivity, dispatchWebhooks } from "@/lib/events";
import { orgFromApiKey } from "../auth";

const CreateBody = z.object({
  title: z.string().min(3).max(200),
  body: z.string().min(1).max(10_000),
  type: z.enum(["FEEDBACK", "FEATURE_REQUEST"]).optional(),
  guestName: z.string().max(80).optional(),
  guestEmail: z.string().email().optional(),
});

/** GET /api/v1/posts — list public-board posts (paginated). */
export async function GET(req: Request) {
  return api(async () => {
    const org = await orgFromApiKey(req);
    if (!org) return fail(401, "Invalid or missing API key");
    const url = new URL(req.url);
    const page = Math.max(1, Number(url.searchParams.get("page")) || 1);
    const take = Math.min(100, Math.max(1, Number(url.searchParams.get("per_page")) || 25));
    const status = url.searchParams.get("status") ?? undefined;

    const where = {
      orgId: org.id,
      archived: false,
      mergedIntoId: null,
      ...(status ? { status: status as never } : {}),
      OR: [{ boardId: null }, { board: { isPublic: true } }],
    };
    const [total, posts] = await Promise.all([
      db.post.count({ where }),
      db.post.findMany({
        where,
        orderBy: [{ voteCount: "desc" }, { createdAt: "desc" }],
        skip: (page - 1) * take,
        take,
        select: {
          id: true, title: true, body: true, type: true, status: true,
          voteCount: true, commentCount: true, sentiment: true,
          category: { select: { name: true } }, createdAt: true,
        },
      }),
    ]);
    return ok({ page, perPage: take, total, posts });
  });
}

/** POST /api/v1/posts — create feedback programmatically. */
export async function POST(req: Request) {
  return api(async () => {
    const org = await orgFromApiKey(req);
    if (!org) return fail(401, "Invalid or missing API key");
    const body = await parseBody(req, CreateBody);
    const categories = await db.category.findMany({
      where: { orgId: org.id },
      select: { id: true, name: true },
    });
    const analysis = await analyzePostText(body.title, body.body, categories);
    const board = await db.board.findFirst({
      where: { orgId: org.id },
      orderBy: { createdAt: "asc" },
    });
    const post = await db.post.create({
      data: {
        orgId: org.id,
        boardId: board?.id ?? null,
        type: body.type ?? "FEEDBACK",
        title: body.title,
        body: body.body,
        source: "API",
        guestName: body.guestName ?? null,
        guestEmail: body.guestEmail ?? null,
        sentiment: analysis.sentiment,
        sentimentScore: analysis.sentimentScore,
        aiSummary: analysis.aiSummary,
        categoryId: analysis.suggestedCategoryId,
        priorityScore: priorityScore({
          voteCount: 0,
          commentCount: 0,
          sentimentScore: analysis.sentimentScore,
          createdAt: new Date(),
        }),
      },
      select: { id: true, title: true, status: true, createdAt: true },
    });
    await recordActivity(org.id, "post.created", { postId: post.id, title: post.title, via: "api" });
    await dispatchWebhooks(org.id, "post.created", { post });
    return ok(post, { status: 201 });
  });
}
