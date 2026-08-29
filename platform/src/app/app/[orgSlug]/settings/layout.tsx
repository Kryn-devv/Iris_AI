import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";
import { PageHeader } from "@/components/ui/misc";
import { SettingsNav, type SettingsTab } from "@/components/settings/nav";

export const dynamic = "force-dynamic";

export default async function SettingsLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug);

  const base = `/app/${orgSlug}/settings`;
  const tabs: SettingsTab[] = [];
  if (roleAtLeast(ctx.role, "ADMIN")) {
    tabs.push({ href: `${base}/general`, label: "General" });
    tabs.push({ href: `${base}/members`, label: "Members" });
  }
  tabs.push({ href: `${base}/profile`, label: "Profile" });
  if (ctx.role === "OWNER") {
    tabs.push({ href: `${base}/danger`, label: "Danger zone" });
  }

  return (
    <>
      <PageHeader
        title="Settings"
        description="Workspace configuration, team access, and your profile."
      />
      <SettingsNav tabs={tabs} />
      {children}
    </>
  );
}
