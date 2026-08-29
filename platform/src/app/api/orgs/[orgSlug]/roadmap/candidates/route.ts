import { PostStatus } from "@prisma/client";
import { api, ok } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";

type Ctx = { params: Promise<{ orgSlug: string }> };

/**
 * GET /api/orgs/[orgSlug]/roadmap/candidates — posts NOT yet on the roadmap,
 * for the "Add to roadmap" dialog. Supports ?q= (title/body search) and
 * ?status= filters. MEMBER+ (it feeds a mutation flow).
 */
export async function GET(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug, "MEMBER");
    const url = new URL(req.url);
    const q = (url.searchParams.get("q") ?? "").trim().slice(0, 200);
    const statusParam = url.searchParams.get("status");
    const status =
      statusParam &&
      (Object.values(PostStatus) as string[]).includes(statusParam)
        ? (statusParam as PostStatus)
        : undefined;

    const posts = await db.post.findMany({
      where: {
        orgId: org.id,
        showOnRoadmap: false,
        archived: false,
        mergedIntoId: null,
        ...(status ? { status } : {}),
        ...(q
          ? {
              OR: [
                { title: { contains: q, mode: "insensitive" } },
                { body: { contains: q, mode: "insensitive" } },
              ],
            }
          : {}),
      },
      orderBy: [{ voteCount: "desc" }, { createdAt: "desc" }],
      take: 30,
      select: {
        id: true,
        title: true,
        status: true,
        type: true,
        voteCount: true,
        category: { select: { name: true, color: true } },
      },
    });

    return ok({ posts });
  });
}
