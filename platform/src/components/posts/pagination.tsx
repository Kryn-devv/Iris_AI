import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Server-rendered pagination links that preserve current filters.
 * `searchParams` is the already-resolved raw searchParams object.
 */
export function Pagination({
  page,
  totalPages,
  basePath,
  searchParams,
}: {
  page: number;
  totalPages: number;
  basePath: string;
  searchParams: Record<string, string | string[] | undefined>;
}) {
  if (totalPages <= 1) return null;

  const hrefFor = (p: number) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(searchParams)) {
      if (typeof v === "string" && k !== "page" && v) qs.set(k, v);
    }
    if (p > 1) qs.set("page", String(p));
    const s = qs.toString();
    return s ? `${basePath}?${s}` : basePath;
  };

  const linkCls = (disabled: boolean) =>
    cn(
      "inline-flex h-8 items-center gap-1 rounded-lg border border-line px-3 text-xs font-medium",
      disabled
        ? "pointer-events-none opacity-40 text-ink-faint"
        : "text-ink-muted hover:bg-surface-overlay hover:text-ink"
    );

  return (
    <nav
      aria-label="Pagination"
      className="mt-4 flex items-center justify-between"
    >
      <Link
        href={hrefFor(Math.max(1, page - 1))}
        aria-disabled={page <= 1}
        className={linkCls(page <= 1)}
      >
        <ChevronLeft size={13} aria-hidden /> Previous
      </Link>
      <span className="text-xs text-ink-faint">
        Page {page} of {totalPages}
      </span>
      <Link
        href={hrefFor(Math.min(totalPages, page + 1))}
        aria-disabled={page >= totalPages}
        className={linkCls(page >= totalPages)}
      >
        Next <ChevronRight size={13} aria-hidden />
      </Link>
    </nav>
  );
}
