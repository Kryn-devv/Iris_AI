import Link from "next/link";
import { Info, ListOrdered } from "lucide-react";
import { db } from "@/lib/db";
import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { POST_STATUS } from "@/lib/status";
import { compactNumber } from "@/lib/utils";
import { NewPostButton } from "@/components/posts/new-post-dialog";
import { InlineScoreSelect } from "@/components/posts/inline-score";
import { Pagination } from "@/components/posts/pagination";
import { POSTS_PAGE_SIZE } from "@/app/api/orgs/[orgSlug]/posts/helpers";

export const dynamic = "force-dynamic";

/** Formula tooltip content — mirrors the @/lib/priority docblock. */
const FORMULA = [
  "Demand: log2(1+votes)×8 + log2(1+comments)×4",
  "Value: (impact ÷ effort)×6 + log10(1+revenue)×4",
  "Momentum: freshness boost fading over ~90 days",
  "Urgency: strongly negative sentiment adds up to 8",
];

export default async function RequestsPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgSlug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgSlug } = await params;
  const sp = await searchParams;
  const ctx = await requireOrgPage(orgSlug);
  const page = Math.max(1, Number(sp.page) || 1);

  const where = {
    orgId: ctx.org.id,
    type: "FEATURE_REQUEST" as const,
    mergedIntoId: null,
    archived: false,
  };

  const [total, posts, categories, tags, boards] = await Promise.all([
    db.post.count({ where }),
    db.post.findMany({
      where,
      orderBy: [{ priorityScore: "desc" }, { voteCount: "desc" }, { id: "desc" }],
      skip: (page - 1) * POSTS_PAGE_SIZE,
      take: POSTS_PAGE_SIZE,
      select: {
        id: true,
        title: true,
        status: true,
        voteCount: true,
        commentCount: true,
        impact: true,
        effort: true,
        revenueImpact: true,
        priorityScore: true,
        category: { select: { name: true, color: true } },
      },
    }),
    db.category.findMany({
      where: { orgId: ctx.org.id },
      orderBy: { name: "asc" },
      select: { id: true, name: true, color: true },
    }),
    db.tag.findMany({
      where: { orgId: ctx.org.id },
      orderBy: { name: "asc" },
      select: { id: true, name: true, color: true },
    }),
    db.board.findMany({
      where: { orgId: ctx.org.id },
      orderBy: { createdAt: "asc" },
      select: { id: true, name: true },
    }),
  ]);

  const canEdit = roleAtLeast(ctx.role, "MEMBER");
  const totalPages = Math.max(1, Math.ceil(total / POSTS_PAGE_SIZE));

  return (
    <div>
      <PageHeader
        title="Feature requests"
        description="Every open feature request, ranked by priority score. Set impact and effort inline to reshuffle the ranking."
        actions={
          canEdit ? (
            <NewPostButton
              orgSlug={orgSlug}
              categories={categories}
              tags={tags}
              boards={boards}
              defaultType="FEATURE_REQUEST"
            />
          ) : undefined
        }
      />

      {posts.length === 0 ? (
        <EmptyState
          icon={<ListOrdered size={28} aria-hidden />}
          title="No feature requests yet"
          description="Feature requests created here or on your portal show up in this prioritization table."
          action={
            canEdit ? (
              <NewPostButton
                orgSlug={orgSlug}
                categories={categories}
                tags={tags}
                boards={boards}
                defaultType="FEATURE_REQUEST"
              />
            ) : undefined
          }
        />
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-wide text-ink-faint">
                <th className="px-4 py-2.5 font-medium">#</th>
                <th className="px-4 py-2.5 font-medium">Request</th>
                <th className="px-3 py-2.5 text-right font-medium">Votes</th>
                <th className="px-3 py-2.5 text-right font-medium">Comments</th>
                <th className="px-3 py-2.5 font-medium">Impact</th>
                <th className="px-3 py-2.5 font-medium">Effort</th>
                <th className="px-3 py-2.5 text-right font-medium">Revenue</th>
                <th className="px-3 py-2.5 text-right font-medium">
                  <span className="group relative inline-flex items-center gap-1">
                    Score
                    <Info size={12} aria-hidden className="text-ink-faint" />
                    <span
                      role="tooltip"
                      className="pointer-events-none absolute right-0 top-full z-10 mt-1 hidden w-64 rounded-lg border border-line bg-surface-overlay p-3 text-left normal-case tracking-normal shadow-card group-hover:block"
                    >
                      <span className="mb-1 block text-xs font-semibold text-ink">
                        Priority score = demand + value + momentum + urgency
                      </span>
                      {FORMULA.map((line) => (
                        <span
                          key={line}
                          className="block text-[11px] font-normal leading-4 text-ink-muted"
                        >
                          {line}
                        </span>
                      ))}
                    </span>
                  </span>
                </th>
                <th className="px-4 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {posts.map((post, i) => {
                const status = POST_STATUS[post.status];
                return (
                  <tr
                    key={post.id}
                    className="border-b border-line last:border-b-0 hover:bg-surface-overlay/40"
                  >
                    <td className="px-4 py-2.5 text-xs text-ink-faint">
                      {(page - 1) * POSTS_PAGE_SIZE + i + 1}
                    </td>
                    <td className="max-w-[280px] px-4 py-2.5">
                      <Link
                        href={`/app/${orgSlug}/feedback/${post.id}`}
                        className="block truncate font-medium text-ink hover:text-accent-soft"
                      >
                        {post.title}
                      </Link>
                      {post.category && (
                        <span className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-ink-faint">
                          <span
                            aria-hidden
                            className="h-1.5 w-1.5 rounded-full"
                            style={{ backgroundColor: post.category.color }}
                          />
                          {post.category.name}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-ink-muted">
                      {compactNumber(post.voteCount)}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-ink-muted">
                      {compactNumber(post.commentCount)}
                    </td>
                    <td className="px-3 py-2.5">
                      <InlineScoreSelect
                        orgSlug={orgSlug}
                        postId={post.id}
                        field="impact"
                        value={post.impact}
                        disabled={!canEdit}
                      />
                    </td>
                    <td className="px-3 py-2.5">
                      <InlineScoreSelect
                        orgSlug={orgSlug}
                        postId={post.id}
                        field="effort"
                        value={post.effort}
                        disabled={!canEdit}
                      />
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-ink-muted">
                      {post.revenueImpact != null
                        ? `$${compactNumber(post.revenueImpact)}`
                        : "—"}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <span className="font-semibold tabular-nums text-accent-soft">
                        {post.priorityScore.toFixed(1)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge tone={status.tone}>{status.label}</Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}

      <Pagination
        page={page}
        totalPages={totalPages}
        basePath={`/app/${orgSlug}/requests`}
        searchParams={sp}
      />
    </div>
  );
}
