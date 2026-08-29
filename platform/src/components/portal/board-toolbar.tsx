"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Search } from "lucide-react";
import type { PostStatus } from "@prisma/client";
import { POST_STATUS } from "@/lib/status";
import { Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { PORTAL_SORTS, type PortalSort } from "./types";

const FILTER_STATUSES: PostStatus[] = [
  "OPEN",
  "UNDER_CONSIDERATION",
  "PLANNED",
  "IN_PROGRESS",
  "SHIPPED",
];

const SORT_LABELS: Record<PortalSort, string> = {
  trending: "Trending",
  top: "Top voted",
  new: "Newest",
};

/**
 * Search box, status filter chips and sort select for the public board.
 * All state lives in the URL so the server page re-renders the list.
 */
export function BoardToolbar({
  q,
  status,
  sort,
}: {
  q: string;
  status: string;
  sort: PortalSort;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [search, setSearch] = React.useState(q);
  const debounce = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const setParams = React.useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams]
  );

  function onSearchChange(value: string) {
    setSearch(value);
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => setParams({ q: value.trim() || null }), 350);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <Search
            size={14}
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search ideas…"
            aria-label="Search ideas"
            className="h-9 w-full rounded-lg border border-line bg-surface pl-8 pr-3 text-sm text-ink placeholder:text-ink-faint focus:border-accent/60 focus:outline-none focus:ring-1 focus:ring-accent/40"
          />
        </div>
        <Select
          value={sort}
          onChange={(e) => setParams({ sort: e.target.value === "trending" ? null : e.target.value })}
          aria-label="Sort ideas"
          className="w-36"
        >
          {PORTAL_SORTS.map((s) => (
            <option key={s} value={s}>
              {SORT_LABELS[s]}
            </option>
          ))}
        </Select>
      </div>
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by status">
        <StatusChip label="All" active={!status} onClick={() => setParams({ status: null })} />
        {FILTER_STATUSES.map((s) => (
          <StatusChip
            key={s}
            label={POST_STATUS[s].label}
            color={POST_STATUS[s].color}
            active={status === s}
            onClick={() => setParams({ status: status === s ? null : s })}
          />
        ))}
      </div>
    </div>
  );
}

function StatusChip({
  label,
  color,
  active,
  onClick,
}: {
  label: string;
  color?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "border-accent/50 bg-accent/15 text-accent-soft"
          : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink"
      )}
    >
      {color && (
        <span
          aria-hidden
          className="h-1.5 w-1.5 rounded-full"
          style={{ backgroundColor: color }}
        />
      )}
      {label}
    </button>
  );
}
