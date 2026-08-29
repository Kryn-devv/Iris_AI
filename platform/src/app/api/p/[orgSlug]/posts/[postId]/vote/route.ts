import { api, ok, fail } from "@/lib/api";
import { db } from "@/lib/db";
import { ensureGuestId, getCurrentUser } from "@/lib/auth/session";
import { recordActivity } from "@/lib/events";
import {
  getPortalOrg,
  publicPostWhere,
  recomputePortalPriority,
} from "@/components/portal/data";

type Ctx = { params: Promise<{ orgSlug: string; postId: string }> };

/**
 * POST /api/p/[orgSlug]/posts/[postId]/vote — toggle the viewer's vote on a
 * public post. Signed-in users vote by userId, anonymous visitors by the
 * guest cookie id. voteCount + priorityScore update in one transaction.
 */
export async function POST(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const org = await getPortalOrg(orgSlug);
    if (!org) return fail(404, "Portal not found");

    const post = await db.post.findFirst({
      where: { ...publicPostWhere(org.id), id: postId },
      select: { id: true, title: true },
    });
    if (!post) return fail(404, "Post not found");

    const user = await getCurrentUser();
    const guestId = user ? null : await ensureGuestId();
    const identity = user ? { userId: user.id } : { guestId: guestId! };

    const result = await db.$transaction(async (tx) => {
      const existing = await tx.vote.findFirst({
        where: { postId: post.id, ...identity },
        select: { id: true },
      });
      if (existing) {
        await tx.vote.delete({ where: { id: existing.id } });
      } else {
        await tx.vote.create({ data: { postId: post.id, ...identity } });
      }
      const voteCount = await tx.vote.count({ where: { postId: post.id } });
      await tx.post.update({ where: { id: post.id }, data: { voteCount } });
      await recomputePortalPriority(tx, post.id);
      return { voted: !existing, voteCount };
    });

    if (result.voted) {
      await recordActivity(
        org.id,
        "vote.added",
        {
          postId: post.id,
          title: post.title,
          ...(guestId ? { guestId } : {}),
        },
        user?.id
      );
    }

    return ok(result);
  });
}
