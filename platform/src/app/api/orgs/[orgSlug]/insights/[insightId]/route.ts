import { api, ok, fail } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";

export async function PATCH(
  _req: Request,
  { params }: { params: Promise<{ orgSlug: string; insightId: string }> }
) {
  return api(async () => {
    const { orgSlug, insightId } = await params;
    const { org } = await requireOrg(orgSlug, "MEMBER");
    const updated = await db.insight.updateMany({
      where: { id: insightId, orgId: org.id },
      data: { dismissed: true },
    });
    if (updated.count === 0) return fail(404, "Insight not found");
    return ok({ dismissed: true });
  });
}
