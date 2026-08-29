import { api, ok, fail } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { computeSurveyResults } from "../../helpers";

type Ctx = { params: Promise<{ orgSlug: string; surveyId: string }> };

/** Aggregated results JSON — feeds the results tab refresh + CSV export. */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, surveyId } = await params;
    const { org } = await requireOrg(orgSlug, "VIEWER");
    const survey = await db.survey.findFirst({
      where: { id: surveyId, orgId: org.id },
      include: { questions: { orderBy: { order: "asc" } } },
    });
    if (!survey) return fail(404, "Survey not found");
    const results = await computeSurveyResults(survey);
    return ok({ results });
  });
}
