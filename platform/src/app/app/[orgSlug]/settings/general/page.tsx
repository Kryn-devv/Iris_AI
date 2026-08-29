import { requireOrgPage } from "@/lib/auth/guards";
import { GeneralSettingsForm } from "@/components/settings/general-form";

export const dynamic = "force-dynamic";

export default async function GeneralSettingsPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { org } = await requireOrgPage(orgSlug, "ADMIN");

  return (
    <div className="max-w-2xl">
      <GeneralSettingsForm
        orgSlug={org.slug}
        initial={{
          name: org.name,
          portalEnabled: org.portalEnabled,
          portalHeadline: org.portalHeadline,
          portalIntro: org.portalIntro,
          brandColor: org.brandColor,
        }}
      />
    </div>
  );
}
