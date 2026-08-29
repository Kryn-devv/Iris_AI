import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Inbox } from "lucide-react";
import type { PostStatus } from "@prisma/client";
import { db } from "@/lib/db";
import { POST_STATUS } from "@/lib/status";
import { EmptyState } from "@/components/ui/misc";
import {
  getPortalOrg,
  getViewer,
  listPublicPosts,
  toPortalPost,
  votedPostIds,
} from "@/components/portal/data";
import { PORTAL_SORTS, type PortalSort } from "@/components/portal/types";
import { BoardToolbar } from "@/components/portal/board-toolbar";
import { PostCard } from "@/components/portal/post-card";
import { ShareIdea } from "@/components/portal/share-idea";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Feedback" };

type Props = {
  params: Promise<{ orgSlug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function PortalBoardPage({ params, searchParams }: Props) {
  const { orgSlug } = await params;
  const sp = await searchParams;
  const org = await getPortalOrg(orgSlug);
  if (!org) notFound();

  const q =
    typeof sp.q === "string" && sp.q.trim() ? sp.q.trim().slice(0, 200) : undefined;
  const statusRaw = typeof sp.status === "string" ? sp.status : undefined;
  const status =
    statusRaw && statusRaw in POST_STATUS ? (statusRaw as PostStatus) : undefined;
  const sortRaw = typeof sp.sort === "string" ? sp.sort : undefined;
  const sort: PortalSort = PORTAL_SORTS.includes(sortRaw as PortalSort)
    ? (sortRaw as PortalSort)
    : "trending";

  const [{ user, guestId }, rows, categories] = await Promise.all([
    getViewer(),
    listPublicPosts(org.id, { q, status, sort, limit: 60 }),
    db.category.findMany({
      where: { orgId: org.id },
      orderBy: { name: "asc" },
      select: { id: true, name: true },
    }),
  ]);
  const voted = await votedPostIds(
    rows.map((r) => r.id),
    user?.id ?? null,
    guestId
  );
  const posts = rows.map((r) => toPortalPost(r, voted.has(r.id)));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <BoardToolbar q={q ?? ""} status={status ?? ""} sort={sort} />
        </div>
        <ShareIdea orgSlug={org.slug} categories={categories} signedIn={!!user} />
      </div>

      {posts.length === 0 ? (
        <EmptyState
          icon={<Inbox size={22} aria-hidden />}
          title={q || status ? "Nothing matches those filters" : "No ideas yet"}
          description={
            q || status
              ? "Try clearing the search or picking a different status."
              : `Be the first to tell ${org.name} what to build next.`
          }
        />
      ) : (
        <div className="space-y-2.5">
          {posts.map((post) => (
            <PostCard key={post.id} orgSlug={org.slug} post={post} />
          ))}
        </div>
      )}
    </div>
  );
}
