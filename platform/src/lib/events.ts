import "server-only";
import { createHmac } from "crypto";
import { db } from "@/lib/db";

/** Append to the org activity feed. Never throws. */
export async function recordActivity(
  orgId: string,
  type: string,
  meta: Record<string, unknown> = {},
  actorId?: string | null
): Promise<void> {
  try {
    await db.activity.create({
      data: { orgId, type, meta: meta as object, actorId: actorId ?? null },
    });
  } catch (err) {
    console.error("[activity]", err);
  }
}

/**
 * Deliver an event to the org's active webhooks (fire-and-forget).
 * Payloads are signed with HMAC-SHA256 in the X-Signature header.
 */
export async function dispatchWebhooks(
  orgId: string,
  event: string,
  payload: Record<string, unknown>
): Promise<void> {
  try {
    const hooks = await db.webhook.findMany({
      where: { orgId, active: true, events: { has: event } },
    });
    if (hooks.length === 0) return;
    const body = JSON.stringify({ event, payload, sentAt: new Date().toISOString() });
    await Promise.allSettled(
      hooks.map((hook) =>
        fetch(hook.url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Event": event,
            "X-Signature": createHmac("sha256", hook.secret).update(body).digest("hex"),
          },
          body,
          signal: AbortSignal.timeout(5000),
        })
      )
    );
  } catch (err) {
    console.error("[webhooks]", err);
  }
}
