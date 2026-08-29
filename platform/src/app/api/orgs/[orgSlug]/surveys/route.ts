import { Prisma } from "@prisma/client";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import {
  SurveyWriteSchema,
  replaceQuestions,
  toSurveyDTO,
  validateQuestionInputs,
} from "./helpers";

type Ctx = { params: Promise<{ orgSlug: string }> };

/** List surveys with question + response counts. */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug, "VIEWER");
    const surveys = await db.survey.findMany({
      where: { orgId: org.id },
      orderBy: { createdAt: "desc" },
      include: {
        questions: { orderBy: { order: "asc" } },
        _count: { select: { responses: true } },
      },
    });
    const completedCounts = await db.surveyResponse.groupBy({
      by: ["surveyId"],
      where: { survey: { orgId: org.id }, completedAt: { not: null } },
      _count: { _all: true },
    });
    const completedBySurvey = new Map(
      completedCounts.map((c) => [c.surveyId, c._count._all])
    );
    return ok({
      surveys: surveys.map((s) =>
        toSurveyDTO(s, s._count.responses, completedBySurvey.get(s.id) ?? 0)
      ),
    });
  });
}

/** Create a survey (DRAFT unless a valid activation payload is sent). */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org, user } = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, SurveyWriteSchema);

    if (!body.name) return fail(400, "Survey name is required");

    const questions = body.questions ?? [];
    const invalid = validateQuestionInputs(questions);
    if (invalid) return fail(400, invalid);

    const status = body.status ?? "DRAFT";
    if (status !== "DRAFT" && status !== "ACTIVE") {
      return fail(400, "New surveys can only be created as draft or active");
    }
    if (status === "ACTIVE" && questions.length === 0) {
      return fail(400, "Add at least one question before activating");
    }
    const startsAt = body.startsAt ? new Date(body.startsAt) : null;
    const endsAt = body.endsAt ? new Date(body.endsAt) : null;
    if (startsAt && endsAt && endsAt <= startsAt) {
      return fail(400, "End date must be after the start date");
    }

    const created = await db.$transaction(async (tx) => {
      const survey = await tx.survey.create({
        data: {
          orgId: org.id,
          name: body.name!,
          description: body.description ?? null,
          status,
          startsAt,
          endsAt,
          allowMultipleResponses: body.allowMultipleResponses ?? false,
          audience: body.audience
            ? (body.audience as Prisma.InputJsonValue)
            : Prisma.JsonNull,
        },
      });
      await replaceQuestions(tx, survey.id, questions);
      return survey;
    });

    await recordActivity(
      org.id,
      "survey.created",
      { surveyId: created.id, name: created.name },
      user.id
    );
    return ok({ id: created.id });
  });
}
