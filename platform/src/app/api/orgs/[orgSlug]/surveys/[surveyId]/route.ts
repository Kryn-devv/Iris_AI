import { Prisma } from "@prisma/client";
import type { SurveyStatus } from "@prisma/client";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import { SURVEY_TRANSITIONS } from "@/components/surveys/types";
import {
  SurveyWriteSchema,
  getOrgSurvey,
  replaceQuestions,
  toSurveyDTO,
  validateQuestionInputs,
} from "../helpers";

type Ctx = { params: Promise<{ orgSlug: string; surveyId: string }> };

export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, surveyId } = await params;
    const { org } = await requireOrg(orgSlug, "VIEWER");
    const found = await getOrgSurvey(org.id, surveyId);
    if (!found) return fail(404, "Survey not found");
    return ok({
      survey: toSurveyDTO(found.survey, found.responseCount, found.completedCount),
    });
  });
}

/**
 * Update a survey. Structure (questions) can only be replaced while the
 * survey has zero responses; once responses exist only copy changes
 * (name / description / question prompts) and lifecycle settings are allowed.
 */
export async function PATCH(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, surveyId } = await params;
    const { org, user } = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, SurveyWriteSchema);

    const found = await getOrgSurvey(org.id, surveyId);
    if (!found) return fail(404, "Survey not found");
    const { survey, responseCount } = found;

    if (body.questions && responseCount > 0) {
      return fail(
        409,
        "This survey already has responses — question structure is locked. Only copy edits are allowed."
      );
    }
    if (body.questions) {
      const invalid = validateQuestionInputs(body.questions);
      if (invalid) return fail(400, invalid);
    }

    // Lifecycle transition check
    let nextStatus: SurveyStatus | undefined;
    if (body.status && body.status !== survey.status) {
      const allowed = SURVEY_TRANSITIONS[survey.status];
      if (!allowed.includes(body.status)) {
        return fail(
          400,
          `A ${survey.status.toLowerCase()} survey cannot move to ${body.status.toLowerCase()}`
        );
      }
      const finalQuestionCount = body.questions
        ? body.questions.length
        : survey.questions.length;
      if (body.status === "ACTIVE" && finalQuestionCount === 0) {
        return fail(400, "Add at least one question before activating");
      }
      nextStatus = body.status;
    }

    const startsAt =
      body.startsAt === undefined
        ? undefined
        : body.startsAt === null
          ? null
          : new Date(body.startsAt);
    const endsAt =
      body.endsAt === undefined
        ? undefined
        : body.endsAt === null
          ? null
          : new Date(body.endsAt);
    const effStart = startsAt === undefined ? survey.startsAt : startsAt;
    const effEnd = endsAt === undefined ? survey.endsAt : endsAt;
    if (effStart && effEnd && effEnd <= effStart) {
      return fail(400, "End date must be after the start date");
    }

    // Copy-only prompt edits must target this survey's own questions.
    if (body.promptUpdates && body.promptUpdates.length > 0) {
      const own = new Set(survey.questions.map((q) => q.id));
      if (body.promptUpdates.some((u) => !own.has(u.id))) {
        return fail(400, "Unknown question in prompt updates");
      }
    }

    await db.$transaction(async (tx) => {
      await tx.survey.update({
        where: { id: survey.id },
        data: {
          ...(body.name !== undefined ? { name: body.name } : {}),
          ...(body.description !== undefined
            ? { description: body.description ?? null }
            : {}),
          ...(nextStatus ? { status: nextStatus } : {}),
          ...(startsAt !== undefined ? { startsAt } : {}),
          ...(endsAt !== undefined ? { endsAt } : {}),
          ...(body.allowMultipleResponses !== undefined
            ? { allowMultipleResponses: body.allowMultipleResponses }
            : {}),
          ...(body.audience !== undefined
            ? {
                audience: body.audience
                  ? (body.audience as Prisma.InputJsonValue)
                  : Prisma.JsonNull,
              }
            : {}),
        },
      });
      if (body.questions) {
        await replaceQuestions(tx, survey.id, body.questions);
      }
      if (body.promptUpdates) {
        for (const u of body.promptUpdates) {
          await tx.surveyQuestion.update({
            where: { id: u.id },
            data: { prompt: u.prompt },
          });
        }
      }
    });

    if (nextStatus) {
      await recordActivity(
        org.id,
        "survey.status_changed",
        { surveyId: survey.id, name: body.name ?? survey.name, from: survey.status, to: nextStatus },
        user.id
      );
    }

    const updated = await getOrgSurvey(org.id, surveyId);
    if (!updated) return fail(404, "Survey not found");
    return ok({
      survey: toSurveyDTO(
        updated.survey,
        updated.responseCount,
        updated.completedCount
      ),
    });
  });
}

export async function DELETE(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, surveyId } = await params;
    const { org, user } = await requireOrg(orgSlug, "ADMIN");
    const survey = await db.survey.findFirst({
      where: { id: surveyId, orgId: org.id },
      select: { id: true, name: true },
    });
    if (!survey) return fail(404, "Survey not found");
    await db.survey.delete({ where: { id: survey.id } });
    await recordActivity(
      org.id,
      "survey.deleted",
      { surveyId: survey.id, name: survey.name },
      user.id
    );
    return ok({ deleted: true });
  });
}
