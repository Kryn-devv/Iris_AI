import { api, ok, fail } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import { recomputePriority } from "../../helpers";

type Ctx = { params: Promise<{ orgSlug: string; postId: string }> };

/**
 * POST /api/orgs/[orgSlug]/posts/[postId]/vote — toggle the current team
 * member's vote. voteCount and priorityScore update in the same transaction.
 */
export async function POST(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const { org, user } = await requireOrg(orgSlug);

    const post = await db.post.findFirst({
      where: { id: postId, orgId: org.id },
      select: { id: true, title: true, mergedIntoId: true },
    });
    if (!post) return fail(404, "Post not found");
    if (post.mergedIntoId) {
      return fail(400, "This post was merged into another one — vote there instead");
    }

    const result = await db.$transaction(async (tx) => {
      const existing = await tx.vote.findFirst({
        where: { postId: post.id, userId: user.id },
        select: { id: true },
      });
      if (existing) {
        await tx.vote.delete({ where: { id: existing.id } });
      } else {
        await tx.vote.create({ data: { postId: post.id, userId: user.id } });
      }
      const voteCount = await tx.vote.count({ where: { postId: post.id } });
      await tx.post.update({
        where: { id: post.id },
        data: { voteCount },
      });
      const priorityScore = await recomputePriority(tx, post.id);
      return { voted: !existing, voteCount, priorityScore };
    });

    if (result.voted) {
      await recordActivity(
        org.id,
        "vote.added",
        { postId: post.id, title: post.title },
        user.id
      );
    }

    return ok(result);
  });
}
