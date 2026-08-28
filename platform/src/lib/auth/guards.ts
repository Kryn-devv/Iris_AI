import "server-only";
import { redirect } from "next/navigation";
import { cache } from "react";
import type { Organization, Role } from "@prisma/client";
import { db } from "@/lib/db";
import { getCurrentUser, type SessionUser } from "@/lib/auth/session";

export class AuthError extends Error {
  constructor(
    public status: 401 | 403 | 404,
    message: string
  ) {
    super(message);
  }
}

const ROLE_RANK: Record<Role, number> = {
  VIEWER: 0,
  MEMBER: 1,
  ADMIN: 2,
  OWNER: 3,
};

export function roleAtLeast(role: Role, min: Role): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[min];
}

export type OrgContext = {
  user: SessionUser;
  org: Organization;
  role: Role;
};

/** Page guard: redirect to /login when unauthenticated. */
export async function requireUserPage(): Promise<SessionUser> {
  const user = await getCurrentUser();
  if (!user) redirect("/login");
  return user;
}

/** API guard: throw AuthError(401) when unauthenticated. */
export async function requireUser(): Promise<SessionUser> {
  const user = await getCurrentUser();
  if (!user) throw new AuthError(401, "Authentication required");
  return user;
}

const resolveOrgContext = cache(
  async (orgSlug: string): Promise<OrgContext | null> => {
    const user = await getCurrentUser();
    if (!user) return null;
    const membership = await db.membership.findFirst({
      where: { userId: user.id, org: { slug: orgSlug } },
      include: { org: true },
    });
    if (!membership) return null;
    return { user, org: membership.org, role: membership.role };
  }
);

/**
 * API guard: current user must be a member of `orgSlug` with at least `min`.
 * Throws AuthError — route handlers convert it via `handleApiError`.
 */
export async function requireOrg(
  orgSlug: string,
  min: Role = "VIEWER"
): Promise<OrgContext> {
  const user = await getCurrentUser();
  if (!user) throw new AuthError(401, "Authentication required");
  const ctx = await resolveOrgContext(orgSlug);
  if (!ctx) throw new AuthError(404, "Workspace not found");
  if (!roleAtLeast(ctx.role, min)) {
    throw new AuthError(403, "You do not have permission to do that");
  }
  return ctx;
}

/** Page guard variant: redirects instead of throwing. */
export async function requireOrgPage(
  orgSlug: string,
  min: Role = "VIEWER"
): Promise<OrgContext> {
  const user = await getCurrentUser();
  if (!user) redirect("/login");
  const ctx = await resolveOrgContext(orgSlug);
  if (!ctx) redirect("/app");
  if (!roleAtLeast(ctx.role, min)) redirect(`/app/${orgSlug}/dashboard`);
  return ctx;
}

/** Look up a public (portal-enabled) org by slug, or null. */
export async function getPublicOrg(orgSlug: string) {
  return db.organization.findFirst({
    where: { slug: orgSlug, portalEnabled: true },
  });
}
