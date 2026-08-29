import { Suspense } from "react";
import { Inbox } from "lucide-react";
import { db } from "@/lib/db";
import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";
import { Card } from "@/components/ui/card";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { NewPostButton } from "@/components/posts/new-post-dialog";
import { PostFilters } from "@/components/posts/post-filters";
import { PostListItem } from "@/components/posts/post-list-item";
import { Pagination } from "@/components/posts/pagination";
import {
  buildPostOrderBy,
  buildPostWhere,
  parsePostFilters,
  POSTS_PAGE_SIZE,
} from "@/app/api/orgs/[orgSlug]/posts/helpers";

export const dynamic = "force-dynamic";

export default async function FeedbackPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgSlug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgSlug } = await params;
  const sp = await searchParams;
  const ctx = await requireOrgPage(orgSlug);

  const filters = parsePostFilters(sp);
  const page = Math.max(1, Number(sp.page) || 1);
  const where = buildPostWhere(ctx.org.id, filters);

  const [total, posts, categories, tags, boards] = await Promise.all([
    db.post.count({ where }),
    db.post.findMany({
      where,
      orderBy: buildPostOrderBy(filters.sort),
      skip: (page - 1) * POSTS_PAGE_SIZE,
      take: POSTS_PAGE_SIZE,
      select: {
        id: true,
        title: true,
        aiSummary: true,
        status: true,
        type: true,
        sentiment: true,
        source: true,
        voteCount: true,
        commentCount: true,
        pinned: true,
        archived: true,
        createdAt: true,
        guestName: true,
        author: { select: { name: true } },
        category: { select: { name: true, color: true } },
        tags: {
          include: { tag: { select: { id: true, name: true, color: true } } },
        },
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

  const totalPages = Math.max(1, Math.ceil(total / POSTS_PAGE_SIZE));
  const canCreate = roleAtLeast(ctx.role, "MEMBER");
  const anyFilter = ["status", "type", "category", "tag", "sentiment", "source", "q", "archived"].some(
    (k) => typeof sp[k] === "string" && sp[k]
  );

  return (
    <div>
      <PageHeader
        title="Feedback"
        description={`${total} post${total === 1 ? "" : "s"} — collect, triage and prioritize what your users are asking for.`}
        actions={
          canCreate ? (
            <NewPostButton
              orgSlug={orgSlug}
              categories={categories}
              tags={tags}
              boards={boards}
            />
          ) : undefined
        }
      />
      <Suspense>
        <PostFilters categories={categories} tags={tags} />
      </Suspense>
      {posts.length === 0 ? (
        <EmptyState
          icon={<Inbox size={28} aria-hidden />}
          title={anyFilter ? "Nothing matches these filters" : "No feedback yet"}
          description={
            anyFilter
              ? "Try clearing a filter or changing your search."
              : "Create the first post, or bring feedback in from your portal, widget or a CSV import."
          }
          action={
            canCreate && !anyFilter ? (
              <NewPostButton
                orgSlug={orgSlug}
                categories={categories}
                tags={tags}
                boards={boards}
              />
            ) : undefined
          }
        />
      ) : (
        <Card>
          {posts.map((post) => (
            <PostListItem key={post.id} post={post} orgSlug={orgSlug} />
          ))}
        </Card>
      )}
      <Pagination
        page={page}
        totalPages={totalPages}
        basePath={`/app/${orgSlug}/feedback`}
        searchParams={sp}
      />
    </div>
  );
}
