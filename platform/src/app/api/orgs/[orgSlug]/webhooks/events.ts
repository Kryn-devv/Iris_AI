export const WEBHOOK_EVENTS = [
  "post.created",
  "post.status_changed",
  "vote.added",
  "changelog.published",
  "survey.response",
] as const;

export type WebhookEvent = (typeof WEBHOOK_EVENTS)[number];
