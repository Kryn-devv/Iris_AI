import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import { recomputePriority } from "../../helpers";

type Ctx = { params: Promise<{ orgSlug: string; postId: string }> };

/** GET /api/orgs/[orgSlug]/posts/[postId]/comments — full thread, oldest first. */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const { org } = await requireOrg(orgSlug);
    const post = await db.post.findFirst({
      where: { id: postId, orgId: org.id },
      select: { id: true },
    });
    if (!post) return fail(404, "Post not found");
    const comments = await db.comment.findMany({
      where: { postId: post.id },
      orderBy: { createdAt: "asc" },
      include: { author: { select: { id: true, name: true, avatarUrl: true } } },
    });
    return ok({ comments });
  });
}

const CreateBody = z.object({
  body: z.string().trim().min(1).max(5_000),
  parentId: z.string().optional(),
});

/**
 * POST /api/orgs/[orgSlug]/posts/[postId]/comments — team comment
 * (isTeam=true), optionally replying to an existing comment (one level deep;
 * replies to replies are flattened onto the top-level parent).
 * commentCount + priorityScore update in the same transaction.
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const { org, user } = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, CreateBody);

    const post = await db.post.findFirst({
      where: { id: postId, orgId: org.id },
      select: { id: true, title: true },
    });
    if (!post) return fail(404, "Post not found");

    let parentId: string | null = null;
    if (body.parentId) {
      const parent = await db.comment.findFirst({
        where: { id: body.parentId, postId: post.id },
        select: { id: true, parentId: true },
      });
      if (!parent) return fail(400, "Unknown parent comment");
      parentId = parent.parentId ?? parent.id; // keep nesting to one level
    }

    const comment = await db.$transaction(async (tx) => {
      const created = await tx.comment.create({
        data: {
          postId: post.id,
          authorId: user.id,
          body: body.body,
          isTeam: true,
          parentId,
        },
        include: {
          author: { select: { id: true, name: true, avatarUrl: true } },
        },
      });
      const commentCount = await tx.comment.count({
        where: { postId: post.id },
      });
      await tx.post.update({
        where: { id: post.id },
        data: { commentCount },
      });
      await recomputePriority(tx, post.id);
      return created;
    });

    await recordActivity(
      org.id,
      "comment.created",
      { postId: post.id, title: post.title, commentId: comment.id },
      user.id
    );

    return ok({ comment }, { status: 201 });
  });
}
