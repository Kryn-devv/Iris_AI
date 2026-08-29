import { z } from "zod";
import { api, ok, parseBody } from "@/lib/api";
import { requireUser } from "@/lib/auth/guards";
import { db } from "@/lib/db";

const Body = z.object({
  name: z.string().trim().min(1, "Name is required").max(100).optional(),
  avatarUrl: z
    .union([z.string().trim().url("Enter a valid URL").max(500), z.literal("")])
    .optional(),
});

/** GET /api/auth/profile — the signed-in user's profile. */
export async function GET() {
  return api(async () => {
    const user = await requireUser();
    return ok({ user });
  });
}

/** PATCH /api/auth/profile — update name / avatar. */
export async function PATCH(req: Request) {
  return api(async () => {
    const sessionUser = await requireUser();
    const body = await parseBody(req, Body);

    const user = await db.user.update({
      where: { id: sessionUser.id },
      data: {
        ...(body.name !== undefined ? { name: body.name } : {}),
        ...(body.avatarUrl !== undefined
          ? { avatarUrl: body.avatarUrl === "" ? null : body.avatarUrl }
          : {}),
      },
      select: { id: true, name: true, email: true, avatarUrl: true },
    });
    return ok({ user });
  });
}
