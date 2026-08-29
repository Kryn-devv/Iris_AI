import { z } from "zod";
import type { PostStatus } from "@prisma/client";
import { api, ok, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { ROADMAP_STATUSES } from "@/lib/status";
import { ROADMAP_CARD_SELECT, parsePostType, roadmapWhere } from "./helpers";

type Ctx = { params: Promise<{ orgSlug: string }> };

/**
 * GET /api/orgs/[orgSlug]/roadmap — kanban payload: posts grouped into the
 * ROADMAP_STATUSES columns, ordered by roadmapOrder.
 * Optional filters: ?category=…&board=…&type=…
 */
export async function GET(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug);
    const url = new URL(req.url);
    const posts = await db.post.findMany({
      where: roadmapWhere(org.id, {
        categoryId: url.searchParams.get("category") || undefined,
        boardId: url.searchParams.get("board") || undefined,
        type: parsePostType(url.searchParams.get("type")),
      }),
      orderBy: [{ roadmapOrder: "asc" }, { createdAt: "asc" }],
      select: ROADMAP_CARD_SELECT,
    });
    const columns = Object.fromEntries(
      ROADMAP_STATUSES.map((status) => [
        status,
        posts.filter((p) => p.status === status),
      ])
    );
    return ok({ columns });
  });
}

const ReorderBody = z.object({
  status: z.enum(ROADMAP_STATUSES as [PostStatus, ...PostStatus[]]),
  orderedIds: z.array(z.string()).min(1).max(500),
});

/**
 * PATCH /api/orgs/[orgSlug]/roadmap — renormalize a column: assign
 * roadmapOrder 1..n following orderedIds. Pure reordering (no status
 * change, no events) used when fractional midpoints run out of precision.
 * MEMBER+.
 */
export async function PATCH(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const ctx = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, ReorderBody);

    // Only ids that really belong to this org, this column, and the roadmap.
    const valid = await db.post.findMany({
      where: {
        orgId: ctx.org.id,
        id: { in: body.orderedIds },
        showOnRoadmap: true,
        status: body.status,
      },
      select: { id: true },
    });
    const validIds = new Set(valid.map((p) => p.id));
    const ordered = body.orderedIds.filter((id) => validIds.has(id));

    await db.$transaction(
      ordered.map((id, index) =>
        db.post.update({
          where: { id },
          data: { roadmapOrder: index + 1 },
        })
      )
    );

    return ok({ reordered: ordered.length });
  });
}
