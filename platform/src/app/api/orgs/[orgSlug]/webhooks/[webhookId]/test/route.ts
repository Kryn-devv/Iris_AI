import { createHmac } from "crypto";
import { api, ok, fail } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";

/** Fire a sample signed payload at one webhook and report the result. */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ orgSlug: string; webhookId: string }> }
) {
  return api(async () => {
    const { orgSlug, webhookId } = await params;
    const { org } = await requireOrg(orgSlug, "ADMIN");
    const hook = await db.webhook.findFirst({ where: { id: webhookId, orgId: org.id } });
    if (!hook) return fail(404, "Webhook not found");

    const body = JSON.stringify({
      event: "test",
      payload: { message: "Test delivery", org: org.slug },
      sentAt: new Date().toISOString(),
    });
    try {
      const res = await fetch(hook.url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Event": "test",
          "X-Signature": createHmac("sha256", hook.secret).update(body).digest("hex"),
        },
        body,
        signal: AbortSignal.timeout(5000),
      });
      return ok({ delivered: res.ok, status: res.status });
    } catch (err) {
      return ok({ delivered: false, status: 0, error: String(err).slice(0, 200) });
    }
  });
}
