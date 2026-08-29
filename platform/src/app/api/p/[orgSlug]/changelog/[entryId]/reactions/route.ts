import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { db } from "@/lib/db";
import { ensureGuestId, getCurrentUser } from "@/lib/auth/session";
import { getPortalOrg } from "@/components/portal/data";
import { REACTION_EMOJIS } from "@/components/portal/types";

type Ctx = { params: Promise<{ orgSlug: string; entryId: string }> };

const Body = z.object({
  emoji: z.enum(REACTION_EMOJIS),
});

/**
 * POST /api/p/[orgSlug]/changelog/[entryId]/reactions — toggle one emoji
 * reaction per viewer (user or guest cookie) on a published entry.
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, entryId } = await params;
    const org = await getPortalOrg(orgSlug);
    if (!org) return fail(404, "Portal not found");

    const entry = await db.changelogEntry.findFirst({
      where: { id: entryId, orgId: org.id, publishedAt: { not: null } },
      select: { id: true },
    });
    if (!entry) return fail(404, "Changelog entry not found");

    const { emoji } = await parseBody(req, Body);
    const user = await getCurrentUser();
    const guestId = user ? null : await ensureGuestId();
    const identity = user ? { userId: user.id } : { guestId: guestId! };

    const result = await db.$transaction(async (tx) => {
      const existing = await tx.changelogReaction.findFirst({
        where: { entryId: entry.id, emoji, ...identity },
        select: { id: true },
      });
      if (existing) {
        await tx.changelogReaction.delete({ where: { id: existing.id } });
      } else {
        await tx.changelogReaction.create({
          data: { entryId: entry.id, emoji, ...identity },
        });
      }
      const count = await tx.changelogReaction.count({
        where: { entryId: entry.id, emoji },
      });
      return { reacted: !existing, count };
    });

    return ok(result);
  });
}
