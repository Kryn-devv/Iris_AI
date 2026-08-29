import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { requireOrgPage } from "@/lib/auth/guards";
import { PageHeader } from "@/components/ui/misc";
import { SurveyBuilder } from "@/components/surveys/builder";

export const dynamic = "force-dynamic";

export default async function NewSurveyPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  await requireOrgPage(orgSlug, "MEMBER");

  return (
    <div className="mx-auto max-w-3xl">
      <Link
        href={`/app/${orgSlug}/surveys`}
        className="mb-4 inline-flex items-center gap-1.5 text-xs text-ink-muted transition-colors hover:text-ink"
      >
        <ArrowLeft size={13} aria-hidden />
        All surveys
      </Link>
      <PageHeader
        title="New survey"
        description="Name it, add questions, then activate when you're ready. It is created as a draft."
      />
      <SurveyBuilder orgSlug={orgSlug} />
    </div>
  );
}
