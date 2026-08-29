import type { PostStatus, PostType } from "@prisma/client";

/** Serializable card payload passed from server pages to the kanban board. */
export type RoadmapCard = {
  id: string;
  title: string;
  status: PostStatus;
  type: PostType;
  voteCount: number;
  commentCount: number;
  priorityScore: number;
  roadmapOrder: number;
  category: { id: string; name: string; color: string } | null;
  board: { id: string; name: string } | null;
};

export type RoadmapColumns = Record<string, RoadmapCard[]>;

export type FilterOption = { id: string; name: string; color?: string };
