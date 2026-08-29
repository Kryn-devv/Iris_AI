import type { PostStatus, PostType } from "@prisma/client";

/** Serialized post shape shared between portal server pages and client islands. */
export type PortalPost = {
  id: string;
  title: string;
  snippet: string;
  status: PostStatus;
  type: PostType;
  voteCount: number;
  commentCount: number;
  pinned: boolean;
  createdAt: string; // ISO
  category: { name: string; color: string } | null;
  voted: boolean;
};

/** Serialized comment used on portal post details + changelog entries. */
export type PortalComment = {
  id: string;
  name: string;
  isTeam: boolean;
  body: string;
  createdAt: string; // ISO
};

/** A similar-post suggestion returned by GET /api/p/[orgSlug]/posts?similarTo=… */
export type SimilarPortalPost = {
  id: string;
  title: string;
  status: PostStatus;
  voteCount: number;
  voted: boolean;
  score: number;
};

/** Emoji reactions offered on changelog entries — single source of truth. */
export const REACTION_EMOJIS = ["🎉", "❤️", "👍", "🚀"] as const;
export type ReactionEmoji = (typeof REACTION_EMOJIS)[number];

export const PORTAL_SORTS = ["trending", "top", "new"] as const;
export type PortalSort = (typeof PORTAL_SORTS)[number];
