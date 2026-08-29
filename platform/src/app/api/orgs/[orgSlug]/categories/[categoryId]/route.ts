import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";

type Ctx = { params: Promise<{ orgSlug: string; categoryId: string }> };

const PatchBody = z.object({
  name: z.string().trim().min(1).max(50).optional(),
  color: z
    .string()
    .regex(/^#[0-9a-fA-F]{6}$/)
    .optional(),
});

/** PATCH /api/orgs/[orgSlug]/categories/[categoryId] — rename/recolor (MEMBER+). */
export async function PATCH(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, categoryId } = await params;
    const { org } = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, PatchBody);

    const category = await db.category.findFirst({
      where: { id: categoryId, orgId: org.id },
    });
    if (!category) return fail(404, "Category not found");

    if (body.name) {
      const clash = await db.category.findFirst({
        where: {
          orgId: org.id,
          id: { not: category.id },
          name: { equals: body.name, mode: "insensitive" },
        },
        select: { id: true },
      });
      if (clash) return fail(400, "A category with that name already exists");
    }

    const updated = await db.category.update({
      where: { id: category.id },
      data: {
        ...(body.name ? { name: body.name } : {}),
        ...(body.color ? { color: body.color } : {}),
      },
    });
    return ok({ category: updated });
  });
}

/** DELETE /api/orgs/[orgSlug]/categories/[categoryId] — ADMIN+. */
export async function DELETE(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, categoryId } = await params;
    const { org } = await requireOrg(orgSlug, "ADMIN");
    const category = await db.category.findFirst({
      where: { id: categoryId, orgId: org.id },
      select: { id: true },
    });
    if (!category) return fail(404, "Category not found");
    await db.category.delete({ where: { id: category.id } });
    return ok({ deleted: true });
  });
}
