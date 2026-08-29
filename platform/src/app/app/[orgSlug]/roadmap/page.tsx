import { Suspense } from "react";
import Link from "next/link";
import { ExternalLink } from "lucide-react";
import { db } from "@/lib/db";
import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";
import { PageHeader } from "@/components/ui/misc";
import { ROADMAP_STATUSES } from "@/lib/status";
import { RoadmapBoard } from "@/components/roadmap/board";
import { RoadmapFilters } from "@/components/roadmap/filters";
import { AddToRoadmapButton } from "@/components/roadmap/add-dialog";
import type { RoadmapCard, RoadmapColumns } from "@/components/roadmap/types";
import {
  ROADMAP_CARD_SELECT,
  parsePostType,
  roadmapWhere,
} from "@/app/api/orgs/[orgSlug]/roadmap/helpers";

export const dynamic = "force-dynamic";

export default async function RoadmapPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgSlug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgSlug } = await params;
  const sp = await searchParams;
  const ctx = await requireOrgPage(orgSlug);
  const canEdit = roleAtLeast(ctx.role, "MEMBER");

  const [posts, categories, boards] = await Promise.all([
    db.post.findMany({
      where: roadmapWhere(ctx.org.id, {
        categoryId: typeof sp.category === "string" ? sp.category : undefined,
        boardId: typeof sp.board === "string" ? sp.board : undefined,
        type: parsePostType(typeof sp.type === "string" ? sp.type : undefined),
      }),
      orderBy: [{ roadmapOrder: "asc" }, { createdAt: "asc" }],
      select: ROADMAP_CARD_SELECT,
    }),
    db.category.findMany({
      where: { orgId: ctx.org.id },
      orderBy: { name: "asc" },
      select: { id: true, name: true },
    }),
    db.board.findMany({
      where: { orgId: ctx.org.id },
      orderBy: { createdAt: "asc" },
      select: { id: true, name: true },
    }),
  ]);

  const columns: RoadmapColumns = Object.fromEntries(
    ROADMAP_STATUSES.map((status) => [
      status,
      posts.filter((p) => p.status === status) as RoadmapCard[],
    ])
  );

  return (
    <div>
      <PageHeader
        title="Roadmap"
        description={
          canEdit
            ? "Drag posts between columns to update their status, or within a column to reprioritize."
            : "What the team is considering, building and shipping."
        }
        actions={
          <div className="flex items-center gap-2">
            <Link
              href={`/p/${orgSlug}/roadmap`}
              target="_blank"
              className="inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium text-ink-muted transition-colors hover:bg-surface-overlay hover:text-ink"
            >
              <ExternalLink size={13} aria-hidden />
              Public roadmap
            </Link>
            {canEdit && <AddToRoadmapButton orgSlug={orgSlug} />}
          </div>
        }
      />
      <Suspense>
        <RoadmapFilters categories={categories} boards={boards} />
      </Suspense>
      <RoadmapBoard
        orgSlug={orgSlug}
        initialColumns={columns}
        canEdit={canEdit}
      />
    </div>
  );
}
