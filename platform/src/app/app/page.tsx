import { redirect } from "next/navigation";
import { db } from "@/lib/db";
import { requireUserPage } from "@/lib/auth/guards";

export const dynamic = "force-dynamic";

/** /app — route the user to their first workspace (or onboarding). */
export default async function AppIndexPage() {
  const user = await requireUserPage();
  const membership = await db.membership.findFirst({
    where: { userId: user.id },
    include: { org: { select: { slug: true } } },
    orderBy: { createdAt: "asc" },
  });
  if (!membership) redirect("/onboarding");
  redirect(`/app/${membership.org.slug}/dashboard`);
}
