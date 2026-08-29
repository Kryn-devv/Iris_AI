import "server-only";
import { createHash } from "crypto";
import type { Organization } from "@prisma/client";
import { db } from "@/lib/db";

/**
 * Resolve the org for a public REST request from its Bearer API key.
 * Returns null when the key is missing/invalid (callers respond 401).
 */
export async function orgFromApiKey(req: Request): Promise<Organization | null> {
  const header = req.headers.get("authorization") ?? "";
  const match = header.match(/^Bearer\s+(nvk_[a-f0-9]{40})$/i);
  if (!match) return null;
  const keyHash = createHash("sha256").update(match[1]!).digest("hex");
  const key = await db.apiKey.findUnique({
    where: { keyHash },
    include: { org: true },
  });
  if (!key) return null;
  // Fire-and-forget usage stamp.
  db.apiKey
    .update({ where: { id: key.id }, data: { lastUsedAt: new Date() } })
    .catch(() => undefined);
  return key.org;
}
