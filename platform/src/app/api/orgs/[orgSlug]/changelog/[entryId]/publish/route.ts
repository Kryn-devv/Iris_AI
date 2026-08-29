import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity, dispatchWebhooks } from "@/lib/events";

type Ctx = { params: Promise<{ orgSlug: string; entryId: string }> };

const Body = z.object({ published: z.boolean() });

/**
 * POST /api/orgs/[orgSlug]/changelog/[entryId]/publish — publish (idempotent)
 * or unpublish an entry. MEMBER+.
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, entryId } = await params;
    const ctx = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, Body);

    const entry = await db.changelogEntry.findFirst({
      where: { id: entryId, orgId: ctx.org.id },
      select: { id: true, title: true, slug: true, version: true, publishedAt: true },
    });
    if (!entry) return fail(404, "Changelog entry not found");

    if (body.published) {
      // Idempotent: already published entries keep their original timestamp.
      if (entry.publishedAt) {
        return ok({ id: entry.id, publishedAt: entry.publishedAt });
      }
      const publishedAt = new Date();
      await db.changelogEntry.update({
        where: { id: entry.id },
        data: { publishedAt },
      });
      await recordActivity(
        ctx.org.id,
        "changelog.published",
        { entryId: entry.id, title: entry.title, version: entry.version },
        ctx.user.id
      );
      await dispatchWebhooks(ctx.org.id, "changelog.published", {
        entryId: entry.id,
        title: entry.title,
        slug: entry.slug,
        version: entry.version,
        publishedAt: publishedAt.toISOString(),
      });
      return ok({ id: entry.id, publishedAt });
    }

    // Unpublish (back to draft). Idempotent when already a draft.
    if (entry.publishedAt) {
      await db.changelogEntry.update({
        where: { id: entry.id },
        data: { publishedAt: null },
      });
      await recordActivity(
        ctx.org.id,
        "changelog.unpublished",
        { entryId: entry.id, title: entry.title },
        ctx.user.id
      );
    }
    return ok({ id: entry.id, publishedAt: null });
  });
}
