import { randomBytes } from "crypto";
import { z } from "zod";
import { api, ok, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import { WEBHOOK_EVENTS } from "./events";

const CreateBody = z.object({
  url: z.string().url().startsWith("https://", "Webhook URLs must be https"),
  events: z.array(z.enum(WEBHOOK_EVENTS)).min(1),
});

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ orgSlug: string }> }
) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug, "ADMIN");
    const hooks = await db.webhook.findMany({
      where: { orgId: org.id },
      orderBy: { createdAt: "desc" },
      select: { id: true, url: true, events: true, active: true, createdAt: true },
    });
    return ok(hooks);
  });
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ orgSlug: string }> }
) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org, user } = await requireOrg(orgSlug, "ADMIN");
    const body = await parseBody(req, CreateBody);
    const secret = `whsec_${randomBytes(24).toString("hex")}`;
    const hook = await db.webhook.create({
      data: { orgId: org.id, url: body.url, events: body.events, secret },
    });
    await recordActivity(org.id, "webhook.created", { url: body.url }, user.id);
    // The secret is returned exactly once, at creation time.
    return ok({ id: hook.id, url: hook.url, events: hook.events, secret });
  });
}
