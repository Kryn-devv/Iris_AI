import { api, ok } from "@/lib/api";
import { requireOrg, AuthError } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

type Ctx = { params: Promise<{ orgSlug: string; inviteId: string }> };

/** DELETE /api/orgs/[orgSlug]/invites/[inviteId] — revoke an invite (ADMIN+). */
export async function DELETE(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, inviteId } = await params;
    const ctx = await requireOrg(orgSlug, "ADMIN");

    const invite = await db.invite.findFirst({
      where: { id: inviteId, orgId: ctx.org.id },
    });
    if (!invite) throw new AuthError(404, "Invite not found");

    await db.invite.delete({ where: { id: invite.id } });
    await recordActivity(
      ctx.org.id,
      "invite.revoked",
      { email: invite.email },
      ctx.user.id
    );
    return ok({ revoked: true });
  });
}
