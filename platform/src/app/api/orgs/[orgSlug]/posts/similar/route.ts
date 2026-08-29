import { z } from "zod";
import { api, ok, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { findSimilar } from "@/lib/similarity";

type Ctx = { params: Promise<{ orgSlug: string }> };

const Body = z.object({
  title: z.string().trim().min(2).max(200),
  body: z.string().max(10_000).optional(),
  excludeId: z.string().optional(),
});

/**
 * POST /api/orgs/[orgSlug]/posts/similar — rank existing org posts by
 * similarity to a draft (dedup nudge while typing a new post).
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug);
    const input = await parseBody(req, Body);

    const candidates = await db.post.findMany({
      where: {
        orgId: org.id,
        mergedIntoId: null,
        archived: false,
        ...(input.excludeId ? { id: { not: input.excludeId } } : {}),
      },
      orderBy: { createdAt: "desc" },
      take: 500,
      select: {
        id: true,
        title: true,
        body: true,
        status: true,
        voteCount: true,
      },
    });

    const matches = findSimilar(
      { title: input.title, body: input.body },
      candidates.map((c) => ({ id: c.id, title: c.title, body: c.body })),
      { threshold: 0.22, limit: 5 }
    );

    const byId = new Map(candidates.map((c) => [c.id, c]));
    return ok({
      matches: matches.map((m) => {
        const post = byId.get(m.id)!;
        return {
          id: m.id,
          title: m.title,
          score: m.score,
          status: post.status,
          voteCount: post.voteCount,
        };
      }),
    });
  });
}
