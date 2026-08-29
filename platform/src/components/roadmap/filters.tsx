"use client";

import * as React from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { Select } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import type { FilterOption } from "./types";

/** Category / board / type filter bar for the roadmap — drives searchParams. */
export function RoadmapFilters({
  categories,
  boards,
}: {
  categories: FilterOption[];
  boards: FilterOption[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(sp.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.push(`${pathname}?${next.toString()}`);
  };

  const hasFilters = ["category", "board", "type"].some((k) => sp.get(k));

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <Select
        aria-label="Filter by category"
        value={sp.get("category") ?? ""}
        onChange={(e) => setParam("category", e.target.value)}
        className="w-auto min-w-[140px]"
      >
        <option value="">All categories</option>
        {categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Filter by board"
        value={sp.get("board") ?? ""}
        onChange={(e) => setParam("board", e.target.value)}
        className="w-auto min-w-[140px]"
      >
        <option value="">All boards</option>
        {boards.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Filter by type"
        value={sp.get("type") ?? ""}
        onChange={(e) => setParam("type", e.target.value)}
        className="w-auto min-w-[140px]"
      >
        <option value="">All types</option>
        <option value="FEEDBACK">Feedback</option>
        <option value="FEATURE_REQUEST">Feature request</option>
      </Select>
      {hasFilters && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push(pathname)}
        >
          Clear
        </Button>
      )}
    </div>
  );
}
