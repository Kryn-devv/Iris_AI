import { api, ok, fail } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

type Ctx = { params: Promise<{ orgSlug: string; entryId: string }> };

/**
 * POST /api/orgs/[orgSlug]/changelog/[entryId]/notify — create an in-app
 * notification for every org member and stamp notifiedAt. MEMBER+.
 * One-shot: refuses when the entry was already notified.
 */
export async function POST(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, entryId } = await params;
    const ctx = await requireOrg(orgSlug, "MEMBER");

    const entry = await db.changelogEntry.findFirst({
      where: { id: entryId, orgId: ctx.org.id },
      select: { id: true, title: true, publishedAt: true, notifiedAt: true },
    });
    if (!entry) return fail(404, "Changelog entry not found");
    if (!entry.publishedAt) {
      return fail(400, "Publish the entry before notifying members");
    }
    if (entry.notifiedAt) {
      return fail(400, "Members were already notified about this release");
    }

    const memberships = await db.membership.findMany({
      where: { orgId: ctx.org.id },
      select: { userId: true },
    });

    const notified = await db.$transaction(async (tx) => {
      // Guard against a concurrent notify: only proceed if we win the stamp.
      const stamped = await tx.changelogEntry.updateMany({
        where: { id: entry.id, orgId: ctx.org.id, notifiedAt: null },
        data: { notifiedAt: new Date() },
      });
      if (stamped.count === 0) return -1;
      if (memberships.length === 0) return 0;
      const created = await tx.notification.createMany({
        data: memberships.map((m) => ({
          userId: m.userId,
          orgId: ctx.org.id,
          title: `New release: ${entry.title}`,
          body: "A new changelog entry was published.",
          href: `/p/${orgSlug}/changelog`,
        })),
      });
      return created.count;
    });

    if (notified === -1) {
      return fail(400, "Members were already notified about this release");
    }

    await recordActivity(
      ctx.org.id,
      "changelog.notified",
      { entryId: entry.id, title: entry.title, notified },
      ctx.user.id
    );

    return ok({ notified });
  });
}
