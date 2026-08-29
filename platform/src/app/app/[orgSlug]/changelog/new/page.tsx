import { db } from "@/lib/db";
import { requireOrgPage } from "@/lib/auth/guards";
import { PageHeader } from "@/components/ui/misc";
import { ChangelogEditor } from "@/components/changelog/editor";

export const dynamic = "force-dynamic";

export default async function NewChangelogEntryPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug, "MEMBER");

  const shippedPosts = await db.post.findMany({
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
  });

  return (
    <div>
      <PageHeader
        title="New changelog entry"
        description="Draft your release notes — publish them when you're ready."
      />
      <ChangelogEditor orgSlug={orgSlug} shippedPosts={shippedPosts} />
    </div>
  );
}
