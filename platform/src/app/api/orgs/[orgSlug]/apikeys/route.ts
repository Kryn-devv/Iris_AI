import { createHash, randomBytes } from "crypto";
import { z } from "zod";
import { api, ok, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";

const CreateBody = z.object({ name: z.string().min(1).max(60) });

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ orgSlug: string }> }
) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org } = await requireOrg(orgSlug, "ADMIN");
    const keys = await db.apiKey.findMany({
      where: { orgId: org.id },
      orderBy: { createdAt: "desc" },
      select: { id: true, name: true, prefix: true, lastUsedAt: true, createdAt: true },
    });
    return ok(keys);
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
    const raw = `nvk_${randomBytes(20).toString("hex")}`;
    const created = await db.apiKey.create({
      data: {
        orgId: org.id,
        name: body.name,
        prefix: raw.slice(0, 8),
        keyHash: createHash("sha256").update(raw).digest("hex"),
      },
    });
    await recordActivity(org.id, "apikey.created", { name: body.name }, user.id);
    // The full key is returned exactly once.
    return ok({ id: created.id, name: created.name, prefix: created.prefix, key: raw });
  });
}
