import { NextResponse, type NextRequest } from "next/server";
import { brand } from "@/config/brand";

/**
 * Fast-path guard: /app requires a session cookie. The real session check
 * (DB-backed) happens in server layouts — this only prevents obviously
 * unauthenticated navigation.
 */
export function middleware(request: NextRequest) {
  const hasSession = request.cookies.has(brand.cookieName);
  if (!hasSession && request.nextUrl.pathname.startsWith("/app")) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/app/:path*", "/onboarding"],
};
