import { z } from "zod";
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireUser } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { hashPassword, verifyPassword } from "@/lib/auth/password";

const Body = z.object({
  currentPassword: z.string().min(1, "Current password is required"),
  newPassword: z
    .string()
    .min(8, "New password must be at least 8 characters")
    .max(200),
});

/** POST /api/auth/password — change password (requires current password). */
export async function POST(req: Request) {
  return api(async () => {
    const sessionUser = await requireUser();
    const body = await parseBody(req, Body);

    const user = await db.user.findUnique({
      where: { id: sessionUser.id },
      select: { id: true, passwordHash: true },
    });
    if (!user?.passwordHash) {
      return fail(400, "This account does not use password sign-in.");
    }

    const valid = await verifyPassword(body.currentPassword, user.passwordHash);
    if (!valid) return fail(400, "Current password is incorrect.");

    await db.user.update({
      where: { id: user.id },
      data: { passwordHash: await hashPassword(body.newPassword) },
    });
    return ok({ changed: true });
  });
}
