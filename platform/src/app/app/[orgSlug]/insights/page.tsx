import Link from "next/link";
import { Sparkles, Boxes } from "lucide-react";
import { db } from "@/lib/db";
import { requireOrgPage } from "@/lib/auth/guards";
import { activeEngine } from "@/lib/ai/insights";
import { SENTIMENT_META } from "@/lib/status";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, EmptyState } from "@/components/ui/misc";
import { RunAnalysisButton } from "@/components/insights/run-analysis-button";
import { InsightCard } from "@/components/insights/insight-card";

export const dynamic = "force-dynamic";

export default async function InsightsPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { org } = await requireOrgPage(orgSlug);
  const engine = activeEngine();

  const [insights, clusters] = await Promise.all([
    db.insight.findMany({
      where: { orgId: org.id, dismissed: false },
      orderBy: { createdAt: "desc" },
      take: 20,
    }),
    db.cluster.findMany({
      where: { orgId: org.id },
      include: {
        posts: {
          where: { archived: false, mergedIntoId: null },
          orderBy: { voteCount: "desc" },
          select: { id: true, title: true, voteCount: true, sentiment: true },
        },
      },
    }),
  ]);
  const sortedClusters = clusters
    .filter((c) => c.posts.length > 0)
    .sort((a, b) => b.posts.length - a.posts.length);

  return (
    <div>
      <PageHeader
        title="Insights"
        description="AI-generated intelligence from your feedback — themes, trends, and what to do next."
        actions={<RunAnalysisButton orgSlug={orgSlug} />}
      />

      <div className="mb-5 flex items-center gap-2 text-xs text-ink-muted">
        <Badge tone={engine.offline ? "neutral" : "accent"}>
          <Sparkles size={11} /> Engine: {engine.name}
        </Badge>
        {engine.offline && (
          <span>
            Deterministic offline analysis. Set <code className="rounded bg-line/50 px-1">AI_PROVIDER</code>{" "}
            to anthropic or openai for LLM-powered summaries.
          </span>
        )}
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <section>
          <h2 className="mb-3 text-sm font-semibold text-ink">Insight feed</h2>
          {insights.length === 0 ? (
            <EmptyState
              icon={<Sparkles size={18} />}
              title="No insights yet"
              description="Run analysis to cluster your feedback and surface trends, alerts, and opportunities."
              action={<RunAnalysisButton orgSlug={orgSlug} />}
            />
          ) : (
            <div className="space-y-3">
              {insights.map((i) => (
                <InsightCard
                  key={i.id}
                  orgSlug={orgSlug}
                  insight={{ ...i, createdAt: i.createdAt.toISOString() }}
                />
              ))}
            </div>
          )}
        </section>

        <section>
          <h2 className="mb-3 text-sm font-semibold text-ink">Feedback clusters</h2>
          {sortedClusters.length === 0 ? (
            <EmptyState
              icon={<Boxes size={18} />}
              title="No clusters yet"
              description="Analysis groups similar feedback into themes so you can see what users keep asking for."
            />
          ) : (
            <div className="space-y-3">
              {sortedClusters.map((c) => {
                const mix = { POSITIVE: 0, NEUTRAL: 0, NEGATIVE: 0 };
                for (const p of c.posts) if (p.sentiment) mix[p.sentiment]++;
                const total = c.posts.length;
                return (
                  <Card key={c.id}>
                    <CardHeader className="flex-row items-center justify-between">
                      <CardTitle>{c.label}</CardTitle>
                      <span className="text-xs text-ink-faint">{total} posts</span>
                    </CardHeader>
                    <CardContent>
                      {c.summary && <CardDescription className="mb-3">{c.summary}</CardDescription>}
                      <div className="mb-3 flex h-1.5 overflow-hidden rounded-full bg-line/40">
                        {(Object.keys(mix) as (keyof typeof mix)[]).map((k) =>
                          mix[k] > 0 ? (
                            <span
                              key={k}
                              title={`${SENTIMENT_META[k].label}: ${mix[k]}`}
                              style={{
                                width: `${(mix[k] / total) * 100}%`,
                                background:
                                  k === "POSITIVE" ? "#4ade80" : k === "NEGATIVE" ? "#f87171" : "#646c86",
                              }}
                            />
                          ) : null
                        )}
                      </div>
                      <ul className="space-y-1.5">
                        {c.posts.slice(0, 3).map((p) => (
                          <li key={p.id} className="flex items-center justify-between gap-2 text-xs">
                            <Link
                              href={`/app/${orgSlug}/feedback/${p.id}`}
                              className="min-w-0 truncate text-ink-muted hover:text-accent-soft"
                            >
                              {p.title}
                            </Link>
                            <span className="shrink-0 text-ink-faint">{p.voteCount} ▲</span>
                          </li>
                        ))}
                      </ul>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
