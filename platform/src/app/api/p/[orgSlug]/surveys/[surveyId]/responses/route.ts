import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { getPublicOrg } from "@/lib/auth/guards";
import { ensureGuestId, getCurrentUser } from "@/lib/auth/session";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import { parseAudience } from "@/components/surveys/types";
import { validateSubmission } from "../../../../../orgs/[orgSlug]/surveys/helpers";

type Ctx = { params: Promise<{ orgSlug: string; surveyId: string }> };

const Body = z.object({
  /** questionId → raw answer value (number | string | string[]) */
  answers: z.record(z.string(), z.unknown()),
});

/** Public survey submission: creates SurveyResponse + SurveyAnswers. */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, surveyId } = await params;
    const org = await getPublicOrg(orgSlug);
    if (!org) return fail(404, "Not found");

    const survey = await db.survey.findFirst({
      where: { id: surveyId, orgId: org.id },
      include: { questions: { orderBy: { order: "asc" } } },
    });
    if (!survey || survey.status !== "ACTIVE") return fail(404, "Not found");

    const now = new Date();
    if (survey.startsAt && now < survey.startsAt) return fail(404, "Not found");
    if (survey.endsAt && now > survey.endsAt) return fail(404, "Not found");

    // Identity: signed-in user, else stable guest cookie.
    const user = await getCurrentUser();
    const audience = parseAudience(survey.audience);
    if (audience.segment === "members" && !user) {
      return fail(401, "Please sign in to answer this survey");
    }
    const guestId = user ? null : await ensureGuestId();

    if (!survey.allowMultipleResponses) {
      const existing = await db.surveyResponse.findFirst({
        where: {
          surveyId: survey.id,
          ...(user ? { userId: user.id } : { guestId }),
        },
        select: { id: true },
      });
      if (existing) {
        return fail(409, "You have already answered this survey — thank you!");
      }
    }

    const body = await parseBody(req, Body);
    const result = validateSubmission(survey.questions, body.answers);
    if (!result.ok) return fail(400, result.error);
    if (result.answers.length === 0) {
      return fail(400, "Please answer at least one question");
    }

    const response = await db.surveyResponse.create({
      data: {
        surveyId: survey.id,
        userId: user?.id ?? null,
        guestId,
        completedAt: new Date(),
        answers: {
          create: result.answers.map((a) => ({
            questionId: a.questionId,
            value: a.value,
          })),
        },
      },
      select: { id: true },
    });

    await recordActivity(
      org.id,
      "survey.response",
      { surveyId: survey.id, responseId: response.id, name: survey.name },
      user?.id ?? null
    );

    return ok({ id: response.id });
  });
}
