import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import {
  EntryBody,
  isValidCoverUrl,
  isValidVideoUrl,
  uniqueEntrySlug,
} from "../helpers";

type Ctx = { params: Promise<{ orgSlug: string; entryId: string }> };

/** GET /api/orgs/[orgSlug]/changelog/[entryId] — full entry with engagement. */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, entryId } = await params;
    const { org } = await requireOrg(orgSlug);
    const entry = await db.changelogEntry.findFirst({
      where: { id: entryId, orgId: org.id },
      include: {
        author: { select: { id: true, name: true, avatarUrl: true } },
        comments: {
          orderBy: { createdAt: "desc" },
          include: { author: { select: { id: true, name: true } } },
        },
        _count: { select: { reactions: true, comments: true } },
      },
    });
    if (!entry) return fail(404, "Changelog entry not found");
    return ok({ entry });
  });
}

const PatchBody = EntryBody.partial();

/** PATCH /api/orgs/[orgSlug]/changelog/[entryId] — edit an entry (MEMBER+). */
export async function PATCH(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, entryId } = await params;
    const ctx = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, PatchBody);

    const entry = await db.changelogEntry.findFirst({
      where: { id: entryId, orgId: ctx.org.id },
      select: { id: true, slug: true, title: true },
    });
    if (!entry) return fail(404, "Changelog entry not found");

    if (body.videoUrl && !isValidVideoUrl(body.videoUrl)) {
      return fail(400, "Video URL must be an https link (YouTube, Vimeo, …)");
    }
    if (body.coverImageUrl && !isValidCoverUrl(body.coverImageUrl)) {
      return fail(400, "Cover image URL is not valid");
    }

    let slug: string | undefined;
    if (body.slug !== undefined && body.slug !== entry.slug) {
      slug = await uniqueEntrySlug(
        ctx.org.id,
        body.slug || body.title || entry.title,
        entry.id
      );
    }

    const updated = await db.changelogEntry.update({
      where: { id: entry.id },
      data: {
        ...(body.title !== undefined ? { title: body.title } : {}),
        ...(slug !== undefined ? { slug } : {}),
        ...(body.version !== undefined ? { version: body.version || null } : {}),
        ...(body.body !== undefined ? { body: body.body } : {}),
        ...(body.labels !== undefined ? { labels: body.labels } : {}),
        ...(body.coverImageUrl !== undefined
          ? { coverImageUrl: body.coverImageUrl || null }
          : {}),
        ...(body.videoUrl !== undefined
          ? { videoUrl: body.videoUrl || null }
          : {}),
      },
      select: { id: true, slug: true },
    });

    await recordActivity(
      ctx.org.id,
      "changelog.updated",
      { entryId: entry.id, title: body.title ?? entry.title },
      ctx.user.id
    );

    return ok({ id: updated.id, slug: updated.slug });
  });
}

/** DELETE /api/orgs/[orgSlug]/changelog/[entryId] — ADMIN+. */
export async function DELETE(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, entryId } = await params;
    const ctx = await requireOrg(orgSlug, "ADMIN");
    const entry = await db.changelogEntry.findFirst({
      where: { id: entryId, orgId: ctx.org.id },
      select: { id: true, title: true },
    });
    if (!entry) return fail(404, "Changelog entry not found");
    await db.changelogEntry.delete({ where: { id: entry.id } });
    await recordActivity(
      ctx.org.id,
      "changelog.deleted",
      { entryId: entry.id, title: entry.title },
      ctx.user.id
    );
    return ok({ deleted: true });
  });
}
