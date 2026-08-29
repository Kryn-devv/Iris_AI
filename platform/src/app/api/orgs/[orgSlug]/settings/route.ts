import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

type Ctx = { params: Promise<{ orgSlug: string }> };

const PatchBody = z.object({
  name: z.string().trim().min(1, "Name is required").max(60).optional(),
  portalEnabled: z.boolean().optional(),
  portalHeadline: z
    .union([z.string().trim().max(120), z.literal("")])
    .optional(),
  portalIntro: z.union([z.string().trim().max(600), z.literal("")]).optional(),
  brandColor: z
    .union([z.string().regex(/^#[0-9a-fA-F]{6}$/, "Use a hex color like #8b8bf5"), z.literal("")])
    .optional(),
});

/** PATCH /api/orgs/[orgSlug]/settings — update workspace settings (ADMIN+). */
export async function PATCH(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org, user } = await requireOrg(orgSlug, "ADMIN");
    const body = await parseBody(req, PatchBody);

    const updated = await db.organization.update({
      where: { id: org.id },
      data: {
        ...(body.name !== undefined ? { name: body.name } : {}),
        ...(body.portalEnabled !== undefined
          ? { portalEnabled: body.portalEnabled }
          : {}),
        ...(body.portalHeadline !== undefined
          ? { portalHeadline: body.portalHeadline || null }
          : {}),
        ...(body.portalIntro !== undefined
          ? { portalIntro: body.portalIntro || null }
          : {}),
        ...(body.brandColor !== undefined
          ? { brandColor: body.brandColor || null }
          : {}),
      },
      select: {
        id: true,
        name: true,
        slug: true,
        portalEnabled: true,
        portalHeadline: true,
        portalIntro: true,
        brandColor: true,
      },
    });

    await recordActivity(org.id, "org.updated", {}, user.id);
    return ok({ org: updated });
  });
}

const DeleteBody = z.object({
  confirmName: z.string(),
});

/**
 * DELETE /api/orgs/[orgSlug]/settings — delete the workspace (OWNER only).
 * Requires typing the exact workspace name to confirm; cascades via Prisma.
 */
export async function DELETE(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug, "OWNER");
    const body = await parseBody(req, DeleteBody);

    if (body.confirmName.trim() !== org.name) {
      return fail(400, "Workspace name does not match.");
    }

    await db.organization.delete({ where: { id: org.id } });
    return ok({ deleted: true });
  });
}
