import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { db } from "@/lib/db";
import { ensureGuestId, getCurrentUser } from "@/lib/auth/session";
import { recordActivity } from "@/lib/events";
import {
  getPortalOrg,
  publicPostWhere,
  recomputePortalPriority,
  toPortalComment,
} from "@/components/portal/data";

type Ctx = { params: Promise<{ orgSlug: string; postId: string }> };

const COMMENT_SELECT = {
  id: true,
  body: true,
  isTeam: true,
  guestName: true,
  createdAt: true,
  author: { select: { name: true } },
} as const;

async function findPublicPost(orgId: string, postId: string) {
  return db.post.findFirst({
    where: { ...publicPostWhere(orgId), id: postId },
    select: { id: true, title: true },
  });
}

/** GET /api/p/[orgSlug]/posts/[postId]/comments — public comment thread. */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const org = await getPortalOrg(orgSlug);
    if (!org) return fail(404, "Portal not found");
    const post = await findPublicPost(org.id, postId);
    if (!post) return fail(404, "Post not found");

    const comments = await db.comment.findMany({
      where: { postId: post.id },
      orderBy: { createdAt: "asc" },
      take: 200,
      select: COMMENT_SELECT,
    });
    return ok({ comments: comments.map(toPortalComment) });
  });
}

const CreateBody = z.object({
  body: z.string().trim().min(1, "Write a comment first").max(2000),
  guestName: z.string().trim().max(80).optional(),
});

/**
 * POST /api/p/[orgSlug]/posts/[postId]/comments — add a comment as the
 * signed-in user (team members get the Team badge) or as a guest
 * (name optional — rendered as "Anonymous" when omitted).
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const org = await getPortalOrg(orgSlug);
    if (!org) return fail(404, "Portal not found");
    const post = await findPublicPost(org.id, postId);
    if (!post) return fail(404, "Post not found");

    const body = await parseBody(req, CreateBody);
    const user = await getCurrentUser();
    const guestId = user ? null : await ensureGuestId();

    let isTeam = false;
    if (user) {
      const membership = await db.membership.findFirst({
        where: { userId: user.id, orgId: org.id },
        select: { id: true },
      });
      isTeam = Boolean(membership);
    }

    const comment = await db.$transaction(async (tx) => {
      const created = await tx.comment.create({
        data: {
          postId: post.id,
          body: body.body,
          authorId: user?.id ?? null,
          guestName: user ? null : body.guestName?.trim() || null,
          isTeam,
        },
        select: COMMENT_SELECT,
      });
      const commentCount = await tx.comment.count({
        where: { postId: post.id },
      });
      await tx.post.update({ where: { id: post.id }, data: { commentCount } });
      await recomputePortalPriority(tx, post.id);
      return created;
    });

    await recordActivity(
      org.id,
      "comment.added",
      {
        postId: post.id,
        title: post.title,
        ...(guestId ? { guestId } : {}),
      },
      user?.id
    );

    return ok({ comment: toPortalComment(comment) }, { status: 201 });
  });
}
