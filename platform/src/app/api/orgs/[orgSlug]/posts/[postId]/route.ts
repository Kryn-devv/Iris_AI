import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg, roleAtLeast } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity, dispatchWebhooks } from "@/lib/events";
import { recomputePriority, POST_STATUSES, POST_TYPES } from "../helpers";

type Ctx = { params: Promise<{ orgSlug: string; postId: string }> };

/** GET /api/orgs/[orgSlug]/posts/[postId] — full post with relations. */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const { org } = await requireOrg(orgSlug);
    const post = await db.post.findFirst({
      where: { id: postId, orgId: org.id },
      include: {
        category: true,
        board: { select: { id: true, name: true } },
        author: { select: { id: true, name: true, avatarUrl: true } },
        tags: { include: { tag: true } },
        attachments: true,
        mergedInto: { select: { id: true, title: true } },
        mergedPosts: { select: { id: true, title: true, voteCount: true } },
      },
    });
    if (!post) return fail(404, "Post not found");
    return ok({ post });
  });
}

const PatchBody = z.object({
  title: z.string().trim().min(1).max(200).optional(),
  body: z.string().trim().min(1).max(10_000).optional(),
  type: z.enum(POST_TYPES).optional(),
  status: z.enum(POST_STATUSES).optional(),
  categoryId: z.string().nullable().optional(),
  tagIds: z.array(z.string()).max(20).optional(),
  impact: z.number().int().min(1).max(5).nullable().optional(),
  effort: z.number().int().min(1).max(5).nullable().optional(),
  revenueImpact: z.number().int().min(0).max(1_000_000_000).nullable().optional(),
  pinned: z.boolean().optional(),
  archived: z.boolean().optional(),
});

/**
 * PATCH /api/orgs/[orgSlug]/posts/[postId] — edit content/meta (MEMBER+),
 * pin/archive (ADMIN+). Recomputes priorityScore in the same transaction.
 */
export async function PATCH(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const ctx = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, PatchBody);

    if (
      (body.pinned !== undefined || body.archived !== undefined) &&
      !roleAtLeast(ctx.role, "ADMIN")
    ) {
      return fail(403, "Only admins can pin or archive posts");
    }

    const post = await db.post.findFirst({
      where: { id: postId, orgId: ctx.org.id },
    });
    if (!post) return fail(404, "Post not found");

    // Validate cross-references stay inside the tenant.
    if (body.categoryId) {
      const cat = await db.category.findFirst({
        where: { id: body.categoryId, orgId: ctx.org.id },
        select: { id: true },
      });
      if (!cat) return fail(400, "Unknown category");
    }
    let validTagIds: string[] | undefined;
    if (body.tagIds) {
      const tags = await db.tag.findMany({
        where: { orgId: ctx.org.id, id: { in: body.tagIds } },
        select: { id: true },
      });
      validTagIds = tags.map((t) => t.id);
    }

    const statusChanged =
      body.status !== undefined && body.status !== post.status;

    const updated = await db.$transaction(async (tx) => {
      await tx.post.update({
        where: { id: post.id },
        data: {
          ...(body.title !== undefined ? { title: body.title } : {}),
          ...(body.body !== undefined ? { body: body.body } : {}),
          ...(body.type !== undefined ? { type: body.type } : {}),
          ...(body.status !== undefined ? { status: body.status } : {}),
          ...(body.categoryId !== undefined
            ? { categoryId: body.categoryId }
            : {}),
          ...(body.impact !== undefined ? { impact: body.impact } : {}),
          ...(body.effort !== undefined ? { effort: body.effort } : {}),
          ...(body.revenueImpact !== undefined
            ? { revenueImpact: body.revenueImpact }
            : {}),
          ...(body.pinned !== undefined ? { pinned: body.pinned } : {}),
          ...(body.archived !== undefined ? { archived: body.archived } : {}),
          ...(statusChanged && body.status === "SHIPPED" && !post.shippedAt
            ? { shippedAt: new Date() }
            : {}),
        },
      });
      if (validTagIds !== undefined) {
        await tx.postTag.deleteMany({ where: { postId: post.id } });
        if (validTagIds.length) {
          await tx.postTag.createMany({
            data: validTagIds.map((tagId) => ({ postId: post.id, tagId })),
            skipDuplicates: true,
          });
        }
      }
      const priorityScore = await recomputePriority(tx, post.id);
      return { priorityScore };
    });

    if (statusChanged) {
      await recordActivity(
        ctx.org.id,
        "post.status_changed",
        { postId: post.id, title: post.title, from: post.status, to: body.status },
        ctx.user.id
      );
      await dispatchWebhooks(ctx.org.id, "post.status_changed", {
        postId: post.id,
        title: post.title,
        from: post.status,
        to: body.status,
      });
    }

    return ok({ id: post.id, priorityScore: updated.priorityScore });
  });
}

/** DELETE /api/orgs/[orgSlug]/posts/[postId] — ADMIN+, cascades relations. */
export async function DELETE(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const ctx = await requireOrg(orgSlug, "ADMIN");
    const post = await db.post.findFirst({
      where: { id: postId, orgId: ctx.org.id },
      select: { id: true, title: true },
    });
    if (!post) return fail(404, "Post not found");
    await db.post.delete({ where: { id: post.id } });
    await recordActivity(
      ctx.org.id,
      "post.deleted",
      { postId: post.id, title: post.title },
      ctx.user.id
    );
    return ok({ deleted: true });
  });
}
