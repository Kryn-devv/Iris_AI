import { z } from "zod";
import { api, ok, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";

type Ctx = { params: Promise<{ orgSlug: string }> };

/** GET /api/orgs/[orgSlug]/tags — all org tags. */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug);
    const tags = await db.tag.findMany({
      where: { orgId: org.id },
      orderBy: { name: "asc" },
      include: { _count: { select: { posts: true } } },
    });
    return ok({ tags });
  });
}

const CreateBody = z.object({
  name: z.string().trim().min(1).max(40),
  color: z
    .string()
    .regex(/^#[0-9a-fA-F]{6}$/)
    .optional(),
});

/**
 * POST /api/orgs/[orgSlug]/tags — create (MEMBER+, e.g. on the fly from the
 * post dialog). Returns the existing tag when the name is taken.
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, CreateBody);

    const existing = await db.tag.findFirst({
      where: { orgId: org.id, name: { equals: body.name, mode: "insensitive" } },
    });
    if (existing) return ok({ tag: existing });

    const tag = await db.tag.create({
      data: {
        orgId: org.id,
        name: body.name,
        ...(body.color ? { color: body.color } : {}),
      },
    });
    return ok({ tag }, { status: 201 });
  });
}
