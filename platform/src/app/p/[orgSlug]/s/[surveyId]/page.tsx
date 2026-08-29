import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, CheckCircle2, LogIn } from "lucide-react";
import { db } from "@/lib/db";
import { getPublicOrg } from "@/lib/auth/guards";
import { getCurrentUser, getGuestId } from "@/lib/auth/session";
import { parseAudience } from "@/components/surveys/types";
import { toQuestionDTO } from "../../../../api/orgs/[orgSlug]/surveys/helpers";
import { SurveyTakeFlow } from "@/components/surveys/take-flow";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ orgSlug: string; surveyId: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { orgSlug, surveyId } = await params;
  const org = await getPublicOrg(orgSlug);
  if (!org) return { title: "Survey" };
  const survey = await db.survey.findFirst({
    where: { id: surveyId, orgId: org.id, status: "ACTIVE" },
    select: { name: true },
  });
  return { title: survey ? survey.name : "Survey" };
}

export default async function PublicSurveyPage({ params }: Props) {
  const { orgSlug, surveyId } = await params;
  const org = await getPublicOrg(orgSlug);
  if (!org) notFound();

  const survey = await db.survey.findFirst({
    where: { id: surveyId, orgId: org.id },
    include: { questions: { orderBy: { order: "asc" } } },
  });
  if (!survey || survey.status !== "ACTIVE") notFound();

  const now = new Date();
  if (survey.startsAt && now < survey.startsAt) notFound();
  if (survey.endsAt && now > survey.endsAt) notFound();
  if (survey.questions.length === 0) notFound();

  const user = await getCurrentUser();
  const audience = parseAudience(survey.audience);

  // Members-only surveys politely ask guests to sign in instead of 404ing.
  if (audience.segment === "members" && !user) {
    return (
      <TerminalCard
        icon={<LogIn size={30} aria-hidden className="text-ink-faint" />}
        title="This survey is for signed-in members"
        body={`${org.name} is asking its members directly — sign in and come back to this link to answer.`}
        orgSlug={orgSlug}
        extra={
          <Link
            href="/login"
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-medium text-white shadow-glow transition-colors hover:bg-accent-strong"
          >
            Sign in
          </Link>
        }
      />
    );
  }

  // One response per person unless the survey allows repeats.
  if (!survey.allowMultipleResponses) {
    const guestId = user ? null : await getGuestId();
    const existing =
      user || guestId
        ? await db.surveyResponse.findFirst({
            where: {
              surveyId: survey.id,
              ...(user ? { userId: user.id } : { guestId: guestId! }),
            },
            select: { id: true },
          })
        : null;
    if (existing) {
      return (
        <TerminalCard
          icon={<CheckCircle2 size={30} aria-hidden className="text-success" />}
          title="You already answered this survey"
          body="Thanks — your response was recorded, and this survey accepts one response per person."
          orgSlug={orgSlug}
        />
      );
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <SurveyTakeFlow
        orgSlug={orgSlug}
        surveyId={survey.id}
        name={survey.name}
        description={survey.description}
        questions={survey.questions.map(toQuestionDTO)}
      />
    </div>
  );
}

function TerminalCard({
  icon,
  title,
  body,
  orgSlug,
  extra,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  orgSlug: string;
  extra?: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-2xl">
      <div className="glass flex flex-col items-center gap-3 rounded-2xl border border-line p-8 text-center">
        {icon}
        <h2 className="font-display text-xl font-semibold tracking-tight text-ink">
          {title}
        </h2>
        <p className="max-w-md text-sm leading-relaxed text-ink-muted">{body}</p>
        <div className="mt-2 flex items-center gap-3">
          {extra}
          <Link
            href={`/p/${orgSlug}`}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line-strong px-4 text-sm font-medium text-ink transition-colors hover:bg-surface-overlay"
          >
            <ArrowLeft size={14} aria-hidden />
            Back to the portal
          </Link>
        </div>
      </div>
    </div>
  );
}
