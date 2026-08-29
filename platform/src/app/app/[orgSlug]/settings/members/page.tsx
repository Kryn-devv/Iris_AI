import { db } from "@/lib/db";
import { requireOrgPage } from "@/lib/auth/guards";
import {
  MembersTable,
  InvitesPanel,
  type MemberRow,
  type InviteRow,
} from "@/components/settings/members";

export const dynamic = "force-dynamic";

export default async function MembersSettingsPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug, "ADMIN");

  const [memberships, invites] = await Promise.all([
    db.membership.findMany({
      where: { orgId: ctx.org.id },
      include: {
        user: { select: { id: true, name: true, email: true, avatarUrl: true } },
      },
      orderBy: { createdAt: "asc" },
    }),
    db.invite.findMany({
      where: { orgId: ctx.org.id, acceptedAt: null },
      orderBy: { createdAt: "desc" },
    }),
  ]);

  const appUrl = (process.env.NEXT_PUBLIC_APP_URL ?? "").replace(/\/$/, "");

  const memberRows: MemberRow[] = memberships.map((m) => ({
    id: m.id,
    role: m.role,
    joinedAt: m.createdAt.toISOString(),
    user: m.user,
  }));

  const inviteRows: InviteRow[] = invites.map((i) => ({
    id: i.id,
    email: i.email,
    role: i.role,
    expiresAt: i.expiresAt.toISOString(),
    createdAt: i.createdAt.toISOString(),
    url: `${appUrl}/invite/${i.token}`,
    path: `/invite/${i.token}`,
  }));

  return (
    <div className="space-y-6">
      <MembersTable
        orgSlug={orgSlug}
        members={memberRows}
        currentUserId={ctx.user.id}
        currentRole={ctx.role as "OWNER" | "ADMIN"}
      />
      <InvitesPanel
        orgSlug={orgSlug}
        invites={inviteRows}
        currentRole={ctx.role as "OWNER" | "ADMIN"}
      />
    </div>
  );
}
