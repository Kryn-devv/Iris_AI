import { requireOrgPage } from "@/lib/auth/guards";
import { ProfileForm, PasswordForm } from "@/components/settings/profile-forms";

export const dynamic = "force-dynamic";

export default async function ProfileSettingsPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug);

  return (
    <div className="max-w-2xl space-y-6">
      <ProfileForm
        initialName={ctx.user.name}
        initialAvatarUrl={ctx.user.avatarUrl}
      />
      <PasswordForm />
    </div>
  );
}
