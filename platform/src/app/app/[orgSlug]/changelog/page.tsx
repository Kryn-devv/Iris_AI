import Link from "next/link";
import { ExternalLink, Megaphone, MessageCircle, Plus, SmilePlus } from "lucide-react";
import { format } from "date-fns";
import { db } from "@/lib/db";
import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { CHANGELOG_LABEL_META } from "@/lib/status";
import { EntryActions } from "@/components/changelog/entry-actions";

export const dynamic = "force-dynamic";

export default async function ChangelogPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug);
  const canCreate = roleAtLeast(ctx.role, "MEMBER");
  const canDelete = roleAtLeast(ctx.role, "ADMIN");

  const entries = await db.changelogEntry.findMany({
    where: { orgId: ctx.org.id },
    orderBy: [
      { publishedAt: { sort: "desc", nulls: "first" } },
      { createdAt: "desc" },
    ],
    select: {
      id: true,
      title: true,
      slug: true,
      version: true,
      labels: true,
      publishedAt: true,
      notifiedAt: true,
      createdAt: true,
      author: { select: { name: true } },
      _count: { select: { reactions: true, comments: true } },
    },
  });

  const drafts = entries.filter((e) => !e.publishedAt).length;

  return (
    <div>
      <PageHeader
        title="Changelog"
        description={`${entries.length} entr${entries.length === 1 ? "y" : "ies"}${
          drafts ? ` — ${drafts} draft${drafts === 1 ? "" : "s"}` : ""
        }. Tell users what you shipped.`}
        actions={
          <div className="flex items-center gap-2">
            <Link
              href={`/p/${orgSlug}/changelog`}
              target="_blank"
              className="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium text-ink-muted transition-colors hover:bg-surface-overlay hover:text-ink"
            >
              <ExternalLink size={13} aria-hidden />
              Public changelog
            </Link>
            {canCreate && (
              <Link
                href={`/app/${orgSlug}/changelog/new`}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-xs font-medium text-white shadow-glow transition-colors hover:bg-accent-strong"
              >
                <Plus size={14} aria-hidden />
                New entry
              </Link>
            )}
          </div>
        }
      />
      {entries.length === 0 ? (
        <EmptyState
          icon={<Megaphone size={28} aria-hidden />}
          title="No changelog entries yet"
          description="Write your first release notes and publish them to your public changelog."
          action={
            canCreate ? (
              <Link
                href={`/app/${orgSlug}/changelog/new`}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-medium text-white shadow-glow transition-colors hover:bg-accent-strong"
              >
                <Plus size={14} aria-hidden />
                Write the first entry
              </Link>
            ) : undefined
          }
        />
      ) : (
        <Card>
          <ul className="divide-y divide-line">
            {entries.map((entry) => (
              <li
                key={entry.id}
                className="flex flex-wrap items-center gap-3 px-5 py-3.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    {canCreate ? (
                      <Link
                        href={`/app/${orgSlug}/changelog/${entry.id}`}
                        className="truncate text-sm font-medium text-ink hover:text-accent-soft"
                      >
                        {entry.title}
                      </Link>
                    ) : (
                      <span className="truncate text-sm font-medium text-ink">
                        {entry.title}
                      </span>
                    )}
                    {entry.version && (
                      <Badge tone="neutral" className="font-mono">
                        {entry.version}
                      </Badge>
                    )}
                    {entry.labels.map((label) => {
                      const meta = CHANGELOG_LABEL_META[label];
                      return (
                        <Badge key={label} tone={meta?.tone ?? "neutral"}>
                          {meta?.label ?? label}
                        </Badge>
                      );
                    })}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-ink-muted">
                    {entry.publishedAt ? (
                      <Badge tone="success">
                        Published {format(entry.publishedAt, "MMM d, yyyy")}
                      </Badge>
                    ) : (
                      <Badge tone="warning">Draft</Badge>
                    )}
                    {entry.author && <span>by {entry.author.name}</span>}
                    <span
                      className="inline-flex items-center gap-1"
                      title="Reactions"
                    >
                      <SmilePlus size={12} aria-hidden />
                      {entry._count.reactions}
                    </span>
                    <span
                      className="inline-flex items-center gap-1"
                      title="Comments"
                    >
                      <MessageCircle size={12} aria-hidden />
                      {entry._count.comments}
                    </span>
                    {entry.notifiedAt && (
                      <span className="text-ink-faint">members notified</span>
                    )}
                  </div>
                </div>
                {canCreate && (
                  <EntryActions
                    orgSlug={orgSlug}
                    entryId={entry.id}
                    entryTitle={entry.title}
                    published={Boolean(entry.publishedAt)}
                    canDelete={canDelete}
                  />
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
