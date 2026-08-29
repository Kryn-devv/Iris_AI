import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Megaphone } from "lucide-react";
import { format } from "date-fns";
import { db } from "@/lib/db";
import { Markdown } from "@/lib/markdown";
import { CHANGELOG_LABEL_META } from "@/lib/status";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/misc";
import { getPortalOrg, getViewer } from "@/components/portal/data";
import { isVideoFile, youTubeEmbedUrl } from "@/components/portal/theme";
import { ChangelogReactions } from "@/components/portal/changelog-reactions";
import { ChangelogComments } from "@/components/portal/changelog-comments";
import {
  REACTION_EMOJIS,
  type PortalComment,
  type ReactionEmoji,
} from "@/components/portal/types";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Changelog" };

type Props = { params: Promise<{ orgSlug: string }> };

export default async function PortalChangelogPage({ params }: Props) {
  const { orgSlug } = await params;
  const org = await getPortalOrg(orgSlug);
  if (!org) notFound();

  const [{ user, guestId }, entries] = await Promise.all([
    getViewer(),
    db.changelogEntry.findMany({
      where: { orgId: org.id, publishedAt: { not: null, lte: new Date() } },
      orderBy: { publishedAt: "desc" },
      take: 30,
      select: {
        id: true,
        title: true,
        version: true,
        body: true,
        coverImageUrl: true,
        videoUrl: true,
        labels: true,
        publishedAt: true,
        reactions: { select: { emoji: true, userId: true, guestId: true } },
        comments: {
          orderBy: { createdAt: "asc" },
          take: 100,
          select: {
            id: true,
            body: true,
            guestName: true,
            createdAt: true,
            authorId: true,
            author: { select: { name: true } },
          },
        },
      },
    }),
  ]);

  if (entries.length === 0) {
    return (
      <EmptyState
        icon={<Megaphone size={22} aria-hidden />}
        title="No updates published yet"
        description={`When ${org.name} ships something new, you'll read about it here.`}
      />
    );
  }

  // Badge team-authored comments across all entries with one membership query.
  const authorIds = Array.from(
    new Set(
      entries
        .flatMap((e) => e.comments.map((c) => c.authorId))
        .filter((id): id is string => Boolean(id))
    )
  );
  const memberships = authorIds.length
    ? await db.membership.findMany({
        where: { orgId: org.id, userId: { in: authorIds } },
        select: { userId: true },
      })
    : [];
  const teamIds = new Set(memberships.map((m) => m.userId));

  return (
    <div className="space-y-10">
      {entries.map((entry) => {
        const counts: Partial<Record<ReactionEmoji, number>> = {};
        const mine: ReactionEmoji[] = [];
        for (const r of entry.reactions) {
          if (!(REACTION_EMOJIS as readonly string[]).includes(r.emoji)) continue;
          const emoji = r.emoji as ReactionEmoji;
          counts[emoji] = (counts[emoji] ?? 0) + 1;
          const isMine = user
            ? r.userId === user.id
            : Boolean(guestId && r.guestId === guestId);
          if (isMine) mine.push(emoji);
        }

        const comments: PortalComment[] = entry.comments.map((c) => ({
          id: c.id,
          name: c.author?.name ?? c.guestName?.trim() ?? "Anonymous",
          isTeam: Boolean(c.authorId && teamIds.has(c.authorId)),
          body: c.body,
          createdAt: c.createdAt.toISOString(),
        }));

        const embedUrl = entry.videoUrl ? youTubeEmbedUrl(entry.videoUrl) : null;

        return (
          <article
            key={entry.id}
            className="grid grid-cols-1 gap-4 sm:grid-cols-[140px_1fr]"
          >
            <div className="text-xs text-ink-faint sm:pt-1.5 sm:text-right">
              {entry.publishedAt && (
                <time dateTime={entry.publishedAt.toISOString()} className="block">
                  {format(entry.publishedAt, "MMM d, yyyy")}
                </time>
              )}
              {entry.version && (
                <span className="mt-1.5 inline-block rounded-full border border-line bg-surface px-2 py-0.5 font-mono text-[11px] text-ink-muted">
                  {entry.version}
                </span>
              )}
            </div>

            <div className="min-w-0 space-y-4 rounded-xl border border-line bg-surface-raised p-5 shadow-card">
              <header className="space-y-2">
                {entry.labels.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {entry.labels.map((label) => {
                      const meta = CHANGELOG_LABEL_META[label];
                      return (
                        <Badge key={label} tone={meta?.tone ?? "neutral"}>
                          {meta?.label ?? label}
                        </Badge>
                      );
                    })}
                  </div>
                )}
                <h2 className="font-display text-lg font-semibold tracking-tight text-ink">
                  {entry.title}
                </h2>
              </header>

              {entry.coverImageUrl && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={entry.coverImageUrl}
                  alt=""
                  className="max-h-72 w-full rounded-lg border border-line object-cover"
                />
              )}

              <Markdown source={entry.body} />

              {entry.videoUrl &&
                (embedUrl ? (
                  <div className="overflow-hidden rounded-lg border border-line">
                    <iframe
                      src={embedUrl}
                      title={`Video: ${entry.title}`}
                      className="aspect-video w-full"
                      allow="accelerometer; encrypted-media; gyroscope; picture-in-picture"
                      allowFullScreen
                      referrerPolicy="strict-origin-when-cross-origin"
                    />
                  </div>
                ) : isVideoFile(entry.videoUrl) ? (
                  // eslint-disable-next-line jsx-a11y/media-has-caption
                  <video
                    src={entry.videoUrl}
                    controls
                    className="w-full rounded-lg border border-line"
                  />
                ) : (
                  <a
                    href={entry.videoUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-block text-xs font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 hover:text-accent"
                  >
                    Watch the video
                  </a>
                ))}

              <ChangelogReactions
                orgSlug={org.slug}
                entryId={entry.id}
                counts={counts}
                mine={mine}
              />

              <ChangelogComments
                orgSlug={org.slug}
                entryId={entry.id}
                initialComments={comments}
                signedIn={!!user}
              />
            </div>
          </article>
        );
      })}
    </div>
  );
}
