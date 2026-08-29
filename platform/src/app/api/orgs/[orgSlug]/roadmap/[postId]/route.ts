import { z } from "zod";
import type { PostStatus, Prisma } from "@prisma/client";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity, dispatchWebhooks } from "@/lib/events";
import { ROADMAP_STATUSES } from "@/lib/status";

type Ctx = { params: Promise<{ orgSlug: string; postId: string }> };

const Body = z
  .object({
    status: z.enum(ROADMAP_STATUSES as [PostStatus, ...PostStatus[]]).optional(),
    order: z.number().finite().min(-1e9).max(1e9).optional(),
    showOnRoadmap: z.boolean().optional(),
  })
  .refine(
    (b) =>
      b.status !== undefined ||
      b.order !== undefined ||
      b.showOnRoadmap !== undefined,
    { message: "Nothing to update" }
  );

/**
 * PATCH /api/orgs/[orgSlug]/roadmap/[postId] — move a post on the roadmap:
 * change column (status, with shippedAt maintenance + events), reposition
 * (fractional roadmapOrder), or add/remove (showOnRoadmap). MEMBER+.
 */
export async function PATCH(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const ctx = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, Body);

    const post = await db.post.findFirst({
      where: { id: postId, orgId: ctx.org.id, archived: false, mergedIntoId: null },
    });
    if (!post) return fail(404, "Post not found");

    const data: Prisma.PostUpdateInput = {};

    // Adding to the roadmap: land in the requested column (or a sensible
    // default) at the end unless an explicit order was provided.
    const adding = body.showOnRoadmap === true && !post.showOnRoadmap;
    const removing = body.showOnRoadmap === false;
    if (body.showOnRoadmap !== undefined) data.showOnRoadmap = body.showOnRoadmap;

    let targetStatus = body.status;
    if (adding && !targetStatus) {
      targetStatus = ROADMAP_STATUSES.includes(post.status)
        ? post.status
        : "UNDER_CONSIDERATION";
    }

    const statusChanged =
      targetStatus !== undefined && targetStatus !== post.status;
    if (statusChanged) {
      data.status = targetStatus;
      // Stamp shippedAt on entering SHIPPED; clear it when leaving.
      data.shippedAt = targetStatus === "SHIPPED" ? new Date() : null;
    }

    if (body.order !== undefined) {
      data.roadmapOrder = body.order;
    } else if (adding) {
      const columnStatus = targetStatus ?? post.status;
      const last = await db.post.aggregate({
        where: { orgId: ctx.org.id, showOnRoadmap: true, status: columnStatus },
        _max: { roadmapOrder: true },
      });
      data.roadmapOrder = (last._max.roadmapOrder ?? 0) + 1;
    }

    const updated = await db.post.update({
      where: { id: post.id },
      data,
      select: {
        id: true,
        status: true,
        roadmapOrder: true,
        showOnRoadmap: true,
        shippedAt: true,
      },
    });

    if (statusChanged) {
      await recordActivity(
        ctx.org.id,
        "post.status_changed",
        { postId: post.id, title: post.title, from: post.status, to: targetStatus },
        ctx.user.id
      );
      await dispatchWebhooks(ctx.org.id, "post.status_changed", {
        postId: post.id,
        title: post.title,
        from: post.status,
        to: targetStatus,
      });
    }
    if (adding) {
      await recordActivity(
        ctx.org.id,
        "roadmap.post_added",
        { postId: post.id, title: post.title, status: updated.status },
        ctx.user.id
      );
    } else if (removing && post.showOnRoadmap) {
      await recordActivity(
        ctx.org.id,
        "roadmap.post_removed",
        { postId: post.id, title: post.title },
        ctx.user.id
      );
    }

    return ok(updated);
  });
}
