import { redirect } from "next/navigation";
import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";

export const dynamic = "force-dynamic";

/** /settings — land admins on General, everyone else on Profile. */
export default async function SettingsIndexPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug);
  redirect(
    roleAtLeast(ctx.role, "ADMIN")
      ? `/app/${orgSlug}/settings/general`
      : `/app/${orgSlug}/settings/profile`
  );
}
