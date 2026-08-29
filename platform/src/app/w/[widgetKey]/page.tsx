import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { db } from "@/lib/db";
import {
  getViewer,
  listPublicPosts,
  toPortalPost,
  votedPostIds,
} from "@/components/portal/data";
import { accentStyle } from "@/components/portal/theme";
import { WidgetApp } from "@/components/portal/widget-app";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ widgetKey: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { widgetKey } = await params;
  const org = await db.organization.findFirst({
    where: { widgetKey, portalEnabled: true },
    select: { name: true },
  });
  return {
    title: org ? `${org.name} — Feedback widget` : "Feedback widget",
    robots: { index: false, follow: false },
  };
}

/**
 * Embeddable feedback widget: a compact, iframe-friendly page resolved by
 * the org's secret widget key. Read + vote + submit, zero auth required.
 */
export default async function WidgetPage({ params }: Props) {
  const { widgetKey } = await params;
  const org = await db.organization.findFirst({
    where: { widgetKey, portalEnabled: true },
    select: { id: true, name: true, slug: true, brandColor: true },
  });
  if (!org) notFound();

  const [{ user, guestId }, rows] = await Promise.all([
    getViewer(),
    listPublicPosts(org.id, { sort: "trending", limit: 10 }),
  ]);
  const voted = await votedPostIds(
    rows.map((r) => r.id),
    user?.id ?? null,
    guestId
  );
  const posts = rows.map((r) => toPortalPost(r, voted.has(r.id)));

  return (
    <div style={accentStyle(org.brandColor)} className="min-h-screen bg-surface">
      <WidgetApp
        orgSlug={org.slug}
        orgName={org.name}
        posts={posts}
        signedIn={!!user}
      />
    </div>
  );
}
