import { requireOrgPage } from "@/lib/auth/guards";
import { DangerZone } from "@/components/settings/danger-zone";

export const dynamic = "force-dynamic";

export default async function DangerSettingsPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { org } = await requireOrgPage(orgSlug, "OWNER");

  return (
    <div className="max-w-2xl">
      <DangerZone orgSlug={org.slug} orgName={org.name} />
    </div>
  );
}
