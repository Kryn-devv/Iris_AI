import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg, AuthError } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

type Ctx = { params: Promise<{ orgSlug: string; memberId: string }> };

const PatchBody = z.object({
  role: z.enum(["VIEWER", "MEMBER", "ADMIN", "OWNER"]),
});

/**
 * PATCH /api/orgs/[orgSlug]/members/[memberId] — change a member's role.
 * ADMIN+ may set VIEWER/MEMBER/ADMIN; only an OWNER may grant or revoke OWNER;
 * the last OWNER can never be demoted.
 */
export async function PATCH(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, memberId } = await params;
    const ctx = await requireOrg(orgSlug, "ADMIN");
    const body = await parseBody(req, PatchBody);

    const target = await db.membership.findFirst({
      where: { id: memberId, orgId: ctx.org.id },
      include: { user: { select: { id: true, name: true, email: true } } },
    });
    if (!target) throw new AuthError(404, "Member not found");

    if (target.role === body.role) {
      return ok({ member: { id: target.id, role: target.role } });
    }

    const touchesOwner = body.role === "OWNER" || target.role === "OWNER";
    if (touchesOwner && ctx.role !== "OWNER") {
      return fail(403, "Only an owner can grant or revoke the owner role.");
    }

    if (target.role === "OWNER" && body.role !== "OWNER") {
      const owners = await db.membership.count({
        where: { orgId: ctx.org.id, role: "OWNER" },
      });
      if (owners <= 1) {
        return fail(400, "You cannot demote the last owner of this workspace.");
      }
    }

    const updated = await db.membership.update({
      where: { id: target.id },
      data: { role: body.role },
    });

    await recordActivity(
      ctx.org.id,
      "member.role_changed",
      { memberEmail: target.user.email, from: target.role, to: body.role },
      ctx.user.id
    );
    return ok({ member: { id: updated.id, role: updated.role } });
  });
}

/**
 * DELETE /api/orgs/[orgSlug]/members/[memberId] — remove a member (ADMIN+).
 * Only an OWNER may remove an OWNER; the last OWNER can never be removed
 * (which also blocks a sole owner from removing themselves).
 */
export async function DELETE(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, memberId } = await params;
    const ctx = await requireOrg(orgSlug, "ADMIN");

    const target = await db.membership.findFirst({
      where: { id: memberId, orgId: ctx.org.id },
      include: { user: { select: { id: true, name: true, email: true } } },
    });
    if (!target) throw new AuthError(404, "Member not found");

    if (target.role === "OWNER") {
      if (ctx.role !== "OWNER") {
        return fail(403, "Only an owner can remove an owner.");
      }
      const owners = await db.membership.count({
        where: { orgId: ctx.org.id, role: "OWNER" },
      });
      if (owners <= 1) {
        return fail(400, "You cannot remove the last owner of this workspace.");
      }
    }

    await db.membership.delete({ where: { id: target.id } });
    await recordActivity(
      ctx.org.id,
      "member.removed",
      { memberEmail: target.user.email, role: target.role },
      ctx.user.id
    );
    return ok({ removed: true, self: target.userId === ctx.user.id });
  });
}
