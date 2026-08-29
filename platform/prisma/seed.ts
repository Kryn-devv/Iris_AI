/**
 * Demo seed for the NOVARIS platform: the "Orbit Labs" organization.
 *
 * Run with `npm run db:seed` (tsx prisma/seed.ts).
 *
 * Idempotent: if the org slug "orbit-labs" already exists the script logs and
 * exits. Set RESET=1 to delete the demo org (cascades clean up children) and
 * its known demo users first, then reseed.
 */

import { PrismaClient, Prisma } from "@prisma/client";
import bcrypt from "bcryptjs";
import {
  analyzeSentiment,
  summarizeText,
  clusterTexts,
} from "../src/lib/ai/heuristic";
import { priorityScore } from "../src/lib/priority";

const db = new PrismaClient();

// ---------------------------------------------------------------------------
// Deterministic PRNG so reseeds look the same.
// ---------------------------------------------------------------------------

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rand = mulberry32(0x0421b17);
const randInt = (min: number, max: number) =>
  Math.floor(rand() * (max - min + 1)) + min;
const pick = <T>(arr: T[]): T => arr[randInt(0, arr.length - 1)];

const NOW = Date.now();
const DAY = 86_400_000;
const daysAgo = (d: number, jitterHours = 20) =>
  new Date(NOW - d * DAY - randInt(0, jitterHours) * 3_600_000 - randInt(0, 3_599_000));

/** Random date in the past 120 days, weighted toward recent weeks. */
function weightedRecentDate(): Date {
  // Squaring the uniform draw pushes mass toward 0 (recent).
  const d = Math.pow(rand(), 2.1) * 120;
  return daysAgo(d, 12);
}

/** Random date between `from` and now. */
function dateSince(from: Date): Date {
  const span = NOW - from.getTime();
  return new Date(from.getTime() + rand() * Math.max(span, 60_000));
}

function slugifyLocal(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

// ---------------------------------------------------------------------------
// Static fixture data
// ---------------------------------------------------------------------------

const DEMO_EMAIL = "demo@novaris.app";
const MIA_EMAIL = "mia@novaris.app";
const ORG_SLUG = "orbit-labs";

const CATEGORIES: { name: string; color: string }[] = [
  { name: "UX & Design", color: "#8b8bf5" },
  { name: "API & Developers", color: "#5eead4" },
  { name: "Performance", color: "#f59e0b" },
  { name: "Collaboration", color: "#f472b6" },
  { name: "Mobile", color: "#60a5fa" },
  { name: "Integrations", color: "#a3e635" },
];

const TAGS: { name: string; color: string }[] = [
  { name: "quick-win", color: "#5eead4" },
  { name: "enterprise", color: "#8b8bf5" },
  { name: "churn-risk", color: "#f87171" },
  { name: "delight", color: "#f472b6" },
  { name: "beta", color: "#60a5fa" },
  { name: "accessibility", color: "#a3e635" },
  { name: "billing", color: "#fbbf24" },
  { name: "security", color: "#94a3b8" },
];

const GUESTS: { name: string; email: string }[] = [
  { name: "Ana Petrova", email: "ana@lumenworks.io" },
  { name: "Ben Okafor", email: "ben@stackline.dev" },
  { name: "Carla Reyes", email: "carla@northwindhq.com" },
  { name: "Dev Sharma", email: "dev@quantic.app" },
  { name: "Elena Fischer", email: "elena@meridianlabs.de" },
  { name: "Felix Tan", email: "felix@harborpoint.co" },
  { name: "Grace Liu", email: "grace@pixelforge.studio" },
  { name: "Hugo Martins", email: "hugo@atlascrm.pt" },
  { name: "Imani Wright", email: "imani@brightpath.org" },
  { name: "Jonas Berg", email: "jonas@fjordanalytics.no" },
];

type PostSpec = {
  title: string;
  body: string;
  type: "FEEDBACK" | "FEATURE_REQUEST";
  status: "OPEN" | "UNDER_CONSIDERATION" | "PLANNED" | "IN_PROGRESS" | "SHIPPED" | "CLOSED";
  source: "DASHBOARD" | "PORTAL" | "WIDGET" | "IMPORT" | "API" | "EMAIL";
  category: string; // category name
  tags?: string[];
  author?: "demo" | "mia"; // otherwise a guest (or anonymous)
  anonymous?: boolean; // guest post without name/email
  votes: number;
  comments?: number; // approximate desired comment count
  impact?: number;
  effort?: number;
  revenueImpact?: number;
  roadmap?: boolean;
  pinned?: boolean;
  daysAgoOverride?: number;
};

// ~55 posts. The clear #1 request is team collaboration & shared workspaces.
const POSTS: PostSpec[] = [
  {
    title: "Team collaboration & shared workspaces",
    body: "We desperately need real team collaboration. Right now every workspace is single-player: my teammates cannot see the boards I set up, and we end up exporting screenshots into Slack. Shared workspaces with roles, mentions and the ability to collaborate on the same roadmap would make Orbit the center of our product process. Almost everyone on our team asks for this weekly.",
    type: "FEATURE_REQUEST",
    status: "PLANNED",
    source: "PORTAL",
    category: "Collaboration",
    tags: ["enterprise", "churn-risk"],
    votes: 180,
    comments: 16,
    impact: 5,
    effort: 4,
    revenueImpact: 120000,
    roadmap: true,
    pinned: true,
    daysAgoOverride: 96,
  },
  {
    title: "Shared team workspaces for agencies",
    body: "As an agency we manage feedback for six clients. We want shared team workspaces where collaborators can be invited per client, with view-only guests. Collaboration is the one thing blocking us from rolling Orbit out to the whole team.",
    type: "FEATURE_REQUEST",
    status: "UNDER_CONSIDERATION",
    source: "EMAIL",
    category: "Collaboration",
    tags: ["enterprise"],
    votes: 84,
    comments: 7,
    impact: 4,
    effort: 3,
    roadmap: true,
    daysAgoOverride: 62,
  },
  {
    title: "Dark mode for the dashboard",
    body: "Please add a proper dark mode. I work late and the bright dashboard is hard on the eyes. A system-follow option plus a manual toggle would be perfect. Every modern tool has this now.",
    type: "FEATURE_REQUEST",
    status: "IN_PROGRESS",
    source: "PORTAL",
    category: "UX & Design",
    tags: ["delight", "quick-win"],
    votes: 142,
    comments: 11,
    impact: 3,
    effort: 2,
    roadmap: true,
    daysAgoOverride: 88,
  },
  {
    title: "Public REST API with API keys",
    body: "We want programmatic access to posts, votes and comments so we can sync feedback into our data warehouse. A documented REST API with per-org API keys and rate limits would be enough for a first version. Webhooks alone are not sufficient for backfills.",
    type: "FEATURE_REQUEST",
    status: "PLANNED",
    source: "PORTAL",
    category: "API & Developers",
    tags: ["enterprise"],
    votes: 97,
    comments: 9,
    impact: 4,
    effort: 3,
    revenueImpact: 45000,
    roadmap: true,
    daysAgoOverride: 74,
  },
  {
    title: "SSO with SAML and Okta",
    body: "Our security team requires SAML SSO before we can expand seats. Okta and Azure AD support would unblock a 300-seat rollout. Happy to be a design partner — this is the last blocker in our procurement checklist.",
    type: "FEATURE_REQUEST",
    status: "UNDER_CONSIDERATION",
    source: "EMAIL",
    category: "Integrations",
    tags: ["enterprise", "security"],
    votes: 76,
    comments: 6,
    impact: 5,
    effort: 4,
    revenueImpact: 90000,
    roadmap: true,
    daysAgoOverride: 55,
  },
  {
    title: "Native mobile app for iOS and Android",
    body: "Reviewing feedback on the go is painful in the mobile browser. A lightweight native app with push notifications for new posts and status changes would be amazing. Even a read-mostly app would help product managers who travel.",
    type: "FEATURE_REQUEST",
    status: "UNDER_CONSIDERATION",
    source: "PORTAL",
    category: "Mobile",
    votes: 68,
    comments: 8,
    impact: 4,
    effort: 5,
    roadmap: true,
    daysAgoOverride: 80,
  },
  {
    title: "Slack integration for new feedback",
    body: "Send new posts and status changes to a Slack channel. Bonus points for being able to reply to a comment thread directly from Slack. Our team lives in Slack and misses feedback that only shows up in the dashboard.",
    type: "FEATURE_REQUEST",
    status: "SHIPPED",
    source: "PORTAL",
    category: "Integrations",
    tags: ["quick-win"],
    votes: 88,
    comments: 7,
    impact: 4,
    effort: 2,
    roadmap: true,
    daysAgoOverride: 105,
  },
  {
    title: "CSV and JSON export of all feedback",
    body: "We need to export posts with votes, tags and statuses to CSV for quarterly board reports. A JSON export for engineers would also be welcome. Right now we copy-paste tables which is error prone.",
    type: "FEATURE_REQUEST",
    status: "SHIPPED",
    source: "DASHBOARD",
    category: "API & Developers",
    tags: ["quick-win"],
    author: "mia",
    votes: 54,
    comments: 5,
    impact: 3,
    effort: 1,
    roadmap: true,
    daysAgoOverride: 98,
  },
  {
    title: "Email notifications for status changes",
    body: "When a post I voted on moves to Planned or Shipped, I want an email. Voters should automatically follow a post. This closes the loop with customers and saves us from answering the same 'any update?' question.",
    type: "FEATURE_REQUEST",
    status: "IN_PROGRESS",
    source: "PORTAL",
    category: "Collaboration",
    votes: 61,
    comments: 6,
    impact: 4,
    effort: 2,
    roadmap: true,
    daysAgoOverride: 47,
  },
  {
    title: "Dashboard is slow with 1,000+ posts",
    body: "Since we imported our backlog the feedback list takes 6-8 seconds to load and filtering feels laggy. Scrolling stutters badly on Firefox. This is becoming a real problem for our weekly triage meeting.",
    type: "FEEDBACK",
    status: "IN_PROGRESS",
    source: "DASHBOARD",
    category: "Performance",
    tags: ["churn-risk"],
    author: "demo",
    votes: 44,
    comments: 9,
    impact: 4,
    effort: 3,
    roadmap: true,
    daysAgoOverride: 21,
  },
  {
    title: "Keyboard shortcuts for triage",
    body: "Let me move through the inbox with j/k, change status with s, and tag with t. Triage would be twice as fast. Superhuman-style shortcuts would be a delight for power users.",
    type: "FEEDBACK",
    status: "PLANNED",
    source: "WIDGET",
    category: "UX & Design",
    tags: ["delight", "quick-win"],
    votes: 39,
    comments: 4,
    impact: 3,
    effort: 2,
    roadmap: true,
    daysAgoOverride: 33,
  },
  {
    title: "Jira two-way sync",
    body: "Link a post to a Jira issue and keep status in sync both ways. When engineering closes the Jira ticket, the post should move to Shipped and notify voters. We currently do this by hand every Friday and it always drifts.",
    type: "FEATURE_REQUEST",
    status: "UNDER_CONSIDERATION",
    source: "PORTAL",
    category: "Integrations",
    tags: ["enterprise"],
    votes: 47,
    comments: 5,
    impact: 4,
    effort: 4,
    revenueImpact: 30000,
    roadmap: true,
    daysAgoOverride: 40,
  },
  {
    title: "Widget breaks layout on narrow screens",
    body: "The embeddable widget overflows its iframe on screens under 360px and the submit button gets cut off. Our mobile users cannot submit feedback at all. This is a bug, not a feature request — it used to work in June.",
    type: "FEEDBACK",
    status: "SHIPPED",
    source: "WIDGET",
    category: "Mobile",
    votes: 28,
    comments: 5,
    impact: 3,
    effort: 1,
    roadmap: true,
    daysAgoOverride: 58,
  },
  {
    title: "Custom statuses and workflow stages",
    body: "Our process has a 'Needs research' stage between consideration and planned. Custom statuses with custom colors would let the roadmap reflect how we actually work instead of forcing us into five fixed buckets.",
    type: "FEATURE_REQUEST",
    status: "UNDER_CONSIDERATION",
    source: "DASHBOARD",
    category: "UX & Design",
    author: "mia",
    votes: 33,
    comments: 3,
    impact: 3,
    effort: 3,
    roadmap: true,
    daysAgoOverride: 36,
  },
  // --- non-roadmap requests & feedback (long tail) ---
  {
    title: "Zapier integration",
    body: "A Zapier app would let us pipe feedback into Airtable and Notion without writing code. Triggers for new post, new vote milestone, and status change would cover 90% of our automations.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "Integrations",
    votes: 26,
    comments: 3,
    impact: 3,
    effort: 2,
  },
  {
    title: "The new insights page is fantastic",
    body: "Just want to say the AI insights digest is fantastic. It surfaced a cluster of onboarding complaints we had completely missed and saved us hours of manual tagging. Really impressive work, thank you!",
    type: "FEEDBACK",
    status: "CLOSED",
    source: "PORTAL",
    category: "UX & Design",
    tags: ["delight"],
    votes: 19,
    comments: 4,
  },
  {
    title: "Search is confusing and misses obvious matches",
    body: "Searching for 'export' does not find posts titled 'CSV exports'. Stemming or fuzzy matching would help a lot. Right now search feels broken and I often create duplicates because I cannot find the original post.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "UX & Design",
    votes: 31,
    comments: 4,
    impact: 3,
    effort: 2,
  },
  {
    title: "Bulk actions in the feedback inbox",
    body: "Select multiple posts and change status, add tags or merge duplicates in one go. Cleaning up after a big import currently takes hours of clicking one post at a time.",
    type: "FEATURE_REQUEST",
    status: "OPEN",
    source: "DASHBOARD",
    category: "UX & Design",
    author: "mia",
    tags: ["quick-win"],
    votes: 24,
    comments: 2,
    impact: 3,
    effort: 2,
  },
  {
    title: "Portal loads slowly from Australia",
    body: "The public portal takes 4+ seconds to load from Sydney. Feels like assets are only served from a US region. A CDN in APAC would make the experience much less frustrating for our customers down here.",
    type: "FEEDBACK",
    status: "UNDER_CONSIDERATION",
    source: "PORTAL",
    category: "Performance",
    votes: 22,
    comments: 3,
    impact: 3,
    effort: 3,
  },
  {
    title: "GraphQL API alongside REST",
    body: "REST is fine but a GraphQL endpoint would let our dashboard fetch posts with votes and comments in one round trip. Not urgent, but would be a nice developer experience win.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "API",
    category: "API & Developers",
    votes: 12,
    comments: 1,
    impact: 2,
    effort: 4,
  },
  {
    title: "Webhook retries and delivery logs",
    body: "When our endpoint is down we silently lose webhook events. Please add automatic retries with exponential backoff and a delivery log so we can replay failed events from the dashboard.",
    type: "FEATURE_REQUEST",
    status: "OPEN",
    source: "API",
    category: "API & Developers",
    tags: ["enterprise"],
    votes: 18,
    comments: 2,
    impact: 3,
    effort: 2,
  },
  {
    title: "Voting on behalf of customers",
    body: "Our support team wants to add a vote on behalf of a customer from a support ticket, with the customer's email attached. This keeps vote counts honest and builds the customer evidence trail automatically.",
    type: "FEATURE_REQUEST",
    status: "OPEN",
    source: "EMAIL",
    category: "Collaboration",
    votes: 21,
    comments: 3,
    impact: 4,
    effort: 2,
    revenueImpact: 15000,
  },
  {
    title: "Anonymous feedback option on the portal",
    body: "Some of our users won't submit feedback if they have to give an email. An explicit anonymous option would increase volume. We understand spam is a concern — maybe rate-limit anonymous posts.",
    type: "FEEDBACK",
    status: "CLOSED",
    source: "PORTAL",
    category: "UX & Design",
    votes: 9,
    comments: 2,
  },
  {
    title: "Love the new roadmap view",
    body: "The public roadmap is beautiful and our customers love it. Sharing it in our newsletter doubled portal signups last month. Great release, the columns and progress states are super clear.",
    type: "FEEDBACK",
    status: "CLOSED",
    source: "PORTAL",
    category: "UX & Design",
    tags: ["delight"],
    votes: 15,
    comments: 3,
  },
  {
    title: "Duplicate detection when submitting",
    body: "Show similar existing posts while typing a new one, like Stack Overflow does. Would cut down the duplicates we merge every week and get voters concentrated on one canonical post.",
    type: "FEATURE_REQUEST",
    status: "PLANNED",
    source: "DASHBOARD",
    category: "UX & Design",
    author: "demo",
    votes: 29,
    comments: 3,
    impact: 4,
    effort: 3,
  },
  {
    title: "Attachment uploads are failing intermittently",
    body: "Roughly one in five image uploads fails with a generic error and we have to retry. Started around two weeks ago. Uploads over 3 MB seem to fail most often. Quite frustrating when reporting visual bugs.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "WIDGET",
    category: "Performance",
    tags: ["churn-risk"],
    votes: 17,
    comments: 4,
    impact: 3,
    effort: 2,
  },
  {
    title: "Localize the portal into German and French",
    body: "Half of our customers are in the DACH region. Being able to translate portal headlines, statuses and buttons would make the portal usable for them. Community-contributed translations would be fine.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "UX & Design",
    votes: 20,
    comments: 2,
    impact: 3,
    effort: 3,
  },
  {
    title: "API rate limits are too aggressive",
    body: "We hit 429 errors after only 60 requests per minute during our nightly sync. Please raise the limit for paid plans or offer a bulk endpoint. The current limit makes a full export take over an hour.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "API",
    category: "API & Developers",
    votes: 14,
    comments: 2,
    impact: 2,
    effort: 1,
  },
  {
    title: "Offline support in the mobile web app",
    body: "On the train my connection drops and the app loses my half-written feedback. Draft autosave plus a basic offline queue would prevent losing work. Losing a long writeup twice made me stop reporting bugs.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "WIDGET",
    category: "Mobile",
    votes: 13,
    comments: 2,
    impact: 2,
    effort: 4,
  },
  {
    title: "Weekly email digest of top feedback",
    body: "A Monday morning email with the top new posts, biggest vote movers and a sentiment summary would keep stakeholders in the loop without giving everyone dashboard seats.",
    type: "FEEDBACK",
    status: "PLANNED",
    source: "DASHBOARD",
    category: "Collaboration",
    author: "mia",
    votes: 27,
    comments: 3,
    impact: 3,
    effort: 2,
  },
  {
    title: "Merge duplicates keeps losing votes",
    body: "When I merge two posts the votes from the merged post sometimes do not transfer, so the canonical post undercounts demand. This is a data integrity problem and makes prioritization wrong.",
    type: "FEEDBACK",
    status: "SHIPPED",
    source: "DASHBOARD",
    category: "Performance",
    author: "demo",
    votes: 11,
    comments: 3,
    impact: 4,
    effort: 2,
  },
  {
    title: "Segment feedback by customer plan",
    body: "We want to filter feedback by the customer's plan (free, pro, enterprise) synced from our billing system. Prioritizing enterprise pain first is how our roadmap actually gets decided.",
    type: "FEATURE_REQUEST",
    status: "UNDER_CONSIDERATION",
    source: "EMAIL",
    category: "Integrations",
    tags: ["enterprise", "billing"],
    votes: 25,
    comments: 2,
    impact: 4,
    effort: 3,
    revenueImpact: 25000,
  },
  {
    title: "Screen reader support on the voting buttons",
    body: "The vote button has no accessible label so VoiceOver reads it as 'button'. Also the status badges rely on color alone. A11y fixes would make the portal usable for our visually impaired teammates.",
    type: "FEEDBACK",
    status: "PLANNED",
    source: "PORTAL",
    category: "UX & Design",
    tags: ["accessibility"],
    votes: 16,
    comments: 2,
    impact: 3,
    effort: 1,
  },
  {
    title: "Changelog RSS feed",
    body: "Expose the public changelog as RSS/Atom so customers can subscribe in their reader or pipe it into Slack themselves. Should be a quick win since the data is already public.",
    type: "FEEDBACK",
    status: "SHIPPED",
    source: "PORTAL",
    category: "API & Developers",
    tags: ["quick-win"],
    votes: 10,
    comments: 1,
    impact: 2,
    effort: 1,
  },
  {
    title: "Roadmap embeds for our marketing site",
    body: "An embeddable roadmap iframe or web component we can drop on our marketing site would save us from screenshotting the roadmap each sprint. Style overrides for brand colors would be nice.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "Integrations",
    votes: 15,
    comments: 1,
    impact: 2,
    effort: 2,
  },
  {
    title: "Comment editor eats my line breaks",
    body: "Writing a multi-paragraph comment collapses everything into one blob after saving. Markdown says it supports line breaks but they disappear. Makes long comments unreadable.",
    type: "FEEDBACK",
    status: "SHIPPED",
    source: "PORTAL",
    category: "UX & Design",
    votes: 8,
    comments: 2,
  },
  {
    title: "Two-factor authentication",
    body: "Please add TOTP two-factor authentication for dashboard accounts. Our security policy requires 2FA on all vendor tools that store customer data. SMS is not acceptable, authenticator apps only.",
    type: "FEATURE_REQUEST",
    status: "UNDER_CONSIDERATION",
    source: "EMAIL",
    category: "Integrations",
    tags: ["security", "enterprise"],
    votes: 23,
    comments: 2,
    impact: 4,
    effort: 2,
  },
  {
    title: "Notifications are overwhelming",
    body: "I get an email for every single comment on posts I follow. A per-post mute and a daily bundle option would stop me from filtering everything to trash, which is what I do now.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "Collaboration",
    votes: 12,
    comments: 2,
    impact: 2,
    effort: 2,
  },
  {
    title: "Import from Canny and UserVoice",
    body: "We are migrating from Canny with four years of history. A first-class importer that preserves votes, comments and statuses would make switching painless. CSV import loses the comment threads.",
    type: "FEATURE_REQUEST",
    status: "SHIPPED",
    source: "IMPORT",
    category: "Integrations",
    author: "demo",
    votes: 18,
    comments: 2,
    impact: 4,
    effort: 3,
  },
  {
    title: "Analytics charts render blank on Safari",
    body: "The analytics page shows empty charts on Safari 17 while Chrome works fine. Console shows a ResizeObserver error. Half our team is on Macs so this blocks our Monday metrics review.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "DASHBOARD",
    category: "Performance",
    author: "mia",
    votes: 7,
    comments: 2,
    impact: 3,
    effort: 1,
  },
  {
    title: "Custom domains for the portal",
    body: "Serve our portal at feedback.ourdomain.com with automatic SSL instead of the shared novaris URL. Important for brand trust — customers hesitate to leave feedback on a third-party domain.",
    type: "FEATURE_REQUEST",
    status: "PLANNED",
    source: "PORTAL",
    category: "Integrations",
    tags: ["enterprise"],
    votes: 34,
    comments: 3,
    impact: 4,
    effort: 3,
    revenueImpact: 20000,
  },
  {
    title: "Widget takes forever to load on our site",
    body: "The feedback widget adds nearly 900 KB of JavaScript and visibly delays our page load. A slim loader that lazy-loads the widget on click would fix the Lighthouse score regression we're seeing.",
    type: "FEEDBACK",
    status: "UNDER_CONSIDERATION",
    source: "WIDGET",
    category: "Performance",
    votes: 19,
    comments: 3,
    impact: 3,
    effort: 2,
  },
  {
    title: "Private boards for internal feedback",
    body: "We want an internal-only board for employee feedback that never appears on the public portal. Same voting and statuses, just hidden from guests. Today we abuse tags to fake this and it leaks occasionally.",
    type: "FEATURE_REQUEST",
    status: "OPEN",
    source: "DASHBOARD",
    category: "Collaboration",
    author: "demo",
    votes: 22,
    comments: 2,
    impact: 3,
    effort: 2,
  },
  {
    title: "Sentiment analysis mislabels sarcasm",
    body: "'Oh great, another crash' was labeled positive. I know sarcasm is hard, but maybe weigh words like crash higher than great. The sentiment charts are otherwise useful for spotting bad releases.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "UX & Design",
    votes: 9,
    comments: 2,
  },
  {
    title: "Board-level permissions per member",
    body: "Let admins restrict which boards each member can see and edit. Our support team should triage the bugs board but not see the pricing research board. Role-per-board, not just role-per-org.",
    type: "FEATURE_REQUEST",
    status: "OPEN",
    source: "DASHBOARD",
    category: "Collaboration",
    author: "mia",
    tags: ["enterprise", "security"],
    votes: 16,
    comments: 2,
    impact: 3,
    effort: 4,
  },
  {
    title: "The onboarding tour was genuinely helpful",
    body: "New team member here — the interactive onboarding tour made setup easy and I had our portal live in ten minutes. Nice touch auto-importing our logo colors. Smooth experience overall.",
    type: "FEEDBACK",
    status: "CLOSED",
    source: "PORTAL",
    category: "UX & Design",
    tags: ["delight"],
    votes: 6,
    comments: 1,
  },
  {
    title: "Vote weight for enterprise customers",
    body: "All votes count equally today, but a request from a $100k account matters more than ten free users. Optional vote weighting or an account-value overlay on the priority score would reflect reality.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "EMAIL",
    category: "Collaboration",
    tags: ["billing"],
    votes: 14,
    comments: 2,
    impact: 3,
    effort: 2,
    revenueImpact: 10000,
  },
  {
    title: "Exports time out for large workspaces",
    body: "Exporting our 8,000-post workspace times out after 30 seconds with a 504 error. An async export that emails a download link when ready would solve it. Currently we export board by board as a workaround.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "DASHBOARD",
    category: "Performance",
    votes: 10,
    comments: 2,
    impact: 3,
    effort: 2,
  },
  {
    title: "Intercom integration for support tickets",
    body: "Create a post or add a vote directly from an Intercom conversation and link the ticket. Support hears the same requests daily and that signal is completely lost right now.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "Integrations",
    votes: 13,
    comments: 1,
    impact: 3,
    effort: 3,
  },
  {
    title: "Add reaction emojis to comments",
    body: "Sometimes I just want to 👍 a comment instead of writing 'agreed'. Lightweight reactions would reduce noise in long threads and give the team a quick read on consensus.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "UX & Design",
    tags: ["delight", "quick-win"],
    votes: 11,
    comments: 1,
    impact: 2,
    effort: 1,
  },
  {
    title: "Trial expired but I was still charged",
    body: "I cancelled during the trial but was charged for a month anyway. Support sorted the refund quickly (thanks!) but the cancellation flow clearly has a bug when cancelling on the last trial day.",
    type: "FEEDBACK",
    status: "CLOSED",
    source: "EMAIL",
    category: "Performance",
    tags: ["billing", "churn-risk"],
    votes: 5,
    comments: 3,
  },
  {
    title: "Survey builder needs question branching",
    body: "We want to show follow-up questions based on earlier answers, e.g. only ask detractors what went wrong. Conditional logic in surveys would double our completion rate compared to one-size-fits-all forms.",
    type: "FEATURE_REQUEST",
    status: "SHIPPED",
    source: "DASHBOARD",
    category: "UX & Design",
    author: "mia",
    votes: 20,
    comments: 2,
    impact: 4,
    effort: 3,
  },
  {
    title: "Mobile app crashes when opening images",
    body: "On Android 14 the PWA crashes whenever I tap an attached screenshot. It happens every single time, so reviewing visual bug reports on mobile is impossible for me.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "WIDGET",
    category: "Mobile",
    votes: 8,
    comments: 2,
    impact: 3,
    effort: 2,
  },
  {
    title: "Show related posts on the post page",
    body: "When viewing a post, show similar posts underneath so voters can discover the canonical request instead of creating duplicates. The clustering data clearly exists already — expose it in the portal.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "UX & Design",
    votes: 12,
    comments: 1,
    impact: 2,
    effort: 2,
  },
  {
    title: "Audit log for admin actions",
    body: "Compliance asks who changed a post status, deleted comments or rotated API keys and when. An immutable audit log with export would tick the SOC 2 box and help us debug 'who moved this?' mysteries.",
    type: "FEATURE_REQUEST",
    status: "OPEN",
    source: "EMAIL",
    category: "API & Developers",
    tags: ["security", "enterprise"],
    votes: 17,
    comments: 1,
    impact: 3,
    effort: 3,
  },
  {
    title: "Copy for empty states is charming",
    body: "Whoever writes your empty state copy deserves a raise. Little details like that make the product feel loved and make demos to stakeholders more fun. Keep it up!",
    type: "FEEDBACK",
    status: "CLOSED",
    source: "PORTAL",
    category: "UX & Design",
    tags: ["delight"],
    votes: 4,
    comments: 1,
  },
  {
    title: "Filter roadmap by category and tag",
    body: "Our public roadmap mixes mobile and API work. Customers only care about their slice — let visitors filter the roadmap columns by category or tag so the view stays relevant to them.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "PORTAL",
    category: "UX & Design",
    votes: 9,
    comments: 1,
    impact: 2,
    effort: 1,
  },
  {
    title: "Search latency spikes every evening",
    body: "Between 6 and 8pm UTC search takes 3-5 seconds instead of the usual instant results. Feels like a shared resource issue. Not blocking, but noticeably slow and worth investigating.",
    type: "FEEDBACK",
    status: "OPEN",
    source: "API",
    category: "Performance",
    votes: 6,
    comments: 1,
  },
];

// ---------------------------------------------------------------------------
// Comment text pools
// ---------------------------------------------------------------------------

const GUEST_COMMENTS = [
  "Big +1 from our team, this would save us hours every week.",
  "We need this too. Any timeline you can share?",
  "Came here to request exactly this. Voted!",
  "This is the main reason we haven't upgraded yet.",
  "Adding our voice — three teams at our company want this.",
  "Same problem here, happy to hop on a call and show you.",
  "Would love this. Our current workaround is really clunky.",
  "Following. This keeps coming up in our retro every sprint.",
  "Is there a beta we can join? We'd test this heavily.",
  "This plus the API would make Orbit unbeatable for us.",
  "We churned from a competitor because they lacked this. Please build it.",
  "Any update on this one? It's been a few weeks.",
  "Our whole support team upvoted this today.",
  "Exactly our use case as well — described better than I could.",
  "Please prioritize this over new features. Basics first!",
];

const TEAM_COMMENTS = [
  "Thanks for the detailed writeup — we're looking into this now.",
  "Great context, everyone. Moving this into consideration and scoping it this week.",
  "Update: design explorations are done, engineering starts next sprint.",
  "We shipped a first version today — would love your feedback on it!",
  "Good news: this is now planned for the upcoming quarter.",
  "Could you share what plan or browser you're on? Want to reproduce this.",
  "We hear you loud and clear. Bumping the priority internally.",
  "Linking a few duplicates into this thread so votes concentrate here.",
  "This is trickier than it looks because of multi-tenant isolation, but it's high on our list.",
  "Rolled out a fix an hour ago — please tell us if you still see it.",
];

// ---------------------------------------------------------------------------

async function resetIfRequested(existingOrgId: string) {
  console.log("RESET=1 — deleting existing 'orbit-labs' org and demo users…");
  await db.organization.delete({ where: { id: existingOrgId } });
  await db.user.deleteMany({ where: { email: { in: [DEMO_EMAIL, MIA_EMAIL] } } });
}

async function main() {
  const existing = await db.organization.findUnique({ where: { slug: ORG_SLUG } });
  if (existing) {
    if (process.env.RESET === "1") {
      await resetIfRequested(existing.id);
    } else {
      console.log(
        `Org '${ORG_SLUG}' already exists (${existing.id}). Nothing to do. Set RESET=1 to reseed.`
      );
      return;
    }
  }

  const counts: Record<string, number> = {};

  // --- Users & org ---------------------------------------------------------
  const passwordHash = await bcrypt.hash("demopass123", 12);
  const demo = await db.user.upsert({
    where: { email: DEMO_EMAIL },
    update: { name: "Demo Founder", passwordHash },
    create: { email: DEMO_EMAIL, name: "Demo Founder", passwordHash },
  });
  const mia = await db.user.upsert({
    where: { email: MIA_EMAIL },
    update: { name: "Mia Chen", passwordHash },
    create: { email: MIA_EMAIL, name: "Mia Chen", passwordHash },
  });
  counts["users"] = 2;

  const org = await db.organization.create({
    data: {
      name: "Orbit Labs",
      slug: ORG_SLUG,
      brandColor: "#8b8bf5",
      portalEnabled: true,
      portalHeadline: "Help us build the future of Orbit",
      portalIntro:
        "Tell us what to build next — vote on ideas, follow the roadmap, and see what ships.",
      memberships: {
        create: [
          { userId: demo.id, role: "OWNER" },
          { userId: mia.id, role: "ADMIN" },
        ],
      },
    },
  });
  counts["organizations"] = 1;
  counts["memberships"] = 2;

  const board = await db.board.create({
    data: {
      orgId: org.id,
      name: "General",
      slug: "general",
      description: "All feedback and feature requests for Orbit.",
      isPublic: true,
    },
  });
  counts["boards"] = 1;

  // --- Categories & tags ---------------------------------------------------
  const categoryByName = new Map<string, string>();
  for (const c of CATEGORIES) {
    const row = await db.category.create({
      data: { orgId: org.id, name: c.name, color: c.color },
    });
    categoryByName.set(c.name, row.id);
  }
  counts["categories"] = CATEGORIES.length;

  const tagByName = new Map<string, string>();
  for (const t of TAGS) {
    const row = await db.tag.create({
      data: { orgId: org.id, name: t.name, color: t.color },
    });
    tagByName.set(t.name, row.id);
  }
  counts["tags"] = TAGS.length;

  // --- Posts ----------------------------------------------------------------
  type CreatedPost = {
    id: string;
    spec: PostSpec;
    createdAt: Date;
    voteCount: number;
    commentCount: number;
    sentimentScore: number;
  };
  const created: CreatedPost[] = [];

  // Spaced roadmap ordering per status column.
  const roadmapCursor: Record<string, number> = {};
  const nextRoadmapOrder = (status: string) => {
    roadmapCursor[status] = (roadmapCursor[status] ?? 0) + 10;
    return roadmapCursor[status] + rand() * 2; // spaced floats
  };

  let guestSeq = 0;
  for (const spec of POSTS) {
    const createdAt =
      spec.daysAgoOverride != null ? daysAgo(spec.daysAgoOverride) : weightedRecentDate();
    const text = `${spec.title}. ${spec.body}`;
    const s = analyzeSentiment(text);
    const summary = summarizeText(spec.body, 160);

    const author = spec.author === "demo" ? demo : spec.author === "mia" ? mia : null;
    const guest = !author && !spec.anonymous ? GUESTS[guestSeq++ % GUESTS.length] : null;

    const shipped = spec.status === "SHIPPED";
    const shippedAt = shipped ? dateSince(new Date(createdAt.getTime() + 5 * DAY)) : null;

    const post = await db.post.create({
      data: {
        orgId: org.id,
        boardId: board.id,
        type: spec.type,
        title: spec.title,
        body: spec.body,
        status: spec.status,
        source: spec.source,
        authorId: author?.id ?? null,
        guestName: guest?.name ?? null,
        guestEmail: guest?.email ?? null,
        categoryId: categoryByName.get(spec.category) ?? null,
        sentiment: s.sentiment,
        sentimentScore: s.score,
        aiSummary: summary,
        impact: spec.impact ?? null,
        effort: spec.effort ?? null,
        revenueImpact: spec.revenueImpact ?? null,
        showOnRoadmap: spec.roadmap === true,
        roadmapOrder: spec.roadmap ? nextRoadmapOrder(spec.status) : 0,
        shippedAt,
        pinned: spec.pinned === true,
        createdAt,
      },
    });

    if (spec.tags?.length) {
      await db.postTag.createMany({
        data: spec.tags.map((name) => ({ postId: post.id, tagId: tagByName.get(name)! })),
      });
      counts["postTags"] = (counts["postTags"] ?? 0) + spec.tags.length;
    }

    created.push({
      id: post.id,
      spec,
      createdAt,
      voteCount: 0,
      commentCount: 0,
      sentimentScore: s.score,
    });
  }
  counts["posts"] = created.length;

  // --- Votes ----------------------------------------------------------------
  let globalVoterSeq = 0;
  let totalVotes = 0;
  for (const p of created) {
    const target = p.spec.votes;
    if (target <= 0) continue;
    const rows: Prisma.VoteCreateManyInput[] = [];
    // A few user votes on popular posts.
    if (target >= 40) {
      rows.push({
        postId: p.id,
        userId: demo.id,
        createdAt: dateSince(p.createdAt),
      });
      rows.push({
        postId: p.id,
        userId: mia.id,
        createdAt: dateSince(p.createdAt),
      });
    }
    while (rows.length < target) {
      rows.push({
        postId: p.id,
        guestId: `seed-guest-${++globalVoterSeq}`,
        createdAt: dateSince(p.createdAt),
      });
    }
    await db.vote.createMany({ data: rows });
    p.voteCount = rows.length;
    totalVotes += rows.length;
  }
  counts["votes"] = totalVotes;

  // --- Comments ---------------------------------------------------------------
  // Distribute ~120 comments using each spec's `comments` weight.
  const desiredTotal = POSTS.reduce((sum, s) => sum + (s.comments ?? 0), 0);
  const commentScale = desiredTotal > 0 ? Math.min(1, 120 / desiredTotal) : 1;
  let totalComments = 0;
  let guestCommentSeq = 0;
  for (const p of created) {
    const weight = p.spec.comments ?? 0;
    const n = weight >= 2 ? Math.max(1, Math.round(weight * commentScale)) : weight;
    if (n <= 0) continue;
    let lastGuestCommentId: string | null = null;
    let lastGuestCommentAt: Date | null = null;
    for (let i = 0; i < n; i++) {
      const isTeam = i > 0 && (i % 3 === 2 || (i === n - 1 && n >= 3));
      if (isTeam) {
        const user = rand() < 0.6 ? mia : demo;
        // Team members sometimes reply directly to a guest comment thread.
        const asReply = lastGuestCommentId !== null && rand() < 0.5;
        const when = asReply && lastGuestCommentAt ? dateSince(lastGuestCommentAt) : dateSince(p.createdAt);
        await db.comment.create({
          data: {
            postId: p.id,
            authorId: user.id,
            body: pick(TEAM_COMMENTS),
            isTeam: true,
            parentId: asReply ? lastGuestCommentId : null,
            createdAt: when,
          },
        });
      } else {
        const when = dateSince(p.createdAt);
        const guest = GUESTS[guestCommentSeq++ % GUESTS.length];
        const row = await db.comment.create({
          data: {
            postId: p.id,
            guestName: guest.name,
            body: pick(GUEST_COMMENTS),
            isTeam: false,
            createdAt: when,
          },
        });
        lastGuestCommentId = row.id;
        lastGuestCommentAt = when;
      }
      p.commentCount++;
      totalComments++;
    }
  }
  counts["comments"] = totalComments;

  // --- Denormalized counters + priority score --------------------------------
  for (const p of created) {
    await db.post.update({
      where: { id: p.id },
      data: {
        voteCount: p.voteCount,
        commentCount: p.commentCount,
        priorityScore: priorityScore({
          voteCount: p.voteCount,
          commentCount: p.commentCount,
          sentimentScore: p.sentimentScore,
          impact: p.spec.impact ?? null,
          effort: p.spec.effort ?? null,
          revenueImpact: p.spec.revenueImpact ?? null,
          createdAt: p.createdAt,
        }),
      },
    });
  }

  // --- Clusters ---------------------------------------------------------------
  const clusterInput = created.map((p) => ({
    id: p.id,
    text: `${p.spec.title}. ${p.spec.body}`,
  }));
  // Slightly looser threshold than the default so the demo data yields a
  // handful of meaningful clusters; keep the top 5 by size.
  const clusters = clusterTexts(clusterInput, 0.13).slice(0, 5);
  for (const c of clusters) {
    const memberTexts = c.memberIds
      .map((id) => created.find((p) => p.id === id))
      .filter((p): p is CreatedPost => Boolean(p))
      .map((p) => `${p.spec.title}. ${p.spec.body}`)
      .join(" ");
    const row = await db.cluster.create({
      data: {
        orgId: org.id,
        label: c.label,
        summary: summarizeText(memberTexts, 200),
      },
    });
    await db.post.updateMany({
      where: { id: { in: c.memberIds }, orgId: org.id },
      data: { clusterId: row.id },
    });
  }
  counts["clusters"] = clusters.length;

  // --- Insights ---------------------------------------------------------------
  const topPost = created[0];
  await db.insight.createMany({
    data: [
      {
        orgId: org.id,
        kind: "TREND",
        title: "Team collaboration is the fastest-growing request, +42% over 30 days",
        body: "Votes on collaboration-related posts grew 42% in the last 30 days, led by 'Team collaboration & shared workspaces' (now the #1 request overall). Agencies and multi-team accounts are driving the surge — consider fast-tracking shared workspaces.",
        data: {
          postId: topPost.id,
          votes30dAgo: 127,
          votesNow: 180,
          growthPct: 42,
          relatedCategory: "Collaboration",
          window: "30d",
        },
        createdAt: daysAgo(2),
      },
      {
        orgId: org.id,
        kind: "SUMMARY",
        title: "Weekly digest: 14 new posts, sentiment steady",
        body: "This week Orbit Labs received 14 new posts (9 feature requests, 5 feedback). Top themes: collaboration, exports and mobile stability. Overall sentiment held steady at slightly positive; performance complaints dipped after last week's fix rollout.",
        data: { newPosts: 14, requests: 9, feedback: 5, sentimentAvg: 0.11, window: "7d" },
        createdAt: daysAgo(1),
      },
      {
        orgId: org.id,
        kind: "OPPORTUNITY",
        title: "Enterprise buyers keep asking for SSO + audit logs",
        body: "Five posts tagged 'enterprise' in the last 45 days mention SSO, 2FA or audit logs, together representing an estimated $110k in attached revenue. Packaging these as a security tier could unblock several stalled procurement processes.",
        data: { taggedPosts: 5, estRevenue: 110000, tags: ["enterprise", "security"], window: "45d" },
        createdAt: daysAgo(4),
      },
      {
        orgId: org.id,
        kind: "ALERT",
        title: "Negative sentiment spike in Performance",
        body: "Negative sentiment in the Performance category jumped from 31% to 58% of posts over the last 14 days, driven by dashboard slowness, upload failures and export timeouts. Recommend a public status update before frustration spreads to social channels.",
        data: {
          category: "Performance",
          negativeShareBefore: 0.31,
          negativeShareNow: 0.58,
          window: "14d",
        },
        createdAt: daysAgo(0.5),
      },
    ],
  });
  counts["insights"] = 4;

  // --- Surveys ----------------------------------------------------------------
  const nps = await db.survey.create({
    data: {
      orgId: org.id,
      name: "Quarterly NPS",
      description: "Quarterly pulse on how likely customers are to recommend Orbit.",
      status: "ACTIVE",
      audience: { segment: "all" },
      startsAt: daysAgo(40),
      createdAt: daysAgo(42),
    },
  });
  const qNps = await db.surveyQuestion.create({
    data: {
      surveyId: nps.id,
      order: 0,
      kind: "NPS",
      prompt: "How likely are you to recommend Orbit?",
      required: true,
    },
  });
  const qRole = await db.surveyQuestion.create({
    data: {
      surveyId: nps.id,
      order: 1,
      kind: "SINGLE_CHOICE",
      prompt: "What best describes your role?",
      required: true,
      options: { choices: ["Product manager", "Engineer", "Designer", "Founder / Exec"] },
    },
  });
  const qImprove = await db.surveyQuestion.create({
    data: {
      surveyId: nps.id,
      order: 2,
      kind: "OPEN_TEXT",
      prompt: "What should we improve?",
      required: false,
      // Conditional display: only shown when the NPS answer is 6 or lower.
      condition: { questionId: qNps.id, lte: 6 },
    },
  });

  const ROLES = ["Product manager", "Engineer", "Designer", "Founder / Exec"];
  const DETRACTOR_TEXTS = [
    "The dashboard is too slow once you have real data volume.",
    "Missing team collaboration — we can't roll it out company-wide.",
    "Exports keep timing out and support was slow to respond.",
    "Mobile experience is rough; the widget breaks on small screens.",
    "Search rarely finds what I'm looking for, so I create duplicates.",
    "Pricing feels steep without SSO and audit logs included.",
  ];
  let npsResponses = 0;
  for (let i = 0; i < 35; i++) {
    // Weighted NPS distribution: mostly promoters/passives, a healthy tail.
    const r = rand();
    const score =
      r < 0.34 ? randInt(9, 10) : r < 0.62 ? randInt(7, 8) : r < 0.85 ? randInt(4, 6) : randInt(0, 3);
    const started = dateSince(daysAgo(38));
    const answers: { questionId: string; value: Prisma.InputJsonValue }[] = [
      { questionId: qNps.id, value: score },
      { questionId: qRole.id, value: ROLES[randInt(0, ROLES.length - 1)] },
    ];
    if (score <= 6) {
      answers.push({ questionId: qImprove.id, value: pick(DETRACTOR_TEXTS) });
    }
    await db.surveyResponse.create({
      data: {
        surveyId: nps.id,
        guestId: `seed-survey-guest-${i + 1}`,
        createdAt: started,
        completedAt: new Date(started.getTime() + randInt(45, 300) * 1000),
        answers: { create: answers },
      },
    });
    npsResponses++;
  }

  const exportsSurvey = await db.survey.create({
    data: {
      orgId: org.id,
      name: "Feature satisfaction: Exports",
      description: "How well do the new CSV/JSON exports work for you?",
      status: "DRAFT",
      createdAt: daysAgo(6),
      questions: {
        create: [
          {
            order: 0,
            kind: "RATING",
            prompt: "How satisfied are you with the new export feature?",
            required: true,
          },
          {
            order: 1,
            kind: "MULTIPLE_CHOICE",
            prompt: "Which export formats do you use?",
            required: true,
            options: { choices: ["CSV", "JSON", "Excel via CSV", "API pull"] },
          },
          {
            order: 2,
            kind: "OPEN_TEXT",
            prompt: "Anything missing from exports?",
            required: false,
          },
        ],
      },
    },
  });
  void exportsSurvey;
  counts["surveys"] = 2;
  counts["surveyQuestions"] = 6;
  counts["surveyResponses"] = npsResponses;

  // --- Changelog ----------------------------------------------------------------
  const CHANGELOG: {
    version: string;
    title: string;
    labels: ("NEW" | "IMPROVED" | "FIXED" | "DEPRECATED" | "SECURITY")[];
    publishedDaysAgo: number;
    body: string;
  }[] = [
    {
      version: "v1.0",
      title: "The Beginning",
      labels: ["NEW"],
      publishedDaysAgo: 100,
      body: `## Orbit is live 🎉\n\nToday we're opening Orbit to everyone. The first release includes:\n\n- **Feedback boards** with voting and comments\n- A **public portal** your customers can use without an account\n- Simple **status tracking** from open to shipped\n\nThanks to our beta users for four months of brutally honest feedback — this product exists because of you.`,
    },
    {
      version: "v1.2",
      title: "The feedback widget",
      labels: ["NEW", "IMPROVED"],
      publishedDaysAgo: 82,
      body: `## Collect feedback anywhere\n\nDrop the new **embeddable widget** into your app with two lines of code.\n\n- Works inside any page as a lightweight iframe\n- Inherits your **brand color** automatically\n- Guests can post and vote without signing up\n\nWe also **improved** portal load times by ~40% by moving assets to a CDN.`,
    },
    {
      version: "v1.5",
      title: "AI Insights arrive",
      labels: ["NEW"],
      publishedDaysAgo: 61,
      body: `## Your feedback, summarized\n\nOrbit now reads every post so you don't have to:\n\n- **Sentiment analysis** on each post, with trends per category\n- **Automatic clustering** groups duplicate asks together\n- A weekly **AI digest** highlights what changed\n\nEverything runs on your existing data — no setup required. **Bold claim:** you'll find at least one theme you didn't know about in the first week.`,
    },
    {
      version: "v1.8",
      title: "Public roadmaps",
      labels: ["NEW", "FIXED"],
      publishedDaysAgo: 45,
      body: `## Show customers what's coming\n\nThe new **public roadmap** turns your plans into a shareable page:\n\n- Columns for *Under consideration*, *Planned*, *In progress* and *Shipped*\n- Drag to reorder — the portal updates instantly\n- Voters get notified when a post moves\n\n**Fixed** along the way: the widget layout on narrow screens, and merged posts now transfer their votes correctly.`,
    },
    {
      version: "v2.0",
      title: "Smart surveys",
      labels: ["NEW", "IMPROVED"],
      publishedDaysAgo: 28,
      body: `## Ask better questions\n\nOrbit 2.0 introduces **surveys** with the smarts built in:\n\n- NPS, ratings, choice and open-text questions\n- **Conditional logic** — only ask detractors what went wrong\n- Response analytics with completion funnels\n\nWe also **improved** the dashboard for large workspaces: lists virtualize past 200 posts and filters apply instantly.`,
    },
    {
      version: "v2.4",
      title: "Exports, Slack & a faster dashboard",
      labels: ["NEW", "IMPROVED", "SECURITY"],
      publishedDaysAgo: 9,
      body: `## Quality-of-life week\n\n- **CSV & JSON exports** for posts, votes and comments — most requested quick win, delivered\n- **Slack integration**: new posts and status changes land in your channel\n- Dashboard queries rewritten — big workspaces load **3× faster**\n\nOn the **security** side, session tokens are now rotated on privilege changes. As always, tell us what to build next on the portal.`,
    },
  ];

  const entryIds: string[] = [];
  let reactionCount = 0;
  let clogCommentCount = 0;
  let reactionGuestSeq = 0;
  for (const e of CHANGELOG) {
    const publishedAt = daysAgo(e.publishedDaysAgo);
    const entry = await db.changelogEntry.create({
      data: {
        orgId: org.id,
        title: e.title,
        slug: slugifyLocal(`${e.version}-${e.title}`),
        version: e.version,
        body: e.body,
        labels: e.labels,
        authorId: demo.id,
        publishedAt,
        createdAt: new Date(publishedAt.getTime() - 2 * DAY),
      },
    });
    entryIds.push(entry.id);

    // Reactions: unique (entryId, emoji, guestId/userId).
    const emojis = ["🎉", "❤️", "👍", "🚀"];
    const reactionRows: Prisma.ChangelogReactionCreateManyInput[] = [];
    for (const emoji of emojis) {
      const n = randInt(1, 9);
      for (let i = 0; i < n; i++) {
        reactionRows.push({
          entryId: entry.id,
          emoji,
          guestId: `seed-react-guest-${++reactionGuestSeq}`,
        });
      }
    }
    // A user reaction or two.
    reactionRows.push({ entryId: entry.id, emoji: "🚀", userId: mia.id });
    if (rand() < 0.5) reactionRows.push({ entryId: entry.id, emoji: "🎉", userId: demo.id });
    await db.changelogReaction.createMany({ data: reactionRows });
    reactionCount += reactionRows.length;
  }

  // A few changelog comments on the most recent entries.
  const clogComments: Prisma.ChangelogCommentCreateManyInput[] = [
    {
      entryId: entryIds[entryIds.length - 1],
      guestName: "Ben Okafor",
      body: "The Slack integration works great — set it up in two minutes.",
      createdAt: daysAgo(7),
    },
    {
      entryId: entryIds[entryIds.length - 1],
      guestName: "Grace Liu",
      body: "Exports! Finally. Any chance of scheduled exports next?",
      createdAt: daysAgo(6),
    },
    {
      entryId: entryIds[entryIds.length - 1],
      authorId: mia.id,
      body: "Scheduled exports are on our radar — vote for it on the board so we can gauge demand!",
      createdAt: daysAgo(5),
    },
    {
      entryId: entryIds[entryIds.length - 2],
      guestName: "Elena Fischer",
      body: "Conditional survey logic doubled our completion rate. Nice work.",
      createdAt: daysAgo(20),
    },
    {
      entryId: entryIds[2],
      guestName: "Jonas Berg",
      body: "The clustering found a theme we'd missed for months. Impressive.",
      createdAt: daysAgo(55),
    },
  ];
  await db.changelogComment.createMany({ data: clogComments });
  clogCommentCount = clogComments.length;

  // One draft entry.
  await db.changelogEntry.create({
    data: {
      orgId: org.id,
      title: "Shared workspaces (early access)",
      slug: "v2-5-shared-workspaces-early-access",
      version: "v2.5",
      body: `## Draft — do not publish yet\n\n- **Shared workspaces** with per-member roles\n- @mentions in comments\n- Real-time presence on the roadmap board\n\nTargeting early access invites for the top 20 voters on the collaboration thread.`,
      labels: ["NEW"],
      authorId: demo.id,
      publishedAt: null,
      createdAt: daysAgo(3),
    },
  });
  counts["changelogEntries"] = CHANGELOG.length + 1;
  counts["changelogReactions"] = reactionCount;
  counts["changelogComments"] = clogCommentCount;

  // --- Webhook ------------------------------------------------------------------
  await db.webhook.create({
    data: {
      orgId: org.id,
      url: "https://example.com/hooks/orbit",
      secret: "whsec_demo",
      events: ["post.created", "post.status_changed"],
      active: false,
    },
  });
  counts["webhooks"] = 1;

  // --- Activity -------------------------------------------------------------------
  const activityRows: Prisma.ActivityCreateManyInput[] = [];
  const recentPosts = created
    .slice()
    .sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime())
    .slice(0, 12);
  for (const p of recentPosts) {
    activityRows.push({
      orgId: org.id,
      actorId: p.spec.author === "demo" ? demo.id : p.spec.author === "mia" ? mia.id : null,
      type: "post.created",
      meta: { title: p.spec.title, postId: p.id, source: p.spec.source },
      createdAt: p.createdAt,
    });
  }
  const statusChanged = created.filter((p) => p.spec.status !== "OPEN").slice(0, 10);
  for (const p of statusChanged) {
    activityRows.push({
      orgId: org.id,
      actorId: rand() < 0.5 ? demo.id : mia.id,
      type: "post.status_changed",
      meta: { title: p.spec.title, postId: p.id, from: "OPEN", to: p.spec.status },
      createdAt: dateSince(new Date(Math.max(p.createdAt.getTime(), NOW - 21 * DAY))),
    });
  }
  const voteWorthy = created.filter((p) => p.voteCount >= 20).slice(0, 5);
  for (const p of voteWorthy) {
    activityRows.push({
      orgId: org.id,
      type: "vote.added",
      meta: { title: p.spec.title, postId: p.id, voteCount: p.voteCount },
      createdAt: daysAgo(randInt(0, 14)),
    });
  }
  for (const [i, e] of CHANGELOG.slice(-3).entries()) {
    activityRows.push({
      orgId: org.id,
      actorId: demo.id,
      type: "changelog.published",
      meta: { title: e.title, version: e.version, entryId: entryIds[CHANGELOG.length - 3 + i] },
      createdAt: daysAgo(e.publishedDaysAgo),
    });
  }
  await db.activity.createMany({ data: activityRows });
  counts["activities"] = activityRows.length;

  // --- Summary ----------------------------------------------------------------------
  console.log("\nSeed complete for org 'orbit-labs'. Created:");
  console.table(
    Object.entries(counts).map(([entity, count]) => ({ entity, count }))
  );
  console.log("Sign in with demo@novaris.app / demopass123 (owner) or mia@novaris.app / demopass123 (admin).");
}

main()
  .catch((err) => {
    console.error("Seed failed:", err);
    process.exitCode = 1;
  })
  .finally(async () => {
    await db.$disconnect();
  });
