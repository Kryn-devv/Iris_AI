import { z } from "zod";
import { api, ok, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";

type Ctx = { params: Promise<{ orgSlug: string }> };

/** GET /api/orgs/[orgSlug]/categories — all org categories. */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug);
    const categories = await db.category.findMany({
      where: { orgId: org.id },
      orderBy: { name: "asc" },
      include: { _count: { select: { posts: true } } },
    });
    return ok({ categories });
  });
}

const CreateBody = z.object({
  name: z.string().trim().min(1).max(50),
  color: z
    .string()
    .regex(/^#[0-9a-fA-F]{6}$/)
    .optional(),
});

/**
 * POST /api/orgs/[orgSlug]/categories — create (MEMBER+, e.g. on the fly
 * from the post dialog). Returns the existing category when the name is taken.
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, CreateBody);

    const existing = await db.category.findFirst({
      where: { orgId: org.id, name: { equals: body.name, mode: "insensitive" } },
    });
    if (existing) return ok({ category: existing });

    const category = await db.category.create({
      data: {
        orgId: org.id,
        name: body.name,
        ...(body.color ? { color: body.color } : {}),
      },
    });
    return ok({ category }, { status: 201 });
  });
}
