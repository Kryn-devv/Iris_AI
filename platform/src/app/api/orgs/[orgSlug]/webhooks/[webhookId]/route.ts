import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

const PatchBody = z.object({ active: z.boolean() });

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ orgSlug: string; webhookId: string }> }
) {
  return api(async () => {
    const { orgSlug, webhookId } = await params;
    const { org } = await requireOrg(orgSlug, "ADMIN");
    const body = await parseBody(req, PatchBody);
    const updated = await db.webhook.updateMany({
      where: { id: webhookId, orgId: org.id },
      data: { active: body.active },
    });
    if (updated.count === 0) return fail(404, "Webhook not found");
    return ok({ active: body.active });
  });
}

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ orgSlug: string; webhookId: string }> }
) {
  return api(async () => {
    const { orgSlug, webhookId } = await params;
    const { org, user } = await requireOrg(orgSlug, "ADMIN");
    const hook = await db.webhook.findFirst({ where: { id: webhookId, orgId: org.id } });
    if (!hook) return fail(404, "Webhook not found");
    await db.webhook.delete({ where: { id: hook.id } });
    await recordActivity(org.id, "webhook.deleted", { url: hook.url }, user.id);
    return ok({ deleted: true });
  });
}
