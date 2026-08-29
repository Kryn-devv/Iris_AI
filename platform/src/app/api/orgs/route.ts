import { z } from "zod";
import { api, ok, parseBody } from "@/lib/api";
import { requireUser } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import { recordActivity } from "@/lib/events";
import { slugify } from "@/lib/utils";

const Body = z.object({
  name: z.string().trim().min(1, "Workspace name is required").max(60),
});

const STARTER_CATEGORIES: { name: string; color: string }[] = [
  { name: "UX & Design", color: "#a78bfa" },
  { name: "API & Developers", color: "#5eead4" },
  { name: "Performance", color: "#f59e0b" },
  { name: "Collaboration", color: "#60a5fa" },
  { name: "Integrations", color: "#f472b6" },
];

/** Find a unique org slug, appending -2, -3, … when taken. */
async function uniqueSlug(name: string): Promise<string> {
  const base = slugify(name) || "workspace";
  let candidate = base;
  let suffix = 2;
  // Bounded loop; slug collisions are rare.
  while (await db.organization.findUnique({ where: { slug: candidate }, select: { id: true } })) {
    candidate = `${base}-${suffix++}`;
  }
  return candidate;
}

/**
 * POST /api/orgs — create a workspace: org + OWNER membership + default
 * public "General" board + starter categories.
 */
export async function POST(req: Request) {
  return api(async () => {
    const user = await requireUser();
    const body = await parseBody(req, Body);
    const slug = await uniqueSlug(body.name);

    const org = await db.organization.create({
      data: {
        name: body.name,
        slug,
        memberships: { create: { userId: user.id, role: "OWNER" } },
        boards: {
          create: {
            name: "General",
            slug: "general",
            description: "General product feedback",
            isPublic: true,
          },
        },
        categories: { create: STARTER_CATEGORIES },
      },
      select: { id: true, name: true, slug: true },
    });

    await recordActivity(org.id, "org.created", { name: org.name }, user.id);
    return ok({ org }, { status: 201 });
  });
}
