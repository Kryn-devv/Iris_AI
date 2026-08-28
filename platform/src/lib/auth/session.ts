import "server-only";
import { cookies } from "next/headers";
import { createHash, randomBytes } from "crypto";
import { cache } from "react";
import { db } from "@/lib/db";
import { brand } from "@/config/brand";

const SESSION_TTL_DAYS = 30;

function hashToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

/** Create a DB-backed session and set the httpOnly cookie. */
export async function createSession(userId: string): Promise<void> {
  const token = randomBytes(32).toString("hex");
  const expiresAt = new Date(Date.now() + SESSION_TTL_DAYS * 86400_000);
  await db.session.create({
    data: { tokenHash: hashToken(token), userId, expiresAt },
  });
  const jar = await cookies();
  jar.set(brand.cookieName, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    expires: expiresAt,
  });
}

/** Destroy the current session (DB row + cookie). */
export async function destroySession(): Promise<void> {
  const jar = await cookies();
  const token = jar.get(brand.cookieName)?.value;
  if (token) {
    await db.session
      .delete({ where: { tokenHash: hashToken(token) } })
      .catch(() => undefined);
  }
  jar.delete(brand.cookieName);
}

export type SessionUser = {
  id: string;
  email: string;
  name: string;
  avatarUrl: string | null;
};

/**
 * Resolve the current user from the session cookie, or null.
 * Wrapped in react `cache` so layouts + pages share one lookup per request.
 */
export const getCurrentUser = cache(async (): Promise<SessionUser | null> => {
  const jar = await cookies();
  const token = jar.get(brand.cookieName)?.value;
  if (!token) return null;
  const session = await db.session.findUnique({
    where: { tokenHash: hashToken(token) },
    include: {
      user: { select: { id: true, email: true, name: true, avatarUrl: true } },
    },
  });
  if (!session) return null;
  if (session.expiresAt < new Date()) {
    await db.session.delete({ where: { id: session.id } }).catch(() => undefined);
    return null;
  }
  return session.user;
});

/**
 * Stable anonymous id for guest voting/survey responses, stored in a cookie.
 * Returns the existing id or null — setting the cookie must happen in a
 * route handler / server action (see `ensureGuestId`).
 */
export async function getGuestId(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(brand.guestCookieName)?.value ?? null;
}

/** Read-or-create the guest id. Call only from route handlers/actions. */
export async function ensureGuestId(): Promise<string> {
  const jar = await cookies();
  const existing = jar.get(brand.guestCookieName)?.value;
  if (existing) return existing;
  const id = randomBytes(16).toString("hex");
  jar.set(brand.guestCookieName, id, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 365,
  });
  return id;
}
