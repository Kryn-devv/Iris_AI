import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { db } from "@/lib/db";
import { hashPassword } from "@/lib/auth/password";
import { createSession } from "@/lib/auth/session";

const Body = z.object({
  name: z.string().trim().min(1, "Name is required").max(100),
  email: z.string().trim().toLowerCase().email("Enter a valid email").max(200),
  password: z.string().min(8, "Password must be at least 8 characters").max(200),
});

/** POST /api/auth/register — create an account and sign in. */
export async function POST(req: Request) {
  return api(async () => {
    const body = await parseBody(req, Body);

    const existing = await db.user.findUnique({
      where: { email: body.email },
      select: { id: true },
    });
    // Generic message: don't confirm whether an email is registered.
    if (existing) {
      return fail(400, "Unable to create an account with that email.");
    }

    const user = await db.user.create({
      data: {
        name: body.name,
        email: body.email,
        passwordHash: await hashPassword(body.password),
      },
      select: { id: true, name: true, email: true },
    });

    await createSession(user.id);
    return ok({ user }, { status: 201 });
  });
}
