import { api, ok } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";

type Ctx = { params: Promise<{ orgSlug: string }> };

/** GET /api/orgs/[orgSlug]/members — memberships with user details (ADMIN+). */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug, "ADMIN");

    const members = await db.membership.findMany({
      where: { orgId: org.id },
      include: {
        user: {
          select: { id: true, name: true, email: true, avatarUrl: true },
        },
      },
      orderBy: { createdAt: "asc" },
    });

    return ok({
      members: members.map((m) => ({
        id: m.id,
        role: m.role,
        joinedAt: m.createdAt.toISOString(),
        user: m.user,
      })),
    });
  });
}
