/**
 * Prioritization scoring: turns demand signals into a single comparable score.
 * The formula is intentionally transparent — it is surfaced in the UI.
 */

export type PrioritySignals = {
  voteCount: number;
  commentCount: number;
  /** -1..1 */
  sentimentScore?: number | null;
  /** 1..5, team-assigned */
  impact?: number | null;
  /** 1..5, team-assigned (higher = more work) */
  effort?: number | null;
  /** Rough revenue attached by the team, in dollars. */
  revenueImpact?: number | null;
  createdAt: Date;
};

/**
 * Score components (each roughly 0..40):
 *  - demand: votes + comments, log-scaled so whales don't drown everything
 *  - value: team-assessed impact vs effort, plus revenue signal
 *  - momentum: newer posts get a small freshness boost
 *  - urgency: strongly negative sentiment nudges the score up (pain signal)
 */
export function priorityScore(signals: PrioritySignals): number {
  const demand =
    Math.log2(1 + signals.voteCount) * 8 + Math.log2(1 + signals.commentCount) * 4;

  const impact = signals.impact ?? 3;
  const effort = signals.effort ?? 3;
  const valueRatio = impact / Math.max(1, effort); // 0.2 .. 5
  const revenue = Math.log10(1 + Math.max(0, signals.revenueImpact ?? 0)) * 4;
  const value = valueRatio * 6 + revenue;

  const ageDays = (Date.now() - signals.createdAt.getTime()) / 86400_000;
  const momentum = Math.max(0, 10 - ageDays / 9); // fades over ~90 days

  const urgency =
    signals.sentimentScore != null && signals.sentimentScore < -0.2
      ? Math.abs(signals.sentimentScore) * 8
      : 0;

  return Number((demand + value + momentum + urgency).toFixed(2));
}
