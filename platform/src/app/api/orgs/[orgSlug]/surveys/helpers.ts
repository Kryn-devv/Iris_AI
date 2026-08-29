import "server-only";
import { z } from "zod";
import { format, startOfDay, subDays } from "date-fns";
import { Prisma } from "@prisma/client";
import type {
  QuestionKind,
  Survey,
  SurveyQuestion,
} from "@prisma/client";
import { db } from "@/lib/db";
import { extractKeywords } from "@/lib/ai/heuristic";
import {
  isChoiceKind,
  isQuestionVisible,
  isScaleKind,
  parseAudience,
  parseChoices,
  parseCondition,
  type AnswerValue,
  type QuestionResults,
  type SurveyDTO,
  type SurveyQuestionDTO,
  type SurveyResultsPayload,
} from "@/components/surveys/types";

// ---------------------------------------------------------------------------
// Zod schemas for the surveys API
// ---------------------------------------------------------------------------

const KindSchema = z.enum([
  "NPS",
  "RATING",
  "SINGLE_CHOICE",
  "MULTIPLE_CHOICE",
  "OPEN_TEXT",
]);

export const AudienceSchema = z.object({
  segment: z.enum(["all", "members"]),
  urlContains: z.string().trim().max(300).optional(),
});

const ConditionInputSchema = z
  .object({
    /** builder-local key of an EARLIER question in the same payload */
    key: z.string().min(1).max(64),
    equals: z.string().min(1).max(300).optional(),
    lte: z.number().int().min(0).max(10).optional(),
  })
  .refine((c) => (c.equals !== undefined) !== (c.lte !== undefined), {
    message: "Condition must set exactly one of equals / lte",
  });

export const QuestionInputSchema = z.object({
  key: z.string().min(1).max(64),
  kind: KindSchema,
  prompt: z.string().trim().min(1).max(500),
  required: z.boolean(),
  choices: z.array(z.string().trim().min(1).max(300)).max(20).optional(),
  condition: ConditionInputSchema.nullable().optional(),
});
export type QuestionInput = z.infer<typeof QuestionInputSchema>;

/** Loose datetime: accepts ISO strings from `new Date().toISOString()`. */
const DateStringSchema = z
  .string()
  .refine((s) => !Number.isNaN(new Date(s).getTime()), {
    message: "Invalid date",
  });

export const SurveyWriteSchema = z.object({
  name: z.string().trim().min(1).max(200).optional(),
  description: z.string().trim().max(2000).nullable().optional(),
  status: z.enum(["DRAFT", "ACTIVE", "PAUSED", "COMPLETED"]).optional(),
  startsAt: DateStringSchema.nullable().optional(),
  endsAt: DateStringSchema.nullable().optional(),
  allowMultipleResponses: z.boolean().optional(),
  audience: AudienceSchema.nullable().optional(),
  /** Full question replacement — only allowed while the survey has no responses. */
  questions: z.array(QuestionInputSchema).max(50).optional(),
  /** Copy-only edits — the escape hatch once responses exist. */
  promptUpdates: z
    .array(
      z.object({
        id: z.string().min(1),
        prompt: z.string().trim().min(1).max(500),
      })
    )
    .max(50)
    .optional(),
});
export type SurveyWriteInput = z.infer<typeof SurveyWriteSchema>;

/**
 * Cross-field validation of a question payload. Returns an error message or
 * null. Rules: choice kinds need >= 2 choices; conditions may only reference
 * an EARLIER question; `equals` needs a choice target holding that choice;
 * `lte` needs an NPS/RATING target.
 */
export function validateQuestionInputs(questions: QuestionInput[]): string | null {
  const seen = new Set<string>();
  for (let i = 0; i < questions.length; i++) {
    const q = questions[i]!;
    if (seen.has(q.key)) return `Duplicate question key "${q.key}"`;
    if (isChoiceKind(q.kind)) {
      const choices = (q.choices ?? []).filter(Boolean);
      if (choices.length < 2) {
        return `Question ${i + 1} needs at least two options`;
      }
      if (new Set(choices).size !== choices.length) {
        return `Question ${i + 1} has duplicate options`;
      }
    }
    if (q.condition) {
      const target = questions
        .slice(0, i)
        .find((p) => p.key === q.condition!.key);
      if (!target) {
        return `Question ${i + 1}: condition must reference an earlier question`;
      }
      if (q.condition.equals !== undefined) {
        if (!isChoiceKind(target.kind)) {
          return `Question ${i + 1}: "equals" conditions need a choice question`;
        }
        if (!(target.choices ?? []).includes(q.condition.equals)) {
          return `Question ${i + 1}: condition option no longer exists`;
        }
      }
      if (q.condition.lte !== undefined) {
        if (!isScaleKind(target.kind)) {
          return `Question ${i + 1}: "at most" conditions need an NPS or rating question`;
        }
        const max = target.kind === "NPS" ? 10 : 5;
        const min = target.kind === "NPS" ? 0 : 1;
        if (q.condition.lte < min || q.condition.lte > max) {
          return `Question ${i + 1}: threshold out of range`;
        }
      }
    }
    seen.add(q.key);
  }
  return null;
}

/**
 * Replace a survey's questions inside a transaction. Two passes: create all
 * questions (building key → id), then attach conditions with real ids.
 * Only ever called when the survey has zero responses (no answers to lose).
 */
export async function replaceQuestions(
  tx: Prisma.TransactionClient,
  surveyId: string,
  questions: QuestionInput[]
): Promise<void> {
  await tx.surveyQuestion.deleteMany({ where: { surveyId } });
  const idByKey = new Map<string, string>();
  for (let i = 0; i < questions.length; i++) {
    const q = questions[i]!;
    const created = await tx.surveyQuestion.create({
      data: {
        surveyId,
        order: i,
        kind: q.kind,
        prompt: q.prompt,
        required: q.required,
        options: isChoiceKind(q.kind)
          ? { choices: (q.choices ?? []).filter(Boolean) }
          : Prisma.JsonNull,
      },
      select: { id: true },
    });
    idByKey.set(q.key, created.id);
  }
  for (const q of questions) {
    if (!q.condition) continue;
    const targetId = idByKey.get(q.condition.key);
    if (!targetId) continue; // validated earlier — defensive
    await tx.surveyQuestion.update({
      where: { id: idByKey.get(q.key)! },
      data: {
        condition: {
          questionId: targetId,
          ...(q.condition.equals !== undefined
            ? { equals: q.condition.equals }
            : {}),
          ...(q.condition.lte !== undefined ? { lte: q.condition.lte } : {}),
        },
      },
    });
  }
}

// ---------------------------------------------------------------------------
// Serialization
// ---------------------------------------------------------------------------

export function toQuestionDTO(q: SurveyQuestion): SurveyQuestionDTO {
  return {
    id: q.id,
    order: q.order,
    kind: q.kind,
    prompt: q.prompt,
    required: q.required,
    choices: parseChoices(q.options),
    condition: parseCondition(q.condition),
  };
}

export function toSurveyDTO(
  survey: Survey & { questions: SurveyQuestion[] },
  responseCount: number,
  completedCount: number
): SurveyDTO {
  return {
    id: survey.id,
    name: survey.name,
    description: survey.description,
    status: survey.status,
    audience: parseAudience(survey.audience),
    startsAt: survey.startsAt ? survey.startsAt.toISOString() : null,
    endsAt: survey.endsAt ? survey.endsAt.toISOString() : null,
    allowMultipleResponses: survey.allowMultipleResponses,
    createdAt: survey.createdAt.toISOString(),
    questions: [...survey.questions]
      .sort((a, b) => a.order - b.order)
      .map(toQuestionDTO),
    responseCount,
    completedCount,
  };
}

/** Fetch an org-scoped survey with ordered questions + response counts. */
export async function getOrgSurvey(orgId: string, surveyId: string) {
  const survey = await db.survey.findFirst({
    where: { id: surveyId, orgId },
    include: { questions: { orderBy: { order: "asc" } } },
  });
  if (!survey) return null;
  const [responseCount, completedCount] = await Promise.all([
    db.surveyResponse.count({ where: { surveyId: survey.id } }),
    db.surveyResponse.count({
      where: { surveyId: survey.id, completedAt: { not: null } },
    }),
  ]);
  return { survey, responseCount, completedCount };
}

// ---------------------------------------------------------------------------
// Results aggregation (results API route + results tab both use this)
// ---------------------------------------------------------------------------

type ResponseWithAnswers = {
  id: string;
  createdAt: Date;
  completedAt: Date | null;
  user: { name: string; email: string } | null;
  guestId: string | null;
  answers: { questionId: string; value: unknown }[];
};

const TIMELINE_DAYS = 30;

export async function computeSurveyResults(
  survey: Survey & { questions: SurveyQuestion[] }
): Promise<SurveyResultsPayload> {
  const responses: ResponseWithAnswers[] = await db.surveyResponse.findMany({
    where: { surveyId: survey.id },
    orderBy: { createdAt: "asc" },
    take: 5000,
    select: {
      id: true,
      createdAt: true,
      completedAt: true,
      guestId: true,
      user: { select: { name: true, email: true } },
      answers: { select: { questionId: true, value: true } },
    },
  });

  const completed = responses.filter((r) => r.completedAt !== null);
  const totals = {
    responses: responses.length,
    completed: completed.length,
    completionRate:
      responses.length === 0
        ? 0
        : Math.round((completed.length / responses.length) * 100),
  };

  // Response timeline: last 30 days, empty days included.
  const today = startOfDay(new Date());
  const byDay = new Map<string, number>();
  for (const r of responses) {
    const key = format(startOfDay(r.createdAt), "yyyy-MM-dd");
    byDay.set(key, (byDay.get(key) ?? 0) + 1);
  }
  const timeline: SurveyResultsPayload["timeline"] = [];
  for (let i = TIMELINE_DAYS - 1; i >= 0; i--) {
    const day = subDays(today, i);
    const key = format(day, "yyyy-MM-dd");
    timeline.push({
      date: key,
      label: format(day, "MMM d"),
      count: byDay.get(key) ?? 0,
    });
  }

  const questions = [...survey.questions].sort((a, b) => a.order - b.order);
  const questionResults: QuestionResults[] = questions.map((q) => {
    const values: { value: unknown; at: Date }[] = [];
    for (const r of completed) {
      const a = r.answers.find((x) => x.questionId === q.id);
      if (a && a.value !== null && a.value !== undefined) {
        values.push({ value: a.value, at: r.completedAt ?? r.createdAt });
      }
    }
    const base: QuestionResults = {
      id: q.id,
      kind: q.kind,
      prompt: q.prompt,
      order: q.order,
      answered: values.length,
    };
    switch (q.kind) {
      case "NPS": {
        const nums = values
          .map((v) => v.value)
          .filter((v): v is number => typeof v === "number")
          .map((n) => Math.round(n))
          .filter((n) => n >= 0 && n <= 10);
        const distribution = Array.from({ length: 11 }, () => 0);
        for (const n of nums) distribution[n] = (distribution[n] ?? 0) + 1;
        const promoters = nums.filter((n) => n >= 9).length;
        const passives = nums.filter((n) => n >= 7 && n <= 8).length;
        const detractors = nums.filter((n) => n <= 6).length;
        const score =
          nums.length === 0
            ? 0
            : Math.round(((promoters - detractors) / nums.length) * 100);
        base.answered = nums.length;
        base.nps = { score, promoters, passives, detractors, distribution };
        break;
      }
      case "RATING": {
        const nums = values
          .map((v) => v.value)
          .filter((v): v is number => typeof v === "number")
          .map((n) => Math.round(n))
          .filter((n) => n >= 1 && n <= 5);
        const histogram = Array.from({ length: 5 }, () => 0);
        for (const n of nums) histogram[n - 1] = (histogram[n - 1] ?? 0) + 1;
        const average =
          nums.length === 0
            ? 0
            : Math.round((nums.reduce((s, n) => s + n, 0) / nums.length) * 10) /
              10;
        base.answered = nums.length;
        base.rating = { average, histogram };
        break;
      }
      case "SINGLE_CHOICE":
      case "MULTIPLE_CHOICE": {
        const choices = parseChoices(q.options);
        const counts = new Map<string, number>(choices.map((c) => [c, 0]));
        let answered = 0;
        for (const v of values) {
          const picked = Array.isArray(v.value)
            ? v.value.filter((x): x is string => typeof x === "string")
            : typeof v.value === "string"
              ? [v.value]
              : [];
          if (picked.length === 0) continue;
          answered++;
          for (const p of picked) {
            if (counts.has(p)) counts.set(p, (counts.get(p) ?? 0) + 1);
          }
        }
        base.answered = answered;
        base.choices = choices.map((label) => ({
          label,
          count: counts.get(label) ?? 0,
          pct:
            answered === 0
              ? 0
              : Math.round(((counts.get(label) ?? 0) / answered) * 100),
        }));
        break;
      }
      case "OPEN_TEXT": {
        const texts = values
          .filter((v): v is { value: string; at: Date } =>
            typeof v.value === "string" && v.value.trim().length > 0
          )
          .sort((a, b) => b.at.getTime() - a.at.getTime());
        base.answered = texts.length;
        base.text = {
          answers: texts.slice(0, 50).map((t) => ({
            value: t.value,
            at: t.at.toISOString(),
          })),
          keywords: extractKeywords(texts.map((t) => t.value).join("\n"), 10),
        };
        break;
      }
    }
    return base;
  });

  // Flat per-response rows for CSV export.
  const rows: SurveyResultsPayload["rows"] = responses.map((r) => {
    const answers: Record<string, AnswerValue> = {};
    for (const a of r.answers) {
      const v = a.value;
      if (
        typeof v === "number" ||
        typeof v === "string" ||
        (Array.isArray(v) && v.every((x) => typeof x === "string"))
      ) {
        answers[a.questionId] = v as AnswerValue;
      }
    }
    return {
      responseId: r.id,
      respondent: r.user ? r.user.name : "Guest",
      startedAt: r.createdAt.toISOString(),
      completedAt: r.completedAt ? r.completedAt.toISOString() : null,
      answers,
    };
  });

  return {
    survey: { id: survey.id, name: survey.name },
    totals,
    timeline,
    questions: questionResults,
    rows,
  };
}

// ---------------------------------------------------------------------------
// Public take-flow answer validation (used by the public responses API)
// ---------------------------------------------------------------------------

/**
 * Validate a submitted answer set against the survey's questions, honoring
 * conditional visibility. Returns either a cleaned answer map (hidden
 * questions dropped) or an error message.
 */
export function validateSubmission(
  questions: SurveyQuestion[],
  raw: Record<string, unknown>
):
  | { ok: true; answers: { questionId: string; value: AnswerValue }[] }
  | { ok: false; error: string } {
  const ordered = [...questions].sort((a, b) => a.order - b.order);
  const visibleAnswers: Record<string, AnswerValue | undefined> = {};
  const cleaned: { questionId: string; value: AnswerValue }[] = [];

  for (const q of ordered) {
    const dto = toQuestionDTO(q);
    const visible = isQuestionVisible(dto, visibleAnswers);
    if (!visible) continue; // hidden — any submitted value is dropped

    const value = raw[q.id];
    const missing = value === undefined || value === null;
    if (missing) {
      if (q.required) return { ok: false, error: `"${q.prompt}" is required` };
      continue;
    }

    let clean: AnswerValue | null = null;
    switch (q.kind) {
      case "NPS": {
        if (typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 10) {
          clean = value;
        }
        break;
      }
      case "RATING": {
        if (typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 5) {
          clean = value;
        }
        break;
      }
      case "SINGLE_CHOICE": {
        if (typeof value === "string" && dto.choices.includes(value)) {
          clean = value;
        }
        break;
      }
      case "MULTIPLE_CHOICE": {
        if (
          Array.isArray(value) &&
          value.length > 0 &&
          value.every(
            (v): v is string => typeof v === "string" && dto.choices.includes(v)
          )
        ) {
          clean = [...new Set(value)];
        }
        break;
      }
      case "OPEN_TEXT": {
        if (typeof value === "string" && value.trim().length > 0) {
          clean = value.trim().slice(0, 5000);
        }
        break;
      }
    }

    if (clean === null) {
      if (q.required) {
        return { ok: false, error: `Invalid answer for "${q.prompt}"` };
      }
      continue;
    }
    visibleAnswers[q.id] = clean;
    cleaned.push({ questionId: q.id, value: clean });
  }

  return { ok: true, answers: cleaned };
}
