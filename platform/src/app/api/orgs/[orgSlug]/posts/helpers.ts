import "server-only";
import { promises as fs } from "fs";
import path from "path";
import { randomBytes } from "crypto";
import type { Prisma, PrismaClient } from "@prisma/client";
import { priorityScore } from "@/lib/priority";

/** Page size shared by the feedback list page and the posts list API. */
export const POSTS_PAGE_SIZE = 20;

export const POST_TYPES = ["FEEDBACK", "FEATURE_REQUEST"] as const;
export const POST_STATUSES = [
  "OPEN",
  "UNDER_CONSIDERATION",
  "PLANNED",
  "IN_PROGRESS",
  "SHIPPED",
  "CLOSED",
] as const;
export const POST_SOURCES = [
  "DASHBOARD",
  "PORTAL",
  "WIDGET",
  "IMPORT",
  "API",
  "EMAIL",
] as const;
export const SENTIMENTS = ["POSITIVE", "NEUTRAL", "NEGATIVE"] as const;
export const POST_SORTS = ["recent", "votes", "priority", "comments"] as const;

export type PostListFilters = {
  status?: string;
  type?: string;
  category?: string;
  tag?: string;
  sentiment?: string;
  source?: string;
  q?: string;
  sort?: string;
  archived?: boolean;
  /** Exclude a specific post id (used by merge target search). */
  exclude?: string;
};

function oneOf<T extends readonly string[]>(
  list: T,
  value: string | undefined
): T[number] | undefined {
  return value && (list as readonly string[]).includes(value)
    ? (value as T[number])
    : undefined;
}

/** Parse raw searchParams into a safe filter object. */
export function parsePostFilters(
  sp: Record<string, string | string[] | undefined>
): PostListFilters {
  const get = (k: string) => {
    const v = sp[k];
    return typeof v === "string" ? v : undefined;
  };
  return {
    status: oneOf(POST_STATUSES, get("status")),
    type: oneOf(POST_TYPES, get("type")),
    category: get("category"),
    tag: get("tag"),
    sentiment: oneOf(SENTIMENTS, get("sentiment")),
    source: oneOf(POST_SOURCES, get("source")),
    q: get("q")?.slice(0, 200),
    sort: oneOf(POST_SORTS, get("sort")) ?? "recent",
    archived: get("archived") === "1",
  };
}

/**
 * Tenant-scoped where clause for post lists. Merged posts are always
 * excluded; archived posts only appear when explicitly requested.
 */
export function buildPostWhere(
  orgId: string,
  f: PostListFilters
): Prisma.PostWhereInput {
  const where: Prisma.PostWhereInput = {
    orgId,
    mergedIntoId: null,
    archived: f.archived ? true : false,
  };
  if (f.status) where.status = f.status as Prisma.PostWhereInput["status"];
  if (f.type) where.type = f.type as Prisma.PostWhereInput["type"];
  if (f.category) where.categoryId = f.category;
  if (f.tag) where.tags = { some: { tagId: f.tag } };
  if (f.sentiment)
    where.sentiment = f.sentiment as Prisma.PostWhereInput["sentiment"];
  if (f.source) where.source = f.source as Prisma.PostWhereInput["source"];
  if (f.exclude) where.id = { not: f.exclude };
  if (f.q) {
    where.OR = [
      { title: { contains: f.q, mode: "insensitive" } },
      { body: { contains: f.q, mode: "insensitive" } },
    ];
  }
  return where;
}

/** Order-by for the supported sort keys (pinned posts float first). */
export function buildPostOrderBy(
  sort: string | undefined
): Prisma.PostOrderByWithRelationInput[] {
  const primary: Prisma.PostOrderByWithRelationInput =
    sort === "votes"
      ? { voteCount: "desc" }
      : sort === "priority"
        ? { priorityScore: "desc" }
        : sort === "comments"
          ? { commentCount: "desc" }
          : { createdAt: "desc" };
  return [{ pinned: "desc" }, primary, { id: "desc" }];
}

type DbLike = Prisma.TransactionClient | PrismaClient;

/**
 * Recompute and persist a post's priorityScore from its current signals.
 * Call inside the same transaction that mutated votes/comments/impact/effort.
 */
export async function recomputePriority(
  tx: DbLike,
  postId: string
): Promise<number> {
  const post = await tx.post.findUnique({ where: { id: postId } });
  if (!post) return 0;
  const score = priorityScore({
    voteCount: post.voteCount,
    commentCount: post.commentCount,
    sentimentScore: post.sentimentScore,
    impact: post.impact,
    effort: post.effort,
    revenueImpact: post.revenueImpact,
    createdAt: post.createdAt,
  });
  await tx.post.update({
    where: { id: postId },
    data: { priorityScore: score },
  });
  return score;
}

// ---------------------------------------------------------------------------
// Attachment uploads (base64 data URLs -> public/uploads/*)
// ---------------------------------------------------------------------------

export const MAX_ATTACHMENT_BYTES = 500 * 1024;
export const MAX_ATTACHMENTS = 5;

const EXT_BY_MIME: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
};

export type IncomingAttachment = { filename: string; dataUrl: string };
export type SavedAttachment = {
  url: string;
  filename: string;
  mimeType: string;
  size: number;
};

/**
 * Decode image data URLs and persist them under public/uploads/.
 * Silently skips anything that is not a supported image or exceeds 500KB.
 */
export async function saveAttachmentFiles(
  files: IncomingAttachment[]
): Promise<SavedAttachment[]> {
  const saved: SavedAttachment[] = [];
  if (files.length === 0) return saved;
  const dir = path.join(process.cwd(), "public", "uploads");
  await fs.mkdir(dir, { recursive: true });
  for (const file of files.slice(0, MAX_ATTACHMENTS)) {
    const match =
      /^data:(image\/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)$/.exec(
        file.dataUrl
      );
    if (!match) continue;
    const mime = match[1]!;
    let buf: Buffer;
    try {
      buf = Buffer.from(match[2]!.replace(/\s+/g, ""), "base64");
    } catch {
      continue;
    }
    if (buf.length === 0 || buf.length > MAX_ATTACHMENT_BYTES) continue;
    const name = `${Date.now().toString(36)}${randomBytes(8).toString("hex")}.${EXT_BY_MIME[mime]}`;
    await fs.writeFile(path.join(dir, name), buf);
    saved.push({
      url: `/uploads/${name}`,
      filename: (file.filename || name).slice(0, 160),
      mimeType: mime,
      size: buf.length,
    });
  }
  return saved;
}
