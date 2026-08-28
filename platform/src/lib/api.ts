import { NextResponse } from "next/server";
import { ZodError, type ZodType } from "zod";
import { AuthError } from "@/lib/auth/guards";

/** Standard success envelope. */
export function ok<T>(data: T, init?: ResponseInit) {
  return NextResponse.json({ ok: true, data }, init);
}

/** Standard error envelope. */
export function fail(status: number, message: string, details?: unknown) {
  return NextResponse.json(
    { ok: false, error: { message, details } },
    { status }
  );
}

/**
 * Wrap a route handler body: converts AuthError / ZodError / unknown errors
 * into consistent JSON error responses.
 *
 *   export const POST = (req: Request, ctx: Ctx) => api(async () => { ... });
 */
export async function api(
  fn: () => Promise<NextResponse>
): Promise<NextResponse> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof AuthError) return fail(err.status, err.message);
    if (err instanceof ZodError) {
      return fail(400, "Invalid request", err.flatten().fieldErrors);
    }
    console.error("[api]", err);
    return fail(500, "Something went wrong");
  }
}

/** Parse and validate a JSON body against a Zod schema (throws ZodError). */
export async function parseBody<T>(req: Request, schema: ZodType<T>): Promise<T> {
  let json: unknown;
  try {
    json = await req.json();
  } catch {
    json = {};
  }
  return schema.parse(json);
}
