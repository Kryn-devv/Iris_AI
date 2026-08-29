import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { format } from "date-fns";
import { db } from "@/lib/db";
import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";
import { PageHeader } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChangelogEditor } from "@/components/changelog/editor";
import { EntryActions } from "@/components/changelog/entry-actions";
import { NotifyButton } from "@/components/changelog/notify-button";
import {
  EntryEngagement,
  type EntryComment,
  type ReactionGroup,
} from "@/components/changelog/engagement";

export const dynamic = "force-dynamic";

export default async function EditChangelogEntryPage({
  params,
}: {
  params: Promise<{ orgSlug: string; entryId: string }>;
}) {
  const { orgSlug, entryId } = await params;
  const ctx = await requireOrgPage(orgSlug, "MEMBER");
  const canModerate = roleAtLeast(ctx.role, "ADMIN");

  const [entry, shippedPosts, memberCount] = await Promise.all([
    db.changelogEntry.findFirst({
      where: { id: entryId, orgId: ctx.org.id },
      include: {
        author: { select: { name: true } },
        reactions: { select: { emoji: true } },
        comments: {
          orderBy: { createdAt: "desc" },
          include: { author: { select: { name: true } } },
        },
      },
    }),
    db.post.findMany({
      where: {
        orgId: ctx.org.id,
        showOnRoadmap: true,
        status: "SHIPPED",
        archived: false,
        mergedIntoId: null,
      },
      orderBy: [{ shippedAt: "desc" }, { updatedAt: "desc" }],
      take: 50,
      select: { id: true, title: true },
    }),
    db.membership.count({ where: { orgId: ctx.org.id } }),
  ]);

  if (!entry) notFound();

  const reactionCounts = new Map<string, number>();
  for (const r of entry.reactions) {
    reactionCounts.set(r.emoji, (reactionCounts.get(r.emoji) ?? 0) + 1);
  }
  const reactions: ReactionGroup[] = [...reactionCounts.entries()]
    .map(([emoji, count]) => ({ emoji, count }))
    .sort((a, b) => b.count - a.count);

  const comments: EntryComment[] = entry.comments.map((c) => ({
    id: c.id,
    body: c.body,
    createdAt: c.createdAt.toISOString(),
    authorName: c.author?.name ?? null,
    guestName: c.guestName,
  }));

  return (
    <div>
      <Link
        href={`/app/${orgSlug}/changelog`}
        className="mb-3 inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted transition-colors hover:text-ink"
      >
        <ArrowLeft size={13} aria-hidden />
        Back to changelog
      </Link>
      <PageHeader
        title="Edit changelog entry"
        description={
          entry.publishedAt
            ? `Published ${format(entry.publishedAt, "MMM d, yyyy")}${
                entry.author ? ` — by ${entry.author.name}` : ""
              }`
            : `Draft${entry.author ? ` — by ${entry.author.name}` : ""}`
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {entry.publishedAt ? (
              <Badge tone="success">Published</Badge>
            ) : (
              <Badge tone="warning">Draft</Badge>
            )}
            <NotifyButton
              orgSlug={orgSlug}
              entryId={entry.id}
              published={Boolean(entry.publishedAt)}
              notifiedAt={entry.notifiedAt ? entry.notifiedAt.toISOString() : null}
              memberCount={memberCount}
            />
            <EntryActions
              orgSlug={orgSlug}
              entryId={entry.id}
              entryTitle={entry.title}
              published={Boolean(entry.publishedAt)}
              canDelete={canModerate}
              showEditLink={false}
            />
          </div>
        }
      />
      <ChangelogEditor
        orgSlug={orgSlug}
        shippedPosts={shippedPosts}
        entry={{
          id: entry.id,
          title: entry.title,
          slug: entry.slug,
          version: entry.version,
          body: entry.body,
          labels: entry.labels,
          coverImageUrl: entry.coverImageUrl,
          videoUrl: entry.videoUrl,
        }}
      />
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Engagement</CardTitle>
        </CardHeader>
        <CardContent>
          <EntryEngagement
            orgSlug={orgSlug}
            entryId={entry.id}
            reactions={reactions}
            comments={comments}
            canModerate={canModerate}
          />
        </CardContent>
      </Card>
    </div>
  );
}
