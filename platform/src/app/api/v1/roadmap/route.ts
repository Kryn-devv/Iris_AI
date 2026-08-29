import { api, ok, fail } from "@/lib/api";
import { db } from "@/lib/db";
import { ROADMAP_STATUSES } from "@/lib/status";
import { orgFromApiKey } from "../auth";

/** GET /api/v1/roadmap — public roadmap grouped by column. */
export async function GET(req: Request) {
  return api(async () => {
    const org = await orgFromApiKey(req);
    if (!org) return fail(401, "Invalid or missing API key");
    const posts = await db.post.findMany({
      where: {
        orgId: org.id,
        showOnRoadmap: true,
        archived: false,
        mergedIntoId: null,
        status: { in: ROADMAP_STATUSES },
      },
      orderBy: { roadmapOrder: "asc" },
      select: {
        id: true, title: true, status: true, voteCount: true,
        category: { select: { name: true } }, shippedAt: true,
      },
    });
    const columns = ROADMAP_STATUSES.map((status) => ({
      status,
      posts: posts.filter((p) => p.status === status),
    }));
    return ok({ columns });
  });
}
