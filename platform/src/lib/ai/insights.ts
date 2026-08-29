import "server-only";
import { db } from "@/lib/db";
import { clusterTexts, summarizeText, analyzeSentiment } from "@/lib/ai/heuristic";
import { completeJSON, getProvider } from "@/lib/ai/provider";
import type { InsightKind, Prisma } from "@prisma/client";

/**
 * Org-level AI pipeline: clustering + insight generation.
 * Works fully offline via the heuristic engine; an LLM provider (when
 * configured) refines cluster labels and adds one narrative summary.
 */

export async function rebuildClusters(orgId: string): Promise<number> {
  const posts = await db.post.findMany({
    where: { orgId, archived: false, mergedIntoId: null },
    select: { id: true, title: true, body: true },
  });

  const clusters = clusterTexts(
    posts.map((p) => ({ id: p.id, text: `${p.title}\n${p.body}` })),
    0.15
  ).slice(0, 8);

  // Optional LLM refinement of labels (batched, single call).
  let labels: string[] | null = null;
  if (getProvider() && clusters.length > 0) {
    labels = await completeJSON<string[]>(
      "You name clusters of related customer feedback.",
      [
        "For each cluster below, return a concise 2-4 word product-area label.",
        ...clusters.map((c, i) => {
          const members = c.memberIds
            .slice(0, 4)
            .map((id) => posts.find((p) => p.id === id)?.title ?? "")
            .join(" | ");
          return `${i + 1}. keywords: ${c.label}; examples: ${members}`;
        }),
        `Return a JSON array of ${clusters.length} strings, in order.`,
      ].join("\n")
    );
    if (!Array.isArray(labels) || labels.length !== clusters.length) labels = null;
  }

  await db.$transaction(async (tx) => {
    await tx.post.updateMany({ where: { orgId }, data: { clusterId: null } });
    await tx.cluster.deleteMany({ where: { orgId } });
    for (let i = 0; i < clusters.length; i++) {
      const c = clusters[i]!;
      const memberText = c.memberIds
        .map((id) => posts.find((p) => p.id === id))
        .filter(Boolean)
        .map((p) => `${p!.title}. ${p!.body}`)
        .join(" ");
      const created = await tx.cluster.create({
        data: {
          orgId,
          label: (labels?.[i] || c.label).slice(0, 80),
          summary: summarizeText(memberText, 220),
        },
      });
      await tx.post.updateMany({
        where: { id: { in: c.memberIds }, orgId },
        data: { clusterId: created.id },
      });
    }
  });

  return clusters.length;
}

type NewInsight = {
  kind: InsightKind;
  title: string;
  body: string;
  data?: Prisma.InputJsonValue;
};

function pct(now: number, before: number): number {
  if (before === 0) return now > 0 ? 100 : 0;
  return Math.round(((now - before) / before) * 100);
}

export async function generateInsights(orgId: string): Promise<number> {
  const now = new Date();
  const d30 = new Date(now.getTime() - 30 * 86400_000);
  const d60 = new Date(now.getTime() - 60 * 86400_000);
  const d45 = new Date(now.getTime() - 45 * 86400_000);

  const [recentPosts, priorPostCount, topPost, stalePlanned, clusters] =
    await Promise.all([
      db.post.findMany({
        where: { orgId, createdAt: { gte: d30 }, mergedIntoId: null },
        select: { sentiment: true, categoryId: true, category: { select: { name: true } } },
      }),
      db.post.count({
        where: { orgId, createdAt: { gte: d60, lt: d30 }, mergedIntoId: null },
      }),
      db.post.findFirst({
        where: { orgId, archived: false, mergedIntoId: null, type: "FEATURE_REQUEST" },
        orderBy: { voteCount: "desc" },
        select: { id: true, title: true, voteCount: true, status: true },
      }),
      db.post.findMany({
        where: { orgId, status: "PLANNED", updatedAt: { lt: d45 } },
        select: { title: true },
        take: 5,
      }),
      db.cluster.findMany({
        where: { orgId },
        include: { posts: { select: { createdAt: true, voteCount: true } } },
      }),
    ]);

  const insights: NewInsight[] = [];

  // Trend: fastest-growing cluster by 30d volume vs prior 30d.
  let best: { label: string; nowN: number; growth: number } | null = null;
  for (const c of clusters) {
    const nowN = c.posts.filter((p) => p.createdAt >= d30).length;
    const beforeN = c.posts.filter((p) => p.createdAt >= d60 && p.createdAt < d30).length;
    if (nowN >= 2) {
      const growth = pct(nowN, beforeN);
      if (!best || growth > best.growth) best = { label: c.label, nowN, growth };
    }
  }
  if (best && best.growth > 0) {
    insights.push({
      kind: "TREND",
      title: `${best.label} is your fastest-growing theme`,
      body: `Feedback about “${best.label}” grew ${best.growth}% over the last 30 days (${best.nowN} new items). Consider prioritizing this area.`,
      data: { cluster: best.label, growthPct: best.growth, items30d: best.nowN },
    });
  }

  // Volume summary + spike alert.
  const growth = pct(recentPosts.length, priorPostCount);
  insights.push({
    kind: "SUMMARY",
    title: `${recentPosts.length} new feedback items in the last 30 days`,
    body: `Volume is ${growth >= 0 ? "up" : "down"} ${Math.abs(growth)}% versus the previous 30 days.${topPost ? ` The most requested feature remains “${topPost.title}” with ${topPost.voteCount} votes.` : ""}`,
    data: { last30: recentPosts.length, prior30: priorPostCount, growthPct: growth },
  });

  // Sentiment alert.
  const neg = recentPosts.filter((p) => p.sentiment === "NEGATIVE").length;
  const negShare = recentPosts.length ? Math.round((neg / recentPosts.length) * 100) : 0;
  if (negShare >= 30 && neg >= 3) {
    const byCat = new Map<string, number>();
    for (const p of recentPosts) {
      if (p.sentiment === "NEGATIVE" && p.category?.name) {
        byCat.set(p.category.name, (byCat.get(p.category.name) ?? 0) + 1);
      }
    }
    const worst = [...byCat.entries()].sort((a, b) => b[1] - a[1])[0];
    insights.push({
      kind: "ALERT",
      title: `Negative sentiment at ${negShare}% of recent feedback`,
      body: `${neg} of the last ${recentPosts.length} items are negative${worst ? `, concentrated in ${worst[0]}` : ""}. Worth a closer look.`,
      data: { negativeShare: negShare, negativeCount: neg, topCategory: worst?.[0] ?? null },
    });
  }

  // Opportunity: top request not yet on the roadmap / stale planned items.
  if (topPost && (topPost.status === "OPEN" || topPost.status === "UNDER_CONSIDERATION")) {
    insights.push({
      kind: "OPPORTUNITY",
      title: `“${topPost.title}” is highly demanded but not planned`,
      body: `Your most-voted request (${topPost.voteCount} votes) is still ${topPost.status === "OPEN" ? "open" : "under consideration"}. Committing to it publicly could be a quick trust win.`,
      data: { postId: topPost.id, votes: topPost.voteCount },
    });
  } else if (stalePlanned.length > 0) {
    insights.push({
      kind: "OPPORTUNITY",
      title: `${stalePlanned.length} planned item${stalePlanned.length > 1 ? "s have" : " has"} been idle for 45+ days`,
      body: `Consider updating status or communicating timelines: ${stalePlanned.map((p) => `“${p.title}”`).slice(0, 3).join(", ")}.`,
      data: { titles: stalePlanned.map((p) => p.title) },
    });
  }

  // Optional narrative summary from the configured LLM provider.
  if (getProvider() && clusters.length > 0) {
    const narrative = await completeJSON<{ title?: string; body?: string }>(
      "You write one crisp executive insight about customer feedback.",
      [
        "Clusters (label: size):",
        ...clusters.map((c) => `- ${c.label}: ${c.posts.length} items, ${c.posts.reduce((s, p) => s + p.voteCount, 0)} votes`),
        `New items last 30 days: ${recentPosts.length} (${growth}% vs prior).`,
        'Return JSON {"title":"<max 80 chars>","body":"<2 sentences>"}',
      ].join("\n")
    );
    if (narrative?.title && narrative.body) {
      insights.push({
        kind: "SUMMARY",
        title: narrative.title.slice(0, 120),
        body: narrative.body.slice(0, 500),
        data: { source: "llm" },
      });
    }
  }

  await db.$transaction(async (tx) => {
    await tx.insight.deleteMany({ where: { orgId, dismissed: false } });
    await tx.insight.createMany({
      data: insights.map((i) => ({ orgId, ...i })),
    });
  });

  return insights.length;
}

/** Which analysis engine is active (for UI display). */
export function activeEngine(): { name: string; offline: boolean } {
  const provider = getProvider();
  if (!provider) return { name: "Heuristic (offline)", offline: true };
  return { name: provider.name === "anthropic" ? "Anthropic" : "OpenAI-compatible", offline: false };
}

// Re-export for convenience in results pages.
export { analyzeSentiment };
