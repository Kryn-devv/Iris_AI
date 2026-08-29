import Link from "next/link";
import {
  MessageSquare,
  TrendingUp,
  ThumbsUp,
  ClipboardList,
  ArrowUpRight,
  Sparkles,
  CheckCircle2,
  Activity as ActivityIcon,
  Megaphone,
  Vote,
  PlusCircle,
} from "lucide-react";
import { db } from "@/lib/db";
import { requireOrgPage } from "@/lib/auth/guards";
import { POST_STATUS, ROADMAP_STATUSES } from "@/lib/status";
import { compactNumber, timeAgo } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader, EmptyState } from "@/components/ui/misc";
import { SentimentDonut } from "@/components/dashboard/sentiment-donut";

export const dynamic = "force-dynamic";

function StatCard({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  icon: React.ReactNode;
}) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between px-5 py-4">
        <div>
          <p className="text-xs text-ink-muted">{label}</p>
          <p className="mt-1 text-2xl font-semibold tracking-tight text-ink">{value}</p>
          {sub && <p className="mt-0.5 text-[11px] text-ink-faint">{sub}</p>}
        </div>
        <span className="rounded-lg bg-accent/10 p-2 text-accent-soft">{icon}</span>
      </CardContent>
    </Card>
  );
}

const ACTIVITY_ICONS: Record<string, React.ReactNode> = {
  "post.created": <MessageSquare size={13} />,
  "post.status_changed": <CheckCircle2 size={13} />,
  "vote.added": <Vote size={13} />,
  "changelog.published": <Megaphone size={13} />,
  "survey.response": <ClipboardList size={13} />,
  "insights.generated": <Sparkles size={13} />,
};

function activityText(type: string, meta: Record<string, unknown> | null): string {
  const title = typeof meta?.title === "string" ? `“${meta.title}”` : "";
  switch (type) {
    case "post.created": return `New feedback ${title}`;
    case "post.status_changed": return `Status changed on ${title || "a post"}${typeof meta?.status === "string" ? ` → ${POST_STATUS[meta.status as keyof typeof POST_STATUS]?.label ?? meta.status}` : ""}`;
    case "vote.added": return `New vote on ${title || "a post"}`;
    case "changelog.published": return `Published ${title || "a changelog entry"}`;
    case "survey.response": return "New survey response";
    case "insights.generated": return "AI analysis completed";
    case "org.created": return "Workspace created";
    default: return type.replace(/[._]/g, " ");
  }
}

export default async function DashboardPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { org } = await requireOrgPage(orgSlug);
  const now = Date.now();
  const d7 = new Date(now - 7 * 86400_000);
  const d14 = new Date(now - 14 * 86400_000);
  const d30 = new Date(now - 30 * 86400_000);
  const basePosts = { orgId: org.id, mergedIntoId: null, archived: false } as const;

  const [
    totalFeedback,
    newThisWeek,
    prevWeek,
    totalVotes,
    activeSurveys,
    topRequest,
    trending,
    sentimentRows,
    roadmapPosts,
    activities,
    latestInsight,
  ] = await Promise.all([
    db.post.count({ where: basePosts }),
    db.post.count({ where: { ...basePosts, createdAt: { gte: d7 } } }),
    db.post.count({ where: { ...basePosts, createdAt: { gte: d14, lt: d7 } } }),
    db.vote.count({ where: { post: { orgId: org.id } } }),
    db.survey.count({ where: { orgId: org.id, status: "ACTIVE" } }),
    db.post.findFirst({
      where: { ...basePosts, type: "FEATURE_REQUEST" },
      orderBy: { voteCount: "desc" },
      select: { id: true, title: true, voteCount: true, status: true, commentCount: true },
    }),
    db.post.findMany({
      where: basePosts,
      orderBy: { priorityScore: "desc" },
      take: 5,
      select: { id: true, title: true, status: true, voteCount: true, priorityScore: true },
    }),
    db.post.groupBy({
      by: ["sentiment"],
      where: { ...basePosts, createdAt: { gte: d30 } },
      _count: true,
    }),
    db.post.groupBy({
      by: ["status"],
      where: { orgId: org.id, showOnRoadmap: true, mergedIntoId: null, archived: false },
      _count: true,
    }),
    db.activity.findMany({
      where: { orgId: org.id },
      orderBy: { createdAt: "desc" },
      take: 12,
      include: { actor: { select: { name: true } } },
    }),
    db.insight.findFirst({
      where: { orgId: org.id, dismissed: false },
      orderBy: { createdAt: "desc" },
    }),
  ]);

  const weekDelta = newThisWeek - prevWeek;
  const sentiment = { positive: 0, neutral: 0, negative: 0 };
  for (const row of sentimentRows) {
    if (row.sentiment === "POSITIVE") sentiment.positive = row._count;
    else if (row.sentiment === "NEGATIVE") sentiment.negative = row._count;
    else sentiment.neutral += row._count;
  }
  const roadmapTotal = roadmapPosts.reduce((s, r) => s + r._count, 0);
  const shipped = roadmapPosts.find((r) => r.status === "SHIPPED")?._count ?? 0;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description={`What's happening across ${org.name}'s feedback right now.`}
        actions={
          <Link
            href={`/app/${orgSlug}/feedback`}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-medium text-white hover:bg-accent-strong"
          >
            <PlusCircle size={15} /> New feedback
          </Link>
        }
      />

      {latestInsight && (
        <Link
          href={`/app/${orgSlug}/insights`}
          className="mb-5 flex items-start gap-3 rounded-xl border border-accent/25 bg-accent/8 px-4 py-3 hover:bg-accent/15"
        >
          <Sparkles size={15} className="mt-0.5 shrink-0 text-accent-soft" />
          <span className="text-sm text-ink">
            <span className="font-medium">{latestInsight.title}.</span>{" "}
            <span className="text-ink-muted">{latestInsight.body}</span>
          </span>
        </Link>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Total feedback"
          value={compactNumber(totalFeedback)}
          icon={<MessageSquare size={16} />}
        />
        <StatCard
          label="New this week"
          value={String(newThisWeek)}
          sub={
            <span className={weekDelta >= 0 ? "text-success" : "text-danger"}>
              {weekDelta >= 0 ? "+" : ""}
              {weekDelta} vs last week
            </span>
          }
          icon={<TrendingUp size={16} />}
        />
        <StatCard label="Total votes" value={compactNumber(totalVotes)} icon={<ThumbsUp size={16} />} />
        <StatCard label="Active surveys" value={String(activeSurveys)} icon={<ClipboardList size={16} />} />
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {topRequest && (
            <Card className="border-accent/25">
              <CardHeader>
                <CardTitle className="text-xs uppercase tracking-wider text-ink-faint">
                  Most requested
                </CardTitle>
              </CardHeader>
              <CardContent className="flex items-center justify-between gap-4">
                <div>
                  <Link
                    href={`/app/${orgSlug}/feedback/${topRequest.id}`}
                    className="text-base font-semibold text-ink hover:text-accent-soft"
                  >
                    {topRequest.title}
                  </Link>
                  <div className="mt-1.5 flex items-center gap-2 text-xs text-ink-muted">
                    <Badge tone={POST_STATUS[topRequest.status].tone}>
                      {POST_STATUS[topRequest.status].label}
                    </Badge>
                    <span>{topRequest.commentCount} comments</span>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-3xl font-semibold text-accent-soft">
                    {compactNumber(topRequest.voteCount)}
                  </p>
                  <p className="text-[11px] text-ink-faint">votes</p>
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>Trending by priority</CardTitle>
            </CardHeader>
            <CardContent>
              {trending.length === 0 ? (
                <EmptyState title="No feedback yet" description="New posts will appear here ranked by priority score." />
              ) : (
                <ul className="divide-y divide-line/60">
                  {trending.map((p, i) => (
                    <li key={p.id}>
                      <Link
                        href={`/app/${orgSlug}/feedback/${p.id}`}
                        className="flex items-center gap-3 py-2.5 hover:bg-surface-overlay/40"
                      >
                        <span className="w-5 text-center font-mono text-xs text-ink-faint">{i + 1}</span>
                        <span className="min-w-0 flex-1 truncate text-sm text-ink">{p.title}</span>
                        <Badge tone={POST_STATUS[p.status].tone}>{POST_STATUS[p.status].label}</Badge>
                        <span className="w-14 text-right text-xs text-ink-muted">{p.voteCount} ▲</span>
                        <span className="w-14 text-right font-mono text-xs text-accent-soft">
                          {Math.round(p.priorityScore)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>Roadmap progress</CardTitle>
              <Link
                href={`/app/${orgSlug}/roadmap`}
                className="inline-flex items-center gap-1 text-xs text-accent-soft hover:underline"
              >
                Open roadmap <ArrowUpRight size={12} />
              </Link>
            </CardHeader>
            <CardContent>
              {roadmapTotal === 0 ? (
                <EmptyState title="Nothing on the roadmap yet" description="Add posts to the roadmap to track progress here." />
              ) : (
                <>
                  <div className="mb-2 flex h-2.5 overflow-hidden rounded-full bg-line/50">
                    {ROADMAP_STATUSES.map((s) => {
                      const n = roadmapPosts.find((r) => r.status === s)?._count ?? 0;
                      if (!n) return null;
                      return (
                        <span
                          key={s}
                          style={{
                            width: `${(n / roadmapTotal) * 100}%`,
                            background: POST_STATUS[s].color,
                          }}
                          className="border-r-2 border-surface-raised last:border-0"
                        />
                      );
                    })}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-muted">
                    {ROADMAP_STATUSES.map((s) => (
                      <span key={s} className="inline-flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full" style={{ background: POST_STATUS[s].color }} />
                        {POST_STATUS[s].label} · {roadmapPosts.find((r) => r.status === s)?._count ?? 0}
                      </span>
                    ))}
                    <span className="ml-auto text-ink-faint">
                      {Math.round((shipped / roadmapTotal) * 100)}% shipped
                    </span>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Sentiment · last 30 days</CardTitle>
            </CardHeader>
            <CardContent>
              {sentiment.positive + sentiment.neutral + sentiment.negative === 0 ? (
                <EmptyState title="No recent feedback" />
              ) : (
                <SentimentDonut {...sentiment} />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Recent activity</CardTitle>
            </CardHeader>
            <CardContent>
              {activities.length === 0 ? (
                <EmptyState icon={<ActivityIcon size={18} />} title="No activity yet" />
              ) : (
                <ul className="space-y-2.5">
                  {activities.map((a) => (
                    <li key={a.id} className="flex items-start gap-2.5 text-xs">
                      <span className="mt-0.5 rounded bg-line/50 p-1 text-ink-faint">
                        {ACTIVITY_ICONS[a.type] ?? <ActivityIcon size={13} />}
                      </span>
                      <span className="min-w-0 flex-1 text-ink-muted">
                        {activityText(a.type, a.meta as Record<string, unknown> | null)}
                        <span className="mt-0.5 block text-[10px] text-ink-faint">
                          {a.actor?.name ?? "Someone"} · {timeAgo(a.createdAt)}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
