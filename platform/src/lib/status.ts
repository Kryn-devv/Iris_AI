import type { PostStatus, Sentiment, SurveyStatus } from "@prisma/client";
import type { BadgeTone } from "@/components/ui/badge";

/** Canonical display metadata for post statuses — use everywhere. */
export const POST_STATUS: Record<
  PostStatus,
  { label: string; tone: BadgeTone; color: string }
> = {
  OPEN: { label: "Open", tone: "neutral", color: "#9aa2ba" },
  UNDER_CONSIDERATION: {
    label: "Under consideration",
    tone: "warning",
    color: "#facc15",
  },
  PLANNED: { label: "Planned", tone: "accent", color: "#7c6cff" },
  IN_PROGRESS: { label: "In progress", tone: "aurora", color: "#42d6eb" },
  SHIPPED: { label: "Shipped", tone: "success", color: "#4ade80" },
  CLOSED: { label: "Closed", tone: "neutral", color: "#646c86" },
};

/** Statuses that appear as roadmap columns, in order. */
export const ROADMAP_STATUSES: PostStatus[] = [
  "UNDER_CONSIDERATION",
  "PLANNED",
  "IN_PROGRESS",
  "SHIPPED",
];

export const SENTIMENT_META: Record<
  Sentiment,
  { label: string; tone: BadgeTone }
> = {
  POSITIVE: { label: "Positive", tone: "success" },
  NEUTRAL: { label: "Neutral", tone: "neutral" },
  NEGATIVE: { label: "Negative", tone: "danger" },
};

export const SURVEY_STATUS: Record<
  SurveyStatus,
  { label: string; tone: BadgeTone }
> = {
  DRAFT: { label: "Draft", tone: "neutral" },
  ACTIVE: { label: "Active", tone: "success" },
  PAUSED: { label: "Paused", tone: "warning" },
  COMPLETED: { label: "Completed", tone: "accent" },
};

export const CHANGELOG_LABEL_META: Record<
  string,
  { label: string; tone: BadgeTone }
> = {
  NEW: { label: "New", tone: "accent" },
  IMPROVED: { label: "Improved", tone: "aurora" },
  FIXED: { label: "Fixed", tone: "success" },
  DEPRECATED: { label: "Deprecated", tone: "warning" },
  SECURITY: { label: "Security", tone: "danger" },
};
