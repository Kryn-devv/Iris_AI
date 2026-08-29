import { api, ok, fail } from "@/lib/api";
import { db } from "@/lib/db";
import { getCurrentUser, getGuestId } from "@/lib/auth/session";
import { findSimilar } from "@/lib/similarity";
import {
  getPortalOrg,
  publicPostWhere,
  toPortalComment,
  toPortalPost,
  votedPostIds,
  PORTAL_POST_SELECT,
} from "@/components/portal/data";

type Ctx = { params: Promise<{ orgSlug: string; postId: string }> };

/**
 * GET /api/p/[orgSlug]/posts/[postId] — public post detail: post, comments,
 * attachments, and similar public posts. 404s for private/archived/merged.
 */
export async function GET(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, postId } = await params;
    const org = await getPortalOrg(orgSlug);
    if (!org) return fail(404, "Portal not found");

    const post = await db.post.findFirst({
      where: { ...publicPostWhere(org.id), id: postId },
      select: {
        ...PORTAL_POST_SELECT,
        attachments: {
          select: { id: true, url: true, filename: true, mimeType: true },
        },
        comments: {
          orderBy: { createdAt: "asc" },
          select: {
            id: true,
            body: true,
            isTeam: true,
            guestName: true,
            createdAt: true,
            author: { select: { name: true } },
          },
        },
      },
    });
    if (!post) return fail(404, "Post not found");

    const user = await getCurrentUser();
    const guestId = user ? null : await getGuestId();

    const candidates = await db.post.findMany({
      where: { ...publicPostWhere(org.id), id: { not: post.id } },
      orderBy: { createdAt: "desc" },
      take: 300,
      select: { id: true, title: true, body: true },
    });
    const similar = findSimilar(
      { title: post.title, body: post.body },
      candidates,
      { threshold: 0.2, limit: 5 }
    );

    const voted = await votedPostIds(
      [post.id, ...similar.map((s) => s.id)],
      user?.id ?? null,
      guestId
    );

    return ok({
      post: { ...toPortalPost(post, voted.has(post.id)), body: post.body },
      attachments: post.attachments,
      comments: post.comments.map(toPortalComment),
      similar,
    });
  });
}
