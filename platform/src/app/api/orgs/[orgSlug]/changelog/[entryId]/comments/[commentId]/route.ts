import { api, ok, fail } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

type Ctx = {
  params: Promise<{ orgSlug: string; entryId: string; commentId: string }>;
};

/**
 * DELETE /api/orgs/[orgSlug]/changelog/[entryId]/comments/[commentId] —
 * moderation: remove a comment from a changelog entry. ADMIN+.
 */
export async function DELETE(_req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug, entryId, commentId } = await params;
    const ctx = await requireOrg(orgSlug, "ADMIN");

    // Scope the comment through its entry to the tenant.
    const comment = await db.changelogComment.findFirst({
      where: {
        id: commentId,
        entryId,
        entry: { orgId: ctx.org.id },
      },
      select: { id: true, entryId: true },
    });
    if (!comment) return fail(404, "Comment not found");

    await db.changelogComment.delete({ where: { id: comment.id } });
    await recordActivity(
      ctx.org.id,
      "changelog.comment_deleted",
      { entryId: comment.entryId, commentId: comment.id },
      ctx.user.id
    );
    return ok({ deleted: true });
  });
}
