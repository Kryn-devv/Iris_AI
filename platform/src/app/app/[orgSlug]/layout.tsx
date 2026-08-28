import { db } from "@/lib/db";
import { requireOrgPage } from "@/lib/auth/guards";
import { AppShell } from "@/components/app/shell";

export const dynamic = "force-dynamic";

export default async function OrgLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug);
  const orgs = await db.membership.findMany({
    where: { userId: ctx.user.id },
    include: { org: { select: { name: true, slug: true } } },
    orderBy: { createdAt: "asc" },
  });

  return (
    <AppShell
      user={ctx.user}
      org={{ name: ctx.org.name, slug: ctx.org.slug }}
      role={ctx.role}
      orgs={orgs.map((m) => ({ name: m.org.name, slug: m.org.slug }))}
    >
      {children}
    </AppShell>
  );
}
