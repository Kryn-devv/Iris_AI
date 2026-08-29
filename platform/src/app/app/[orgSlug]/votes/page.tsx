import Link from "next/link";
import { ChevronUp, TrendingDown, TrendingUp, Trophy, Users } from "lucide-react";
import { db } from "@/lib/db";
import { requireOrgPage } from "@/lib/auth/guards";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { POST_STATUS } from "@/lib/status";
import { compactNumber, timeAgo } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function VotesPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug);
  const orgId = ctx.org.id;

  const now = Date.now();
  const weekAgo = new Date(now - 7 * 86400_000);
  const twoWeeksAgo = new Date(now - 14 * 86400_000);

  const [totalVotes, votesThisWeek, votesLastWeek, distinctVoters, topPosts, recentVotes] =
    await Promise.all([
      db.vote.count({ where: { post: { orgId } } }),
      db.vote.count({
        where: { post: { orgId }, createdAt: { gte: weekAgo } },
      }),
      db.vote.count({
        where: {
          post: { orgId },
          createdAt: { gte: twoWeeksAgo, lt: weekAgo },
        },
      }),
      db.vote.findMany({
        where: { post: { orgId } },
        select: { userId: true, guestId: true },
        distinct: ["userId", "guestId"],
      }),
      db.post.findMany({
        where: { orgId, mergedIntoId: null, archived: false },
        orderBy: [{ voteCount: "desc" }, { createdAt: "desc" }],
        take: 10,
        select: {
          id: true,
          title: true,
          status: true,
          voteCount: true,
          commentCount: true,
        },
      }),
      db.vote.findMany({
        where: { post: { orgId } },
        orderBy: { createdAt: "desc" },
        take: 30,
        select: {
          id: true,
          createdAt: true,
          user: { select: { name: true } },
          post: { select: { id: true, title: true } },
        },
      }),
    ]);

  const uniqueVoters = distinctVoters.length;
  const delta = votesThisWeek - votesLastWeek;
  const maxVotes = Math.max(1, ...topPosts.map((p) => p.voteCount));

  const stats = [
    {
      label: "Total votes",
      value: compactNumber(totalVotes),
      icon: <ChevronUp size={16} aria-hidden />,
      sub: "across all posts",
    },
    {
      label: "Votes this week",
      value: compactNumber(votesThisWeek),
      icon:
        delta >= 0 ? (
          <TrendingUp size={16} aria-hidden />
        ) : (
          <TrendingDown size={16} aria-hidden />
        ),
      sub: `${delta >= 0 ? "+" : ""}${delta} vs previous week`,
      tone: delta >= 0 ? "text-success" : "text-danger",
    },
    {
      label: "Unique voters",
      value: compactNumber(uniqueVoters),
      icon: <Users size={16} aria-hidden />,
      sub: "team members and portal visitors",
    },
  ];

  return (
    <div>
      <PageHeader
        title="Votes"
        description="Demand signals at a glance — who is voting, and what they want most."
      />

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        {stats.map((s) => (
          <Card key={s.label}>
            <CardContent className="pt-4">
              <div className="flex items-center justify-between text-ink-faint">
                <span className="text-xs font-medium uppercase tracking-wide">
                  {s.label}
                </span>
                {s.icon}
              </div>
              <p className="mt-2 font-display text-2xl font-semibold text-ink">
                {s.value}
              </p>
              <p className={`mt-1 text-xs ${s.tone ?? "text-ink-faint"}`}>{s.sub}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5">
              <Trophy size={13} aria-hidden className="text-warning" />
              Most wanted
            </CardTitle>
          </CardHeader>
          <CardContent>
            {topPosts.length === 0 ? (
              <EmptyState
                title="No posts to rank yet"
                description="Votes on feedback will build this leaderboard."
                className="py-8"
              />
            ) : (
              <ol className="space-y-2.5">
                {topPosts.map((post, i) => {
                  const status = POST_STATUS[post.status];
                  return (
                    <li key={post.id} className="flex items-center gap-3">
                      <span
                        aria-hidden
                        className="w-5 shrink-0 text-right text-xs font-semibold tabular-nums text-ink-faint"
                      >
                        {i + 1}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <Link
                            href={`/app/${orgSlug}/feedback/${post.id}`}
                            className="truncate text-sm font-medium text-ink hover:text-accent-soft"
                          >
                            {post.title}
                          </Link>
                          <Badge tone={status.tone}>{status.label}</Badge>
                        </div>
                        <div
                          aria-hidden
                          className="mt-1 h-1 overflow-hidden rounded-full bg-line/50"
                        >
                          <div
                            className="h-full rounded-full bg-accent-gradient"
                            style={{
                              width: `${Math.max(4, (post.voteCount / maxVotes) * 100)}%`,
                            }}
                          />
                        </div>
                      </div>
                      <span className="shrink-0 text-sm font-semibold tabular-nums text-ink">
                        {compactNumber(post.voteCount)}
                      </span>
                    </li>
                  );
                })}
              </ol>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent votes</CardTitle>
          </CardHeader>
          <CardContent>
            {recentVotes.length === 0 ? (
              <EmptyState
                title="No votes yet"
                description="Votes from your team and portal visitors will appear here as they come in."
                className="py-8"
              />
            ) : (
              <ul className="max-h-[420px] space-y-2.5 overflow-y-auto pr-1">
                {recentVotes.map((v) => (
                  <li
                    key={v.id}
                    className="flex items-baseline justify-between gap-3 text-sm"
                  >
                    <p className="min-w-0 flex-1 truncate text-ink-muted">
                      <span className="font-medium text-ink">
                        {v.user?.name ?? "Guest"}
                      </span>{" "}
                      voted for{" "}
                      <Link
                        href={`/app/${orgSlug}/feedback/${v.post.id}`}
                        className="text-accent-soft hover:underline"
                      >
                        {v.post.title}
                      </Link>
                    </p>
                    <span className="shrink-0 text-[11px] text-ink-faint">
                      {timeAgo(v.createdAt)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
