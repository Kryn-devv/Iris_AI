import type { QuestionKind, SurveyStatus } from "@prisma/client";

/**
 * Shared survey domain types + pure helpers used by the builder, the public
 * take flow, and the API layer. No server imports — safe in client components.
 */

// ---------------------------------------------------------------------------
// JSON column shapes (schema.prisma: Survey.audience, SurveyQuestion.options,
// SurveyQuestion.condition, SurveyAnswer.value)
// ---------------------------------------------------------------------------

export type SurveyAudience = {
  segment: "all" | "members";
  urlContains?: string;
};

export type QuestionCondition = {
  /** id of an EARLIER question this one depends on. */
  questionId: string;
  /** show when the referenced choice answer equals (or includes) this value */
  equals?: string;
  /** show when the referenced NPS/RATING answer is <= this number */
  lte?: number;
};

export type AnswerValue = number | string | string[];

// ---------------------------------------------------------------------------
// DTOs passed between server pages / API routes and client components
// ---------------------------------------------------------------------------

export type SurveyQuestionDTO = {
  id: string;
  order: number;
  kind: QuestionKind;
  prompt: string;
  required: boolean;
  choices: string[];
  condition: QuestionCondition | null;
};

export type SurveyDTO = {
  id: string;
  name: string;
  description: string | null;
  status: SurveyStatus;
  audience: SurveyAudience;
  startsAt: string | null; // ISO
  endsAt: string | null; // ISO
  allowMultipleResponses: boolean;
  createdAt: string;
  questions: SurveyQuestionDTO[];
  responseCount: number;
  completedCount: number;
};

// ---------------------------------------------------------------------------
// Display metadata
// ---------------------------------------------------------------------------

export const QUESTION_KINDS: QuestionKind[] = [
  "NPS",
  "RATING",
  "SINGLE_CHOICE",
  "MULTIPLE_CHOICE",
  "OPEN_TEXT",
];

export const QUESTION_KIND_META: Record<
  QuestionKind,
  { label: string; hint: string }
> = {
  NPS: { label: "NPS (0–10)", hint: "How likely are you to recommend…" },
  RATING: { label: "Rating (1–5 stars)", hint: "Star rating from 1 to 5" },
  SINGLE_CHOICE: { label: "Single choice", hint: "Pick exactly one option" },
  MULTIPLE_CHOICE: { label: "Multiple choice", hint: "Pick any that apply" },
  OPEN_TEXT: { label: "Open text", hint: "Free-form written answer" },
};

export function isChoiceKind(kind: QuestionKind): boolean {
  return kind === "SINGLE_CHOICE" || kind === "MULTIPLE_CHOICE";
}

export function isScaleKind(kind: QuestionKind): boolean {
  return kind === "NPS" || kind === "RATING";
}

/** Allowed lifecycle transitions (current → nexts). */
export const SURVEY_TRANSITIONS: Record<SurveyStatus, SurveyStatus[]> = {
  DRAFT: ["ACTIVE"],
  ACTIVE: ["PAUSED", "COMPLETED"],
  PAUSED: ["ACTIVE", "COMPLETED"],
  COMPLETED: [],
};

// ---------------------------------------------------------------------------
// JSON parsing (defensive: DB JSON columns are untyped)
// ---------------------------------------------------------------------------

export function parseAudience(json: unknown): SurveyAudience {
  if (json && typeof json === "object" && !Array.isArray(json)) {
    const j = json as Record<string, unknown>;
    const segment = j.segment === "members" ? "members" : "all";
    const urlContains =
      typeof j.urlContains === "string" && j.urlContains.trim()
        ? j.urlContains.trim()
        : undefined;
    return urlContains ? { segment, urlContains } : { segment };
  }
  return { segment: "all" };
}

export function parseChoices(json: unknown): string[] {
  if (json && typeof json === "object" && !Array.isArray(json)) {
    const raw = (json as Record<string, unknown>).choices;
    if (Array.isArray(raw)) {
      return raw.filter((c): c is string => typeof c === "string" && c.length > 0);
    }
  }
  return [];
}

export function parseCondition(json: unknown): QuestionCondition | null {
  if (json && typeof json === "object" && !Array.isArray(json)) {
    const j = json as Record<string, unknown>;
    if (typeof j.questionId !== "string" || !j.questionId) return null;
    const cond: QuestionCondition = { questionId: j.questionId };
    if (typeof j.equals === "string") cond.equals = j.equals;
    if (typeof j.lte === "number" && Number.isFinite(j.lte)) cond.lte = j.lte;
    if (cond.equals === undefined && cond.lte === undefined) return null;
    return cond;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Conditional visibility — the one true implementation, used by the public
// take flow (client) and by server-side answer validation.
// ---------------------------------------------------------------------------

export function isQuestionVisible(
  question: Pick<SurveyQuestionDTO, "condition">,
  answers: Record<string, AnswerValue | undefined>
): boolean {
  const cond = question.condition;
  if (!cond) return true;
  const answer = answers[cond.questionId];
  if (answer === undefined || answer === null) return false;
  if (cond.equals !== undefined) {
    if (Array.isArray(answer)) return answer.includes(cond.equals);
    return answer === cond.equals;
  }
  if (cond.lte !== undefined) {
    return typeof answer === "number" && answer <= cond.lte;
  }
  return true;
}

/** True when an answer value counts as "answered" for the given kind. */
export function isAnswered(
  kind: QuestionKind,
  value: AnswerValue | undefined
): boolean {
  if (value === undefined || value === null) return false;
  switch (kind) {
    case "NPS":
    case "RATING":
      return typeof value === "number" && Number.isFinite(value);
    case "SINGLE_CHOICE":
      return typeof value === "string" && value.length > 0;
    case "MULTIPLE_CHOICE":
      return Array.isArray(value) && value.length > 0;
    case "OPEN_TEXT":
      return typeof value === "string" && value.trim().length > 0;
  }
}

// ---------------------------------------------------------------------------
// Results payload (results API + results tab share this shape)
// ---------------------------------------------------------------------------

export type QuestionResults = {
  id: string;
  kind: QuestionKind;
  prompt: string;
  order: number;
  answered: number;
  nps?: {
    score: number;
    promoters: number;
    passives: number;
    detractors: number;
    distribution: number[]; // index 0..10
  };
  rating?: {
    average: number;
    histogram: number[]; // index 0 => 1 star … index 4 => 5 stars
  };
  choices?: { label: string; count: number; pct: number }[];
  text?: {
    answers: { value: string; at: string }[]; // latest 50
    keywords: string[];
  };
};

export type SurveyResultsPayload = {
  survey: { id: string; name: string };
  totals: { responses: number; completed: number; completionRate: number };
  timeline: { date: string; label: string; count: number }[];
  questions: QuestionResults[];
  rows: {
    responseId: string;
    respondent: string;
    startedAt: string;
    completedAt: string | null;
    answers: Record<string, AnswerValue>;
  }[];
};
