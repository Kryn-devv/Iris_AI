import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

type Ctx = { params: Promise<{ orgSlug: string }> };

const INVITE_TTL_DAYS = 7;

function invitePath(token: string): string {
  return `/invite/${token}`;
}

function inviteUrl(token: string): string {
  const base = (process.env.NEXT_PUBLIC_APP_URL ?? "").replace(/\/$/, "");
  return `${base}${invitePath(token)}`;
}

/** GET /api/orgs/[orgSlug]/invites — pending (unaccepted) invites (ADMIN+). */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug, "ADMIN");

    const invites = await db.invite.findMany({
      where: { orgId: org.id, acceptedAt: null },
      orderBy: { createdAt: "desc" },
    });

    return ok({
      invites: invites.map((i) => ({
        id: i.id,
        email: i.email,
        role: i.role,
        expiresAt: i.expiresAt.toISOString(),
        createdAt: i.createdAt.toISOString(),
        url: inviteUrl(i.token),
        path: invitePath(i.token),
      })),
    });
  });
}

const CreateBody = z.object({
  email: z.string().trim().toLowerCase().email("Enter a valid email").max(200),
  role: z.enum(["VIEWER", "MEMBER", "ADMIN", "OWNER"]).default("MEMBER"),
});

/**
 * POST /api/orgs/[orgSlug]/invites — create an invite link (ADMIN+; only an
 * OWNER can invite as OWNER). No email is sent — the link is shown to copy.
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const ctx = await requireOrg(orgSlug, "ADMIN");
    const body = await parseBody(req, CreateBody);

    if (body.role === "OWNER" && ctx.role !== "OWNER") {
      return fail(403, "Only an owner can invite someone as an owner.");
    }

    // Already a member?
    const existingMember = await db.membership.findFirst({
      where: { orgId: ctx.org.id, user: { email: body.email } },
      select: { id: true },
    });
    if (existingMember) {
      return fail(400, "That person is already a member of this workspace.");
    }

    // Reuse a live pending invite for the same email instead of stacking them.
    const pending = await db.invite.findFirst({
      where: {
        orgId: ctx.org.id,
        email: body.email,
        acceptedAt: null,
        expiresAt: { gt: new Date() },
      },
    });
    if (pending) {
      const invite =
        pending.role === body.role
          ? pending
          : await db.invite.update({
              where: { id: pending.id },
              data: { role: body.role },
            });
      return ok({
        invite: {
          id: invite.id,
          email: invite.email,
          role: invite.role,
          expiresAt: invite.expiresAt.toISOString(),
          createdAt: invite.createdAt.toISOString(),
          url: inviteUrl(invite.token),
          path: invitePath(invite.token),
        },
        reused: true,
      });
    }

    const invite = await db.invite.create({
      data: {
        orgId: ctx.org.id,
        email: body.email,
        role: body.role,
        expiresAt: new Date(Date.now() + INVITE_TTL_DAYS * 86400_000),
      },
    });

    await recordActivity(
      ctx.org.id,
      "invite.created",
      { email: invite.email, role: invite.role },
      ctx.user.id
    );

    return ok(
      {
        invite: {
          id: invite.id,
          email: invite.email,
          role: invite.role,
          expiresAt: invite.expiresAt.toISOString(),
          createdAt: invite.createdAt.toISOString(),
          url: inviteUrl(invite.token),
          path: invitePath(invite.token),
        },
        reused: false,
      },
      { status: 201 }
    );
  });
}
