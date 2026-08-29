import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import { recomputePriority } from "../../helpers";

type Ctx = { params: Promise<{ orgSlug: string; postId: string }> };

const Body = z.object({ targetId: z.string().min(1) });

/**
 * POST /api/orgs/[orgSlug]/posts/[postId]/merge — mark this post as a
 * duplicate of `targetId`. Votes move to the target unless the voter already
 * voted there (duplicates are skipped). Both posts' denormalized counts and
 * priority scores are recomputed in the same transaction.
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const { org, user } = await requireOrg(orgSlug, "MEMBER");
    const { targetId } = await parseBody(req, Body);

    if (targetId === postId) {
      return fail(400, "A post cannot be merged into itself");
    }

    const [source, target] = await Promise.all([
      db.post.findFirst({
        where: { id: postId, orgId: org.id },
        select: { id: true, title: true, mergedIntoId: true },
      }),
      db.post.findFirst({
        where: { id: targetId, orgId: org.id },
        select: { id: true, title: true, mergedIntoId: true },
      }),
    ]);
    if (!source) return fail(404, "Post not found");
    if (!target) return fail(400, "Unknown target post");
    if (source.mergedIntoId) return fail(400, "This post is already merged");
    if (target.mergedIntoId) {
      return fail(400, "The target post is itself merged into another post");
    }

    const counts = await db.$transaction(async (tx) => {
      const [sourceVotes, targetVotes] = await Promise.all([
        tx.vote.findMany({
          where: { postId: source.id },
          select: { id: true, userId: true, guestId: true },
        }),
        tx.vote.findMany({
          where: { postId: target.id },
          select: { userId: true, guestId: true },
        }),
      ]);
      const targetUsers = new Set(
        targetVotes.map((v) => v.userId).filter(Boolean) as string[]
      );
      const targetGuests = new Set(
        targetVotes.map((v) => v.guestId).filter(Boolean) as string[]
      );
      const movable = sourceVotes.filter(
        (v) =>
          !(v.userId && targetUsers.has(v.userId)) &&
          !(v.guestId && targetGuests.has(v.guestId))
      );
      if (movable.length) {
        await tx.vote.updateMany({
          where: { id: { in: movable.map((v) => v.id) } },
          data: { postId: target.id },
        });
      }

      await tx.post.update({
        where: { id: source.id },
        data: { mergedIntoId: target.id },
      });

      const result = { moved: movable.length, sourceVotes: 0, targetVotes: 0 };
      for (const id of [source.id, target.id]) {
        const [voteCount, commentCount] = await Promise.all([
          tx.vote.count({ where: { postId: id } }),
          tx.comment.count({ where: { postId: id } }),
        ]);
        await tx.post.update({
          where: { id },
          data: { voteCount, commentCount },
        });
        await recomputePriority(tx, id);
        if (id === source.id) result.sourceVotes = voteCount;
        else result.targetVotes = voteCount;
      }
      return result;
    });

    await recordActivity(
      org.id,
      "post.merged",
      {
        postId: source.id,
        sourceTitle: source.title,
        targetId: target.id,
        targetTitle: target.title,
        votesMoved: counts.moved,
      },
      user.id
    );

    return ok({ targetId: target.id, ...counts });
  });
}
