import { z } from "zod";
import { ChangelogLabel } from "@prisma/client";
import { db } from "@/lib/db";
import { slugify } from "@/lib/utils";

/** All changelog labels, in display order. */
export const CHANGELOG_LABELS = Object.values(ChangelogLabel);

/** A video URL must be https, e.g. YouTube / Vimeo / any https host. */
export function isValidVideoUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:";
  } catch {
    return false;
  }
}

/** Cover images are either local uploads or absolute https URLs. */
export function isValidCoverUrl(url: string): boolean {
  if (url.startsWith("/uploads/")) return /^\/uploads\/[\w.-]+$/.test(url);
  try {
    return new URL(url).protocol === "https:";
  } catch {
    return false;
  }
}

export const EntryBody = z.object({
  title: z.string().trim().min(1).max(200),
  slug: z.string().trim().max(80).optional(),
  version: z.string().trim().max(50).nullable().optional(),
  body: z.string().min(1).max(50_000),
  labels: z.array(z.nativeEnum(ChangelogLabel)).max(5).optional(),
  coverImageUrl: z.string().trim().max(500).nullable().optional(),
  videoUrl: z.string().trim().max(500).nullable().optional(),
});

export type EntryInput = z.infer<typeof EntryBody>;

/**
 * Resolve a slug that is unique inside the org: slugify the preferred text
 * and append -2, -3, … on collision (excluding `excludeId` when editing).
 */
export async function uniqueEntrySlug(
  orgId: string,
  preferred: string,
  excludeId?: string
): Promise<string> {
  const root = slugify(preferred) || "entry";
  let candidate = root;
  let n = 2;
  // Bounded loop: orgs will not realistically have thousands of collisions,
  // but keep a hard stop so a pathological state can never spin forever.
  while (n < 1000) {
    const existing = await db.changelogEntry.findFirst({
      where: {
        orgId,
        slug: candidate,
        ...(excludeId ? { id: { not: excludeId } } : {}),
      },
      select: { id: true },
    });
    if (!existing) return candidate;
    candidate = `${root}-${n}`;
    n += 1;
  }
  return `${root}-${Date.now()}`;
}
