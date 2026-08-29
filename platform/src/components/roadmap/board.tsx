"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  KeyboardSensor,
  closestCorners,
  useSensor,
  useSensors,
  useDroppable,
  type DragStartEvent,
  type DragOverEvent,
  type DragEndEvent,
  type UniqueIdentifier,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  arrayMove,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  ChevronUp,
  Gauge,
  Lightbulb,
  MessageSquare,
  MessageCircle,
  MoreHorizontal,
  ExternalLink,
  X,
} from "lucide-react";
import type { PostStatus } from "@prisma/client";
import { POST_STATUS, ROADMAP_STATUSES } from "@/lib/status";
import { cn, compactNumber } from "@/lib/utils";
import type { RoadmapCard, RoadmapColumns } from "./types";

/** Minimum fractional gap before a column gets renormalized to 1..n. */
const MIN_GAP = 1e-6;

function findColumn(
  columns: RoadmapColumns,
  id: UniqueIdentifier
): PostStatus | null {
  const key = String(id);
  if (ROADMAP_STATUSES.includes(key as PostStatus)) return key as PostStatus;
  for (const status of ROADMAP_STATUSES) {
    if ((columns[status] ?? []).some((c) => c.id === key)) return status;
  }
  return null;
}

export function RoadmapBoard({
  orgSlug,
  initialColumns,
  canEdit,
}: {
  orgSlug: string;
  initialColumns: RoadmapColumns;
  canEdit: boolean;
}) {
  const router = useRouter();
  const [columns, setColumns] = React.useState<RoadmapColumns>(initialColumns);
  const [active, setActive] = React.useState<RoadmapCard | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  // Mirror state into a ref so drag handlers always see the latest layout.
  const columnsRef = React.useRef(columns);
  columnsRef.current = columns;
  // Snapshot for restoring on a cancelled drag; origin column for the event.
  const snapshotRef = React.useRef<RoadmapColumns | null>(null);
  const originRef = React.useRef<PostStatus | null>(null);

  // Server refresh (router.refresh) re-renders the server page with fresh
  // props — adopt them as the new source of truth.
  React.useEffect(() => setColumns(initialColumns), [initialColumns]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor)
  );

  const flash = (message: string) => {
    setError(message);
    window.setTimeout(() => setError(null), 4000);
  };

  const patchCard = React.useCallback(
    async (postId: string, body: Record<string, unknown>) => {
      const res = await fetch(`/api/orgs/${orgSlug}/roadmap/${postId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Could not update the roadmap");
      }
    },
    [orgSlug]
  );

  const onDragStart = (event: DragStartEvent) => {
    const col = findColumn(columnsRef.current, event.active.id);
    const card =
      col &&
      columnsRef.current[col]?.find((c) => c.id === String(event.active.id));
    snapshotRef.current = columnsRef.current;
    originRef.current = col;
    setActive(card ?? null);
  };

  const onDragOver = (event: DragOverEvent) => {
    const { active, over } = event;
    if (!over) return;
    const from = findColumn(columnsRef.current, active.id);
    const to = findColumn(columnsRef.current, over.id);
    if (!from || !to || from === to) return;
    setColumns((prev) => {
      const fromItems = [...(prev[from] ?? [])];
      const toItems = [...(prev[to] ?? [])];
      const idx = fromItems.findIndex((c) => c.id === String(active.id));
      if (idx === -1) return prev;
      const [moved] = fromItems.splice(idx, 1);
      const overIdx = toItems.findIndex((c) => c.id === String(over.id));
      const insertAt = overIdx >= 0 ? overIdx : toItems.length;
      toItems.splice(insertAt, 0, { ...moved!, status: to });
      return { ...prev, [from]: fromItems, [to]: toItems };
    });
  };

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    setActive(null);
    const origin = originRef.current;
    const cols = columnsRef.current;
    const activeId = String(active.id);
    const col = findColumn(cols, activeId);

    if (!over || !col || !origin) {
      if (snapshotRef.current) setColumns(snapshotRef.current);
      snapshotRef.current = null;
      return;
    }
    snapshotRef.current = null;

    let items = [...(cols[col] ?? [])];
    const oldIndex = items.findIndex((c) => c.id === activeId);
    if (oldIndex === -1) return;
    let newIndex = oldIndex;
    if (String(over.id) !== activeId) {
      const overCol = findColumn(cols, over.id);
      if (overCol === col) {
        const overIndex = items.findIndex((c) => c.id === String(over.id));
        if (overIndex >= 0) {
          items = arrayMove(items, oldIndex, overIndex);
          newIndex = overIndex;
        }
      }
    }

    const statusChanged = col !== origin;
    if (!statusChanged && newIndex === oldIndex) return; // dropped in place

    // Fractional midpoint order between the new neighbours.
    const prevOrder = newIndex > 0 ? items[newIndex - 1]!.roadmapOrder : null;
    const nextOrder =
      newIndex < items.length - 1 ? items[newIndex + 1]!.roadmapOrder : null;
    let newOrder: number;
    let renormalize = false;
    if (prevOrder === null && nextOrder === null) newOrder = 1;
    else if (prevOrder === null) newOrder = nextOrder! - 1;
    else if (nextOrder === null) newOrder = prevOrder + 1;
    else {
      newOrder = (prevOrder + nextOrder) / 2;
      if (nextOrder - prevOrder < MIN_GAP) renormalize = true;
    }

    items[newIndex] = {
      ...items[newIndex]!,
      roadmapOrder: newOrder,
      status: col,
    };
    const optimistic = { ...cols, [col]: items };
    setColumns(optimistic);

    void (async () => {
      try {
        if (statusChanged || !renormalize) {
          await patchCard(activeId, {
            ...(statusChanged ? { status: col } : {}),
            ...(renormalize ? {} : { order: newOrder }),
          });
        }
        if (renormalize) {
          const res = await fetch(`/api/orgs/${orgSlug}/roadmap`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              status: col,
              orderedIds: items.map((c) => c.id),
            }),
          });
          const json = await res.json().catch(() => null);
          if (!res.ok || !json?.ok) {
            throw new Error(
              json?.error?.message ?? "Could not reorder the column"
            );
          }
        }
      } catch (err) {
        flash(err instanceof Error ? err.message : "Update failed");
      } finally {
        router.refresh();
      }
    })();
  };

  const onDragCancel = () => {
    setActive(null);
    if (snapshotRef.current) setColumns(snapshotRef.current);
    snapshotRef.current = null;
  };

  const removeFromRoadmap = (card: RoadmapCard) => {
    setColumns((prev) => ({
      ...prev,
      [card.status]: (prev[card.status] ?? []).filter((c) => c.id !== card.id),
    }));
    void (async () => {
      try {
        await patchCard(card.id, { showOnRoadmap: false });
      } catch (err) {
        flash(err instanceof Error ? err.message : "Could not remove the post");
      } finally {
        router.refresh();
      }
    })();
  };

  const board = (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
      {ROADMAP_STATUSES.map((status) => (
        <RoadmapColumn
          key={status}
          status={status}
          cards={columns[status] ?? []}
          orgSlug={orgSlug}
          canEdit={canEdit}
          onRemove={removeFromRoadmap}
        />
      ))}
    </div>
  );

  return (
    <div>
      {error && (
        <div
          role="alert"
          className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"
        >
          {error}
        </div>
      )}
      {canEdit ? (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={onDragStart}
          onDragOver={onDragOver}
          onDragEnd={onDragEnd}
          onDragCancel={onDragCancel}
        >
          {board}
          <DragOverlay>
            {active ? (
              <CardBody card={active} dragging orgSlug={orgSlug} />
            ) : null}
          </DragOverlay>
        </DndContext>
      ) : (
        board
      )}
    </div>
  );
}

function RoadmapColumn({
  status,
  cards,
  orgSlug,
  canEdit,
  onRemove,
}: {
  status: PostStatus;
  cards: RoadmapCard[];
  orgSlug: string;
  canEdit: boolean;
  onRemove: (card: RoadmapCard) => void;
}) {
  const meta = POST_STATUS[status];
  const { setNodeRef, isOver } = useDroppable({
    id: status,
    disabled: !canEdit,
  });

  return (
    <section
      ref={setNodeRef}
      aria-label={`${meta.label} column`}
      className={cn(
        "flex min-h-[280px] flex-col rounded-xl border border-line bg-surface-raised/60 p-2 transition-colors",
        isOver && "border-accent/50 bg-accent/5"
      )}
    >
      <header className="flex items-center justify-between px-2 py-2">
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: meta.color }}
          />
          <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
            {meta.label}
          </h2>
        </div>
        <span className="rounded-full bg-line/50 px-2 py-0.5 text-[11px] font-medium text-ink-muted">
          {cards.length}
        </span>
      </header>
      <SortableContext
        items={cards.map((c) => c.id)}
        strategy={verticalListSortingStrategy}
      >
        <div className="flex flex-1 flex-col gap-2 p-1">
          {cards.map((card) => (
            <SortableCard
              key={card.id}
              card={card}
              orgSlug={orgSlug}
              canEdit={canEdit}
              onRemove={onRemove}
            />
          ))}
          {cards.length === 0 && (
            <p className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-xs text-ink-faint">
              {canEdit ? "Drop posts here" : "Nothing here yet"}
            </p>
          )}
        </div>
      </SortableContext>
    </section>
  );
}

function SortableCard({
  card,
  orgSlug,
  canEdit,
  onRemove,
}: {
  card: RoadmapCard;
  orgSlug: string;
  canEdit: boolean;
  onRemove: (card: RoadmapCard) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: card.id, disabled: !canEdit });

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(isDragging && "opacity-40")}
      {...attributes}
      {...(canEdit ? listeners : {})}
    >
      <CardBody
        card={card}
        orgSlug={orgSlug}
        menu={canEdit}
        onRemove={onRemove}
      />
    </div>
  );
}

function CardBody({
  card,
  orgSlug,
  dragging,
  menu,
  onRemove,
}: {
  card: RoadmapCard;
  orgSlug: string;
  dragging?: boolean;
  menu?: boolean;
  onRemove?: (card: RoadmapCard) => void;
}) {
  const TypeIcon = card.type === "FEATURE_REQUEST" ? Lightbulb : MessageSquare;
  return (
    <article
      className={cn(
        "group rounded-lg border border-line bg-surface-overlay p-3 shadow-card",
        dragging ? "rotate-1 border-accent/50" : "hover:border-line-strong"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium leading-snug text-ink">{card.title}</p>
        {menu && onRemove && (
          <CardMenu card={card} orgSlug={orgSlug} onRemove={onRemove} />
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-muted">
        <span className="inline-flex items-center gap-1" title={card.type === "FEATURE_REQUEST" ? "Feature request" : "Feedback"}>
          <TypeIcon size={12} aria-hidden className="text-ink-faint" />
        </span>
        {card.category && (
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: card.category.color }}
            />
            {card.category.name}
          </span>
        )}
        <span className="inline-flex items-center gap-1" title="Votes">
          <ChevronUp size={12} aria-hidden />
          {compactNumber(card.voteCount)}
        </span>
        <span className="inline-flex items-center gap-1" title="Comments">
          <MessageCircle size={12} aria-hidden />
          {compactNumber(card.commentCount)}
        </span>
        <span
          className="inline-flex items-center gap-1 text-accent-soft"
          title="Priority score"
        >
          <Gauge size={12} aria-hidden />
          {Math.round(card.priorityScore)}
        </span>
      </div>
    </article>
  );
}

function CardMenu({
  card,
  orgSlug,
  onRemove,
}: {
  card: RoadmapCard;
  orgSlug: string;
  onRemove: (card: RoadmapCard) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div
      ref={ref}
      className="relative shrink-0"
      // Keep menu interactions from starting a drag.
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        aria-label={`Actions for ${card.title}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="rounded p-1 text-ink-faint opacity-0 transition-opacity hover:bg-line/50 hover:text-ink focus:opacity-100 group-hover:opacity-100"
      >
        <MoreHorizontal size={14} aria-hidden />
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-44 rounded-lg border border-line bg-surface-overlay p-1 shadow-card">
          <Link
            href={`/app/${orgSlug}/feedback/${card.id}`}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-ink hover:bg-line/40"
            onClick={() => setOpen(false)}
          >
            <ExternalLink size={12} aria-hidden />
            Open post
          </Link>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onRemove(card);
            }}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-danger hover:bg-danger/10"
          >
            <X size={12} aria-hidden />
            Remove from roadmap
          </button>
        </div>
      )}
    </div>
  );
}
