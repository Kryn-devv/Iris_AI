import { api, ok } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { recordActivity } from "@/lib/events";
import { rebuildClusters, generateInsights } from "@/lib/ai/insights";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ orgSlug: string }> }
) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org, user } = await requireOrg(orgSlug, "MEMBER");
    const clusters = await rebuildClusters(org.id);
    const insights = await generateInsights(org.id);
    await recordActivity(org.id, "insights.generated", { clusters, insights }, user.id);
    return ok({ clusters, insights });
  });
}
