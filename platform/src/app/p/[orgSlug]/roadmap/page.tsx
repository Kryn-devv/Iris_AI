import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronUp, Map } from "lucide-react";
import { db } from "@/lib/db";
import { POST_STATUS, ROADMAP_STATUSES } from "@/lib/status";
import { compactNumber } from "@/lib/utils";
import { EmptyState } from "@/components/ui/misc";
import { getPortalOrg, publicPostWhere } from "@/components/portal/data";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Roadmap" };

type Props = { params: Promise<{ orgSlug: string }> };

export default async function PortalRoadmapPage({ params }: Props) {
  const { orgSlug } = await params;
  const org = await getPortalOrg(orgSlug);
  if (!org) notFound();

  const posts = await db.post.findMany({
    where: {
      ...publicPostWhere(org.id),
      showOnRoadmap: true,
      status: { in: ROADMAP_STATUSES },
    },
    orderBy: [{ roadmapOrder: "asc" }, { voteCount: "desc" }, { createdAt: "desc" }],
    select: {
      id: true,
      title: true,
      status: true,
      voteCount: true,
      category: { select: { name: true, color: true } },
    },
  });

  if (posts.length === 0) {
    return (
      <EmptyState
        icon={<Map size={22} aria-hidden />}
        title="The roadmap is being drafted"
        description={`When ${org.name} commits to what's next, it will show up here.`}
      />
    );
  }

  const columns = ROADMAP_STATUSES.map((status) => ({
    status,
    meta: POST_STATUS[status],
    posts: posts.filter((p) => p.status === status),
  }));

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
      {columns.map(({ status, meta, posts: columnPosts }) => (
        <section key={status} aria-label={meta.label} className="min-w-0">
          <header className="mb-3 flex items-center gap-2">
            <span
              aria-hidden
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: meta.color }}
            />
            <h2 className="text-sm font-semibold text-ink">{meta.label}</h2>
            <span className="rounded-full border border-line bg-surface px-1.5 text-[11px] font-medium text-ink-faint">
              {columnPosts.length}
            </span>
          </header>
          <div className="space-y-2">
            {columnPosts.length === 0 ? (
              <p className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-xs text-ink-faint">
                Nothing here yet
              </p>
            ) : (
              columnPosts.map((post) => (
                <Link
                  key={post.id}
                  href={`/p/${org.slug}/posts/${post.id}`}
                  className="group block rounded-xl border border-line bg-surface-raised p-3.5 shadow-card transition-colors hover:border-line-strong"
                >
                  <p className="text-xs font-medium leading-snug text-ink transition-colors group-hover:text-accent-soft">
                    {post.title}
                  </p>
                  <p className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
                    <span className="inline-flex items-center gap-1 font-medium text-ink-muted">
                      <ChevronUp size={12} aria-hidden />
                      {compactNumber(post.voteCount)}
                    </span>
                    {post.category && (
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          aria-hidden
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ backgroundColor: post.category.color }}
                        />
                        {post.category.name}
                      </span>
                    )}
                  </p>
                </Link>
              ))
            )}
          </div>
        </section>
      ))}
    </div>
  );
}
