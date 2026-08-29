"use client";

import * as React from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { Search } from "lucide-react";
import { Input, Select } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { POST_STATUS } from "@/lib/status";
import type { CategoryOption, TagOption } from "./types";

const SENTIMENTS = [
  ["POSITIVE", "Positive"],
  ["NEUTRAL", "Neutral"],
  ["NEGATIVE", "Negative"],
] as const;

const SOURCES = [
  ["DASHBOARD", "Dashboard"],
  ["PORTAL", "Portal"],
  ["WIDGET", "Widget"],
  ["IMPORT", "Import"],
  ["API", "API"],
  ["EMAIL", "Email"],
] as const;

const SORTS = [
  ["recent", "Most recent"],
  ["votes", "Most votes"],
  ["priority", "Highest priority"],
  ["comments", "Most comments"],
] as const;

/** Filter bar for the feedback list — updates URL searchParams. */
export function PostFilters({
  categories,
  tags,
  showTypeFilter = true,
}: {
  categories: CategoryOption[];
  tags: TagOption[];
  showTypeFilter?: boolean;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const [q, setQ] = React.useState(sp.get("q") ?? "");

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(sp.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("page");
    router.push(`${pathname}?${next.toString()}`);
  };

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setParam("q", q.trim());
  };

  const hasFilters =
    ["status", "type", "category", "tag", "sentiment", "source", "q", "archived"].some(
      (k) => sp.get(k)
    );

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <form onSubmit={submitSearch} className="relative min-w-[200px] flex-1">
        <Search
          size={14}
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
        />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search title or body…"
          aria-label="Search posts"
          className="pl-8"
        />
      </form>
      <Select
        aria-label="Filter by status"
        value={sp.get("status") ?? ""}
        onChange={(e) => setParam("status", e.target.value)}
        className="w-auto min-w-[120px]"
      >
        <option value="">All statuses</option>
        {Object.entries(POST_STATUS).map(([value, meta]) => (
          <option key={value} value={value}>
            {meta.label}
          </option>
        ))}
      </Select>
      {showTypeFilter && (
        <Select
          aria-label="Filter by type"
          value={sp.get("type") ?? ""}
          onChange={(e) => setParam("type", e.target.value)}
          className="w-auto min-w-[110px]"
        >
          <option value="">All types</option>
          <option value="FEEDBACK">Feedback</option>
          <option value="FEATURE_REQUEST">Feature request</option>
        </Select>
      )}
      <Select
        aria-label="Filter by category"
        value={sp.get("category") ?? ""}
        onChange={(e) => setParam("category", e.target.value)}
        className="w-auto min-w-[120px]"
      >
        <option value="">All categories</option>
        {categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Filter by tag"
        value={sp.get("tag") ?? ""}
        onChange={(e) => setParam("tag", e.target.value)}
        className="w-auto min-w-[100px]"
      >
        <option value="">All tags</option>
        {tags.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Filter by sentiment"
        value={sp.get("sentiment") ?? ""}
        onChange={(e) => setParam("sentiment", e.target.value)}
        className="w-auto min-w-[110px]"
      >
        <option value="">Any sentiment</option>
        {SENTIMENTS.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Filter by source"
        value={sp.get("source") ?? ""}
        onChange={(e) => setParam("source", e.target.value)}
        className="w-auto min-w-[100px]"
      >
        <option value="">Any source</option>
        {SOURCES.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </Select>
      <Select
        aria-label="Sort by"
        value={sp.get("sort") ?? "recent"}
        onChange={(e) => setParam("sort", e.target.value)}
        className="w-auto min-w-[130px]"
      >
        {SORTS.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </Select>
      <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ink-muted">
        <input
          type="checkbox"
          checked={sp.get("archived") === "1"}
          onChange={(e) => setParam("archived", e.target.checked ? "1" : "")}
          className="h-3.5 w-3.5 accent-accent"
        />
        Archived
      </label>
      {hasFilters && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setQ("");
            router.push(pathname);
          }}
        >
          Clear
        </Button>
      )}
    </div>
  );
}
