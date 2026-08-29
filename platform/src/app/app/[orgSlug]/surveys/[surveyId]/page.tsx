import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { requireOrgPage } from "@/lib/auth/guards";
import { PageHeader } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SURVEY_STATUS } from "@/lib/status";
import { SurveyBuilder } from "@/components/surveys/builder";
import { SurveyResults } from "@/components/surveys/results";
import {
  computeSurveyResults,
  getOrgSurvey,
  toSurveyDTO,
} from "../../../../api/orgs/[orgSlug]/surveys/helpers";

export const dynamic = "force-dynamic";

export default async function ManageSurveyPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgSlug: string; surveyId: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { orgSlug, surveyId } = await params;
  const { tab } = await searchParams;
  const ctx = await requireOrgPage(orgSlug, "MEMBER");

  const found = await getOrgSurvey(ctx.org.id, surveyId);
  if (!found) notFound();
  const dto = toSurveyDTO(found.survey, found.responseCount, found.completedCount);
  const results = await computeSurveyResults(found.survey);

  const meta = SURVEY_STATUS[dto.status];

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
        title={dto.name}
        description={`${dto.questions.length} question${dto.questions.length === 1 ? "" : "s"} · ${dto.responseCount} response${dto.responseCount === 1 ? "" : "s"}`}
        actions={
          <div className="flex items-center gap-2">
            <Badge tone={meta.tone}>{meta.label}</Badge>
            <Link
              href={`/p/${orgSlug}/s/${dto.id}`}
              target="_blank"
              className="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium text-ink-muted transition-colors hover:bg-surface-overlay hover:text-ink"
            >
              <ExternalLink size={13} aria-hidden />
              Public link
            </Link>
          </div>
        }
      />

      <Tabs defaultValue={tab === "results" ? "results" : "edit"}>
        <TabsList className="mb-4">
          <TabsTrigger value="edit">Edit</TabsTrigger>
          <TabsTrigger value="results">
            Results ({dto.responseCount})
          </TabsTrigger>
        </TabsList>
        <TabsContent value="edit">
          <SurveyBuilder orgSlug={orgSlug} survey={dto} />
        </TabsContent>
        <TabsContent value="results">
          <SurveyResults orgSlug={orgSlug} results={results} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
