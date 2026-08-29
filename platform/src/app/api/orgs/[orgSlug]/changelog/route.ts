import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import {
  EntryBody,
  isValidCoverUrl,
  isValidVideoUrl,
  uniqueEntrySlug,
} from "./helpers";

type Ctx = { params: Promise<{ orgSlug: string }> };

/**
 * GET /api/orgs/[orgSlug]/changelog — all entries for the org,
 * drafts first, then published (newest first).
 */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug);
    const entries = await db.changelogEntry.findMany({
      where: { orgId: org.id },
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
        author: { select: { id: true, name: true } },
        _count: { select: { reactions: true, comments: true } },
      },
    });
    return ok({ entries });
  });
}

/** POST /api/orgs/[orgSlug]/changelog — create an entry (MEMBER+). */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const ctx = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, EntryBody);

    if (body.videoUrl && !isValidVideoUrl(body.videoUrl)) {
      return fail(400, "Video URL must be an https link (YouTube, Vimeo, …)");
    }
    if (body.coverImageUrl && !isValidCoverUrl(body.coverImageUrl)) {
      return fail(400, "Cover image URL is not valid");
    }

    const slug = await uniqueEntrySlug(ctx.org.id, body.slug || body.title);

    const entry = await db.changelogEntry.create({
      data: {
        orgId: ctx.org.id,
        title: body.title,
        slug,
        version: body.version || null,
        body: body.body,
        labels: body.labels ?? [],
        coverImageUrl: body.coverImageUrl || null,
        videoUrl: body.videoUrl || null,
        authorId: ctx.user.id,
      },
      select: { id: true, slug: true },
    });

    await recordActivity(
      ctx.org.id,
      "changelog.created",
      { entryId: entry.id, title: body.title },
      ctx.user.id
    );

    return ok({ id: entry.id, slug: entry.slug }, { status: 201 });
  });
}
