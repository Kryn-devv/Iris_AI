import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { db } from "@/lib/db";
import { ensureGuestId, getCurrentUser } from "@/lib/auth/session";
import { recordActivity } from "@/lib/events";
import { getPortalOrg } from "@/components/portal/data";
import type { PortalComment } from "@/components/portal/types";

type Ctx = { params: Promise<{ orgSlug: string; entryId: string }> };

const COMMENT_SELECT = {
  id: true,
  body: true,
  guestName: true,
  createdAt: true,
  authorId: true,
  author: { select: { id: true, name: true } },
} as const;

type RawEntryComment = {
  id: string;
  body: string;
  guestName: string | null;
  createdAt: Date;
  authorId: string | null;
  author: { id: string; name: string } | null;
};

function serialize(c: RawEntryComment, teamIds: Set<string>): PortalComment {
  return {
    id: c.id,
    name: c.author?.name ?? c.guestName?.trim() ?? "Anonymous",
    isTeam: Boolean(c.authorId && teamIds.has(c.authorId)),
    body: c.body,
    createdAt: c.createdAt.toISOString(),
  };
}

async function findPublishedEntry(orgId: string, entryId: string) {
  return db.changelogEntry.findFirst({
    where: { id: entryId, orgId, publishedAt: { not: null } },
    select: { id: true, title: true },
  });
}

/** Member ids for the org — used to badge team-authored comments. */
async function teamMemberIds(orgId: string, userIds: string[]): Promise<Set<string>> {
  if (userIds.length === 0) return new Set();
  const memberships = await db.membership.findMany({
    where: { orgId, userId: { in: userIds } },
    select: { userId: true },
  });
  return new Set(memberships.map((m) => m.userId));
}

/** GET /api/p/[orgSlug]/changelog/[entryId]/comments — public comments. */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, entryId } = await params;
    const org = await getPortalOrg(orgSlug);
    if (!org) return fail(404, "Portal not found");
    const entry = await findPublishedEntry(org.id, entryId);
    if (!entry) return fail(404, "Changelog entry not found");

    const comments = await db.changelogComment.findMany({
      where: { entryId: entry.id },
      orderBy: { createdAt: "asc" },
      take: 200,
      select: COMMENT_SELECT,
    });
    const teamIds = await teamMemberIds(
      org.id,
      comments.map((c) => c.authorId).filter((id): id is string => Boolean(id))
    );
    return ok({ comments: comments.map((c) => serialize(c, teamIds)) });
  });
}

const CreateBody = z.object({
  body: z.string().trim().min(1, "Write a comment first").max(2000),
  guestName: z.string().trim().max(80).optional(),
});

/** POST /api/p/[orgSlug]/changelog/[entryId]/comments — guest-friendly. */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, entryId } = await params;
    const org = await getPortalOrg(orgSlug);
    if (!org) return fail(404, "Portal not found");
    const entry = await findPublishedEntry(org.id, entryId);
    if (!entry) return fail(404, "Changelog entry not found");

    const body = await parseBody(req, CreateBody);
    const user = await getCurrentUser();
    const guestId = user ? null : await ensureGuestId();

    const comment = await db.changelogComment.create({
      data: {
        entryId: entry.id,
        body: body.body,
        authorId: user?.id ?? null,
        guestName: user ? null : body.guestName?.trim() || null,
      },
      select: COMMENT_SELECT,
    });
    const teamIds = user ? await teamMemberIds(org.id, [user.id]) : new Set<string>();

    await recordActivity(
      org.id,
      "changelog.commented",
      {
        entryId: entry.id,
        title: entry.title,
        ...(guestId ? { guestId } : {}),
      },
      user?.id
    );

    return ok({ comment: serialize(comment, teamIds) }, { status: 201 });
  });
}
