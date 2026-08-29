import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";

type Ctx = { params: Promise<{ orgSlug: string; tagId: string }> };

const PatchBody = z.object({
  name: z.string().trim().min(1).max(40).optional(),
  color: z
    .string()
    .regex(/^#[0-9a-fA-F]{6}$/)
    .optional(),
});

/** PATCH /api/orgs/[orgSlug]/tags/[tagId] — rename/recolor (MEMBER+). */
export async function PATCH(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, tagId } = await params;
    const { org } = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, PatchBody);

    const tag = await db.tag.findFirst({
      where: { id: tagId, orgId: org.id },
    });
    if (!tag) return fail(404, "Tag not found");

    if (body.name) {
      const clash = await db.tag.findFirst({
        where: {
          orgId: org.id,
          id: { not: tag.id },
          name: { equals: body.name, mode: "insensitive" },
        },
        select: { id: true },
      });
      if (clash) return fail(400, "A tag with that name already exists");
    }

    const updated = await db.tag.update({
      where: { id: tag.id },
      data: {
        ...(body.name ? { name: body.name } : {}),
        ...(body.color ? { color: body.color } : {}),
      },
    });
    return ok({ tag: updated });
  });
}

/** DELETE /api/orgs/[orgSlug]/tags/[tagId] — ADMIN+. */
export async function DELETE(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, tagId } = await params;
    const { org } = await requireOrg(orgSlug, "ADMIN");
    const tag = await db.tag.findFirst({
      where: { id: tagId, orgId: org.id },
      select: { id: true },
    });
    if (!tag) return fail(404, "Tag not found");
    await db.tag.delete({ where: { id: tag.id } });
    return ok({ deleted: true });
  });
}
