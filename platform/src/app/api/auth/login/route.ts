import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { db } from "@/lib/db";
import { verifyPassword } from "@/lib/auth/password";
import { createSession } from "@/lib/auth/session";

const Body = z.object({
  email: z.string().trim().toLowerCase().email(),
  password: z.string().min(1),
});

const INVALID = "Invalid email or password";

/** POST /api/auth/login — verify credentials and start a session. */
export async function POST(req: Request) {
  return api(async () => {
    let body: z.infer<typeof Body>;
    try {
      body = await parseBody(req, Body);
    } catch {
      // Constant-shape error: malformed credentials look the same as wrong ones.
      return fail(400, INVALID);
    }

    const user = await db.user.findUnique({
      where: { email: body.email },
      select: { id: true, passwordHash: true },
    });
    if (!user?.passwordHash) return fail(400, INVALID);

    const valid = await verifyPassword(body.password, user.passwordHash);
    if (!valid) return fail(400, INVALID);

    await createSession(user.id);
    return ok({ userId: user.id });
  });
}
