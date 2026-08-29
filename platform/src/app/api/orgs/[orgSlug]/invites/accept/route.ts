import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireUser } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

type Ctx = { params: Promise<{ orgSlug: string }> };

const Body = z.object({ token: z.string().min(1) });

/**
 * POST /api/orgs/[orgSlug]/invites/accept — accept an invite by token.
 * Requires a signed-in user (any account may accept, matching the invite
 * email is not enforced) but NOT membership — that is what's being granted.
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const user = await requireUser();
    const body = await parseBody(req, Body);

    const invite = await db.invite.findFirst({
      where: { token: body.token, org: { slug: orgSlug } },
      include: { org: { select: { id: true, slug: true, name: true } } },
    });

    if (!invite || invite.acceptedAt || invite.expiresAt < new Date()) {
      return fail(400, "This invite is invalid, expired, or already used.");
    }

    const existing = await db.membership.findUnique({
      where: { userId_orgId: { userId: user.id, orgId: invite.org.id } },
    });

    if (!existing) {
      await db.membership.create({
        data: { userId: user.id, orgId: invite.org.id, role: invite.role },
      });
      await recordActivity(
        invite.org.id,
        "member.joined",
        { email: user.email, role: invite.role },
        user.id
      );
    }

    await db.invite.update({
      where: { id: invite.id },
      data: { acceptedAt: new Date() },
    });

    return ok({ orgSlug: invite.org.slug, alreadyMember: Boolean(existing) });
  });
}
