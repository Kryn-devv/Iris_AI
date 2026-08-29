import Link from "next/link";
import { ClipboardList, ExternalLink, Plus } from "lucide-react";
import { format } from "date-fns";
import { db } from "@/lib/db";
import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { SURVEY_STATUS } from "@/lib/status";
import { SurveyListActions } from "@/components/surveys/list-actions";

export const dynamic = "force-dynamic";

export default async function SurveysPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug);
  const canManage = roleAtLeast(ctx.role, "MEMBER");
  const canDelete = roleAtLeast(ctx.role, "ADMIN");

  const surveys = await db.survey.findMany({
    where: { orgId: ctx.org.id },
    orderBy: { createdAt: "desc" },
    select: {
      id: true,
      name: true,
      status: true,
      createdAt: true,
      startsAt: true,
      endsAt: true,
      _count: { select: { questions: true, responses: true } },
    },
  });
  const completedCounts = await db.surveyResponse.groupBy({
    by: ["surveyId"],
    where: { survey: { orgId: ctx.org.id }, completedAt: { not: null } },
    _count: { _all: true },
  });
  const completedBySurvey = new Map(
    completedCounts.map((c) => [c.surveyId, c._count._all])
  );

  const active = surveys.filter((s) => s.status === "ACTIVE").length;

  return (
    <div>
      <PageHeader
        title="Surveys"
        description={`${surveys.length} survey${surveys.length === 1 ? "" : "s"}${
          active ? ` — ${active} active` : ""
        }. Ask targeted questions, branch on answers, and watch results live.`}
        actions={
          canManage ? (
            <Link
              href={`/app/${orgSlug}/surveys/new`}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-xs font-medium text-white shadow-glow transition-colors hover:bg-accent-strong"
            >
              <Plus size={14} aria-hidden />
              New survey
            </Link>
          ) : undefined
        }
      />

      {surveys.length === 0 ? (
        <EmptyState
          icon={<ClipboardList size={28} aria-hidden />}
          title="No surveys yet"
          description="Build an NPS pulse, a feature poll, or a churn survey — answers land here in real time."
          action={
            canManage ? (
              <Link
                href={`/app/${orgSlug}/surveys/new`}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-medium text-white shadow-glow transition-colors hover:bg-accent-strong"
              >
                <Plus size={14} aria-hidden />
                Create the first survey
              </Link>
            ) : undefined
          }
        />
      ) : (
        <Card>
          <ul className="divide-y divide-line">
            {surveys.map((survey) => {
              const meta = SURVEY_STATUS[survey.status];
              const responses = survey._count.responses;
              const completed = completedBySurvey.get(survey.id) ?? 0;
              const completionRate =
                responses === 0
                  ? null
                  : Math.round((completed / responses) * 100);
              return (
                <li
                  key={survey.id}
                  className="flex flex-wrap items-center gap-3 px-5 py-3.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link
                        href={`/app/${orgSlug}/surveys/${survey.id}`}
                        className="truncate text-sm font-medium text-ink hover:text-accent-soft"
                      >
                        {survey.name}
                      </Link>
                      <Badge tone={meta.tone}>{meta.label}</Badge>
                      {survey.status === "ACTIVE" && (
                        <Link
                          href={`/p/${orgSlug}/s/${survey.id}`}
                          target="_blank"
                          className="inline-flex items-center gap-1 text-[11px] text-ink-faint transition-colors hover:text-accent-soft"
                          aria-label={`Open public link for ${survey.name}`}
                        >
                          <ExternalLink size={11} aria-hidden />
                          public link
                        </Link>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-muted">
                      <span>
                        {survey._count.questions} question
                        {survey._count.questions === 1 ? "" : "s"}
                      </span>
                      <span>
                        {responses} response{responses === 1 ? "" : "s"}
                      </span>
                      {completionRate !== null && (
                        <span>{completionRate}% completion</span>
                      )}
                      {survey.endsAt && (
                        <span>
                          {survey.endsAt < new Date() ? "ended" : "ends"}{" "}
                          {format(survey.endsAt, "MMM d, yyyy")}
                        </span>
                      )}
                      <span className="text-ink-faint">
                        created {format(survey.createdAt, "MMM d, yyyy")}
                      </span>
                    </div>
                  </div>
                  {canManage && (
                    <SurveyListActions
                      orgSlug={orgSlug}
                      surveyId={survey.id}
                      surveyName={survey.name}
                      responseCount={responses}
                      canDelete={canDelete}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </div>
  );
}
