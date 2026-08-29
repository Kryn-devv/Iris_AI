import type { Prisma } from "@prisma/client";
import { PostType } from "@prisma/client";
import { ROADMAP_STATUSES } from "@/lib/status";

export type RoadmapFilters = {
  categoryId?: string;
  boardId?: string;
  type?: PostType;
};

/** Shared where-clause for roadmap posts, tenant-scoped + optional filters. */
export function roadmapWhere(
  orgId: string,
  filters: RoadmapFilters
): Prisma.PostWhereInput {
  return {
    orgId,
    showOnRoadmap: true,
    archived: false,
    mergedIntoId: null,
    status: { in: ROADMAP_STATUSES },
    ...(filters.categoryId ? { categoryId: filters.categoryId } : {}),
    ...(filters.boardId ? { boardId: filters.boardId } : {}),
    ...(filters.type ? { type: filters.type } : {}),
  };
}

/** Coerce a raw query/search param into a PostType, or undefined. */
export function parsePostType(value: string | null | undefined): PostType | undefined {
  return value && (Object.values(PostType) as string[]).includes(value)
    ? (value as PostType)
    : undefined;
}

export const ROADMAP_CARD_SELECT = {
  id: true,
  title: true,
  status: true,
  type: true,
  voteCount: true,
  commentCount: true,
  priorityScore: true,
  roadmapOrder: true,
  shippedAt: true,
  category: { select: { id: true, name: true, color: true } },
  board: { select: { id: true, name: true } },
} satisfies Prisma.PostSelect;
