/**
 * All marketing copy for the scroll journey lives here — one file to edit.
 * Both the 3D journey overlays and the static (reduced-motion / no-WebGL)
 * fallback render from this config, so the two experiences never drift.
 */
import { brand } from "@/config/brand";

export type ChapterId =
  | "hero"
  | "chaos"
  | "capture"
  | "analysis"
  | "priorities"
  | "voting"
  | "roadmap"
  | "surveys"
  | "changelog"
  | "finale";

export interface ChapterCta {
  label: string;
  href: string;
  variant: "primary" | "ghost";
}

export interface ChapterStat {
  value: string;
  label: string;
  tone?: "accent" | "aurora" | "ember" | "success";
}

export interface ChapterCopy {
  id: ChapterId;
  /** Scroll-progress window [start, end] in 0..1 — must match timeline.ts. */
  range: [number, number];
  kicker: string;
  headline: string;
  sub: string;
  bullets?: string[];
  stats?: ChapterStat[];
  ctas?: ChapterCta[];
  /** Where the copy block sits over the 3D world on desktop. */
  align: "center" | "left" | "right";
}

export const PRIMARY_CTA: ChapterCta = {
  label: "Start Building Better Products",
  href: "/register",
  variant: "primary",
};

export const SECONDARY_CTA: ChapterCta = {
  label: "Explore the Platform",
  href: "/p/orbit-labs",
  variant: "ghost",
};

export const INTAKE_CHANNELS = [
  "Feedback boards",
  "Feature requests",
  "Guest submissions",
  "In-app widgets",
  "Surveys",
  "Imports",
] as const;

export const CHAPTERS: ChapterCopy[] = [
  {
    id: "hero",
    range: [0, 0.095],
    kicker: brand.category,
    headline: "STOP GUESSING WHAT TO BUILD.",
    sub: brand.subline,
    ctas: [PRIMARY_CTA, SECONDARY_CTA],
    align: "center",
  },
  {
    id: "chaos",
    range: [0.105, 0.205],
    kicker: "Chapter 01 · The chaos",
    headline: "FEEDBACK IS EVERYWHERE.",
    sub: "Emails. Messages. Support tickets. Communities. Surveys. Your users are constantly telling you what they need.",
    align: "left",
  },
  {
    id: "capture",
    range: [0.215, 0.315],
    kicker: "Chapter 02 · Capture",
    headline: "CAPTURE EVERYTHING.",
    sub: "Every channel flows into one intelligent intake. Nothing your users say gets lost again.",
    bullets: [...INTAKE_CHANNELS],
    align: "right",
  },
  {
    id: "analysis",
    range: [0.325, 0.445],
    kicker: "Chapter 03 · AI analysis",
    headline: "TURN NOISE INTO INSIGHT.",
    sub: "AI reads every piece of feedback, detects themes, measures sentiment, and surfaces what your users actually need.",
    stats: [
      { value: "247", label: "feedback items detected", tone: "accent" },
      { value: "TEAM COLLABORATION", label: "top request", tone: "aurora" },
      { value: "87% positive", label: "sentiment", tone: "success" },
      { value: "+42%", label: "growing demand", tone: "ember" },
    ],
    align: "left",
  },
  {
    id: "priorities",
    range: [0.455, 0.545],
    kicker: "Chapter 04 · Prioritization",
    headline: "BUILD WHAT MATTERS.",
    sub: "Stop guessing. Let real user demand guide your roadmap.",
    align: "right",
  },
  {
    id: "voting",
    range: [0.555, 0.645],
    kicker: "Chapter 05 · Voting",
    headline: "EVERY VOTE IS A SIGNAL.",
    sub: "Users rally behind what they need — priorities emerge on their own, in real time.",
    align: "left",
  },
  {
    id: "roadmap",
    range: [0.655, 0.755],
    kicker: "Chapter 06 · Roadmap",
    headline: "SEE WHERE YOU'RE GOING.",
    sub: "Turn product strategy into a transparent journey your users can follow.",
    align: "right",
  },
  {
    id: "surveys",
    range: [0.765, 0.845],
    kicker: "Chapter 07 · Surveys",
    headline: "ASK AT THE RIGHT MOMENT.",
    sub: "NPS, targeted questions, micro-surveys — calm in-product moments your users actually answer.",
    align: "left",
  },
  {
    id: "changelog",
    range: [0.855, 0.925],
    kicker: "Chapter 08 · Changelog",
    headline: "KEEP EVERYONE IN THE LOOP.",
    sub: "Ship it, tell the story, and close the loop with the users who asked for it.",
    align: "right",
  },
  {
    id: "finale",
    range: [0.94, 1],
    kicker: "The universe, organized",
    headline: "STOP GUESSING. START BUILDING.",
    sub: "Understand your users. Prioritize with confidence. Build products people actually want.",
    ctas: [PRIMARY_CTA, SECONDARY_CTA],
    align: "center",
  },
];

/** Feedback snippets rendered as crisp cards in the chaos field. */
export const CHAOS_SNIPPETS: { text: string; source: string }[] = [
  { text: "Please add dark mode.", source: "Email" },
  { text: "I really need team collaboration.", source: "Support ticket" },
  { text: "Can you add API access?", source: "Community" },
  { text: "This feature would save us hours.", source: "In-app widget" },
  { text: "Export to CSV would be huge for us.", source: "Survey" },
  { text: "Any plans for SSO / SAML?", source: "Sales call" },
  { text: "Webhooks, please. We beg you.", source: "Discord" },
  { text: "Mobile keeps logging me out.", source: "Support ticket" },
];

/** Feature cards in the prioritization matrix. score in 0..1 drives height. */
export const MATRIX_FEATURES: {
  name: string;
  votes: number;
  revenue: string;
  confidence: number;
  score: number;
}[] = [
  { name: "Team Collaboration", votes: 2431, revenue: "$48k", confidence: 0.96, score: 0.97 },
  { name: "Dark Mode", votes: 1248, revenue: "$12k", confidence: 0.91, score: 0.78 },
  { name: "API Access", votes: 983, revenue: "$31k", confidence: 0.88, score: 0.7 },
  { name: "Webhooks", votes: 412, revenue: "$9k", confidence: 0.74, score: 0.44 },
  { name: "CSV Export", votes: 305, revenue: "$4k", confidence: 0.69, score: 0.32 },
  { name: "Custom Themes", votes: 86, revenue: "$1k", confidence: 0.4, score: 0.12 },
];

/** Cards in the voting chapter — counters animate up to `votes`. */
export const VOTE_CARDS: { name: string; votes: number }[] = [
  { name: "Team Collaboration", votes: 2431 },
  { name: "Dark Mode", votes: 1248 },
  { name: "API Access", votes: 983 },
];

/** Roadmap stage regions the camera flies through, in order. */
export const ROADMAP_STAGES: { name: string; cards: string[]; hex: string }[] = [
  { name: "NOW", cards: ["Team Collaboration", "Realtime comments"], hex: "#4ade80" },
  { name: "IN PROGRESS", cards: ["Dark Mode", "API Access"], hex: "#42d6eb" },
  { name: "PLANNED", cards: ["Webhooks", "SSO / SAML"], hex: "#9e92ff" },
  { name: "FUTURE", cards: ["Mobile App", "Custom Themes"], hex: "#ff7a59" },
];

/** Survey panels floating past in the calm chapter. */
export const SURVEY_PANELS = [
  {
    kind: "NPS" as const,
    prompt: "How likely are you to recommend us?",
    hint: "0 — not likely · 10 — extremely likely",
  },
  {
    kind: "CHOICE" as const,
    prompt: "What should we build next?",
    choices: ["Team collaboration", "API access", "Dark mode"],
  },
  {
    kind: "TEXT" as const,
    prompt: "What's one thing we could do better?",
    hint: "Open answer",
  },
];

/** Changelog milestones along the timeline. */
export const CHANGELOG_MILESTONES: { version: string; title: string }[] = [
  { version: "v1.0", title: "The Beginning" },
  { version: "v1.5", title: "AI Insights" },
  { version: "v2.0", title: "Public Roadmaps" },
  { version: "v2.5", title: "Smart Surveys" },
  { version: "v3.0", title: "The Future" },
];

export const FOOTER_LINKS: { label: string; href: string }[] = [
  { label: "Portal demo", href: "/p/orbit-labs" },
  { label: "Roadmap", href: "/p/orbit-labs/roadmap" },
  { label: "Changelog", href: "/p/orbit-labs/changelog" },
  { label: "Log in", href: "/login" },
  { label: "Get started", href: "/register" },
];
