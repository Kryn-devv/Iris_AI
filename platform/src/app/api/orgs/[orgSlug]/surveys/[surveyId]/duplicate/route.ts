import { Prisma } from "@prisma/client";
import { api, ok, fail } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import { parseCondition } from "@/components/surveys/types";

type Ctx = { params: Promise<{ orgSlug: string; surveyId: string }> };

/** Clone a survey (and its questions) as a fresh DRAFT named "… (copy)". */
export async function POST(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, surveyId } = await params;
    const { org, user } = await requireOrg(orgSlug, "MEMBER");

    const source = await db.survey.findFirst({
      where: { id: surveyId, orgId: org.id },
      include: { questions: { orderBy: { order: "asc" } } },
    });
    if (!source) return fail(404, "Survey not found");

    const copy = await db.$transaction(async (tx) => {
      const survey = await tx.survey.create({
        data: {
          orgId: org.id,
          name: `${source.name} (copy)`.slice(0, 200),
          description: source.description,
          status: "DRAFT",
          startsAt: source.startsAt,
          endsAt: source.endsAt,
          allowMultipleResponses: source.allowMultipleResponses,
          audience:
            source.audience === null
              ? Prisma.JsonNull
              : (source.audience as Prisma.InputJsonValue),
        },
      });

      // Pass 1: clone questions, remembering old id → new id.
      const idMap = new Map<string, string>();
      for (const q of source.questions) {
        const created = await tx.surveyQuestion.create({
          data: {
            surveyId: survey.id,
            order: q.order,
            kind: q.kind,
            prompt: q.prompt,
            required: q.required,
            options:
              q.options === null
                ? Prisma.JsonNull
                : (q.options as Prisma.InputJsonValue),
          },
          select: { id: true },
        });
        idMap.set(q.id, created.id);
      }
      // Pass 2: rewrite conditions to point at the cloned questions.
      for (const q of source.questions) {
        const cond = parseCondition(q.condition);
        if (!cond) continue;
        const targetId = idMap.get(cond.questionId);
        if (!targetId) continue; // dangling condition in source — drop it
        await tx.surveyQuestion.update({
          where: { id: idMap.get(q.id)! },
          data: { condition: { ...cond, questionId: targetId } },
        });
      }
      return survey;
    });

    await recordActivity(
      org.id,
      "survey.duplicated",
      { surveyId: copy.id, sourceId: source.id, name: copy.name },
      user.id
    );
    return ok({ id: copy.id });
  });
}
