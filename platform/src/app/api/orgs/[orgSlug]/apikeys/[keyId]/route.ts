import { api, ok, fail } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ orgSlug: string; keyId: string }> }
) {
  return api(async () => {
    const { orgSlug, keyId } = await params;
    const { org, user } = await requireOrg(orgSlug, "ADMIN");
    const key = await db.apiKey.findFirst({ where: { id: keyId, orgId: org.id } });
    if (!key) return fail(404, "API key not found");
    await db.apiKey.delete({ where: { id: key.id } });
    await recordActivity(org.id, "apikey.revoked", { name: key.name }, user.id);
    return ok({ revoked: true });
  });
}
