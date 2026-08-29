import Link from "next/link";
import { db } from "@/lib/db";
import { requireOrgPage } from "@/lib/auth/guards";
import { POST_STATUS } from "@/lib/status";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { PageHeader, EmptyState } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import {
  VolumeChart,
  VotesChart,
  SentimentTrendChart,
  BreakdownBars,
  type WeekPoint,
  type NamedCount,
} from "@/components/analytics/charts";

export const dynamic = "force-dynamic";

const RANGES = { "30": 30, "90": 90, "365": 365 } as const;
type RangeKey = keyof typeof RANGES;

function weekKey(d: Date): string {
  // ISO week start (Monday), rendered as "Mar 4"
  const day = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const dow = (day.getUTCDay() + 6) % 7;
  day.setUTCDate(day.getUTCDate() - dow);
  return day.toISOString().slice(0, 10);
}

function weekLabel(iso: string): string {
  return new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export default async function AnalyticsPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgSlug: string }>;
  searchParams: Promise<{ range?: string }>;
}) {
  const { orgSlug } = await params;
  const sp = await searchParams;
  const { org } = await requireOrgPage(orgSlug);
  const rangeKey: RangeKey = (Object.keys(RANGES) as RangeKey[]).includes(sp.range as RangeKey)
    ? (sp.range as RangeKey)
    : "90";
  const since = new Date(Date.now() - RANGES[rangeKey] * 86400_000);

  const [posts, votes, statusRows, categoryRows, categories, sourceRows, contributors] =
    await Promise.all([
      db.post.findMany({
        where: { orgId: org.id, mergedIntoId: null, createdAt: { gte: since } },
        select: { createdAt: true, type: true, sentiment: true },
      }),
      db.vote.findMany({
        where: { post: { orgId: org.id }, createdAt: { gte: since } },
        select: { createdAt: true },
      }),
      db.post.groupBy({
        by: ["status"],
        where: { orgId: org.id, mergedIntoId: null, archived: false },
        _count: true,
      }),
      db.post.groupBy({
        by: ["categoryId"],
        where: { orgId: org.id, mergedIntoId: null, createdAt: { gte: since } },
        _count: true,
      }),
      db.category.findMany({ where: { orgId: org.id } }),
      db.post.groupBy({
        by: ["source"],
        where: { orgId: org.id, mergedIntoId: null, createdAt: { gte: since } },
        _count: true,
      }),
      db.post.groupBy({
        by: ["authorId", "guestName"],
        where: { orgId: org.id, mergedIntoId: null, createdAt: { gte: since } },
        _count: true,
        orderBy: { _count: { id: "desc" } },
        take: 8,
      }),
    ]);

  // Build a continuous week series.
  const weeks = new Map<string, WeekPoint>();
  for (let t = since.getTime(); t <= Date.now() + 6 * 86400_000; t += 7 * 86400_000) {
    const key = weekKey(new Date(t));
    weeks.set(key, {
      week: weekLabel(key),
      feedback: 0, requests: 0, votes: 0, positive: 0, neutral: 0, negative: 0,
    });
  }
  for (const p of posts) {
    const w = weeks.get(weekKey(p.createdAt));
    if (!w) continue;
    if (p.type === "FEATURE_REQUEST") w.requests++;
    else w.feedback++;
    if (p.sentiment === "POSITIVE") w.positive++;
    else if (p.sentiment === "NEGATIVE") w.negative++;
    else w.neutral++;
  }
  for (const v of votes) {
    const w = weeks.get(weekKey(v.createdAt));
    if (w) w.votes++;
  }
  const series = [...weeks.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([, v]) => v);

  const statusData: NamedCount[] = statusRows
    .map((r) => ({
      name: POST_STATUS[r.status].label,
      count: r._count,
      color: POST_STATUS[r.status].color,
    }))
    .sort((a, b) => b.count - a.count);

  const catById = new Map(categories.map((c) => [c.id, c]));
  const categoryData: NamedCount[] = categoryRows
    .map((r) => ({
      name: r.categoryId ? catById.get(r.categoryId)?.name ?? "Unknown" : "Uncategorized",
      count: r._count,
      color: r.categoryId ? catById.get(r.categoryId)?.color : undefined,
    }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 8);

  const sourceData: NamedCount[] = sourceRows
    .map((r) => ({ name: r.source.charAt(0) + r.source.slice(1).toLowerCase(), count: r._count }))
    .sort((a, b) => b.count - a.count);

  const authorIds = contributors.map((c) => c.authorId).filter((x): x is string => !!x);
  const authors = await db.user.findMany({
    where: { id: { in: authorIds } },
    select: { id: true, name: true },
  });
  const authorName = new Map(authors.map((a) => [a.id, a.name]));

  const hasData = posts.length > 0 || votes.length > 0;

  return (
    <div>
      <PageHeader
        title="Analytics"
        description="How feedback, demand, and sentiment are moving over time."
        actions={
          <div className="inline-flex items-center rounded-lg border border-line bg-surface p-1">
            {(Object.keys(RANGES) as RangeKey[]).map((r) => (
              <Link
                key={r}
                href={`/app/${orgSlug}/analytics?range=${r}`}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs font-medium",
                  r === rangeKey ? "bg-surface-overlay text-ink" : "text-ink-faint hover:text-ink-muted"
                )}
              >
                {r === "365" ? "1y" : `${r}d`}
              </Link>
            ))}
          </div>
        }
      />

      {!hasData ? (
        <EmptyState
          title="Not enough data yet"
          description="Charts light up as feedback and votes arrive in this date range."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Feedback volume</CardTitle>
              <CardDescription>New posts per week, by type</CardDescription>
            </CardHeader>
            <CardContent><VolumeChart data={series} /></CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Votes per week</CardTitle>
            </CardHeader>
            <CardContent><VotesChart data={series} /></CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Sentiment trend</CardTitle>
              <CardDescription>Weekly mix of post sentiment</CardDescription>
            </CardHeader>
            <CardContent><SentimentTrendChart data={series} /></CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Status distribution</CardTitle>
              <CardDescription>All open, unmerged posts</CardDescription>
            </CardHeader>
            <CardContent><BreakdownBars data={statusData} /></CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Category breakdown</CardTitle>
              <CardDescription>Posts in range by category</CardDescription>
            </CardHeader>
            <CardContent><BreakdownBars data={categoryData} /></CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Feedback sources</CardTitle>
              <CardDescription>Where posts in this range came from</CardDescription>
            </CardHeader>
            <CardContent><BreakdownBars data={sourceData} /></CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Top contributors</CardTitle>
              <CardDescription>Most posts submitted in range</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="divide-y divide-line/60">
                {contributors.map((c, i) => (
                  <li key={i} className="flex items-center gap-3 py-2 text-sm">
                    <span className="w-5 text-center font-mono text-xs text-ink-faint">{i + 1}</span>
                    <span className="min-w-0 flex-1 truncate text-ink">
                      {c.authorId
                        ? authorName.get(c.authorId) ?? "Member"
                        : c.guestName || "Anonymous guest"}
                    </span>
                    <span className="text-xs text-ink-muted">{c._count} posts</span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
