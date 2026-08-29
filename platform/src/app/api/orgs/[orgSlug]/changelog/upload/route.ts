import { promises as fs } from "fs";
import path from "path";
import { randomUUID } from "crypto";
import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";

const MAX_BYTES = 800 * 1024; // 800KB
// 800KB of base64 ≈ 1.1M chars; leave headroom for the data: prefix.
const Body = z.object({
  dataUrl: z.string().min(32).max(1_200_000),
});

const DATA_URL_RE =
  /^data:image\/(png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/]+={0,2})$/;

const EXT: Record<string, string> = {
  png: "png",
  jpeg: "jpg",
  jpg: "jpg",
  webp: "webp",
  gif: "gif",
};

/**
 * POST /api/orgs/[orgSlug]/changelog/upload — store a base64 cover image
 * (≤ 800KB) under public/uploads and return its public URL. MEMBER+.
 */
export async function POST(req: Request, { params }: { params: Promise<{ orgSlug: string }> }) {
  return api(async () => {
    const { orgSlug } = await params;
    await requireOrg(orgSlug, "MEMBER");
    const { dataUrl } = await parseBody(req, Body);

    const match = dataUrl.match(DATA_URL_RE);
    if (!match) {
      return fail(400, "Expected a base64 data URL of a png, jpeg, webp or gif image");
    }
    const buffer = Buffer.from(match[2]!, "base64");
    if (buffer.length === 0) return fail(400, "Empty image");
    if (buffer.length > MAX_BYTES) {
      return fail(400, "Image is too large — the limit is 800KB");
    }

    const dir = path.join(process.cwd(), "public", "uploads");
    await fs.mkdir(dir, { recursive: true });
    const filename = `${randomUUID()}.${EXT[match[1]!]}`;
    await fs.writeFile(path.join(dir, filename), buffer);

    return ok({ url: `/uploads/${filename}` }, { status: 201 });
  });
}
