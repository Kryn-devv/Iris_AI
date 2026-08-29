"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Lightbulb, Map, Megaphone } from "lucide-react";
import { cn } from "@/lib/utils";

const TABS = [
  { segment: "", label: "Feedback", icon: Lightbulb },
  { segment: "/roadmap", label: "Roadmap", icon: Map },
  { segment: "/changelog", label: "Changelog", icon: Megaphone },
] as const;

/** Portal section tabs (Feedback / Roadmap / Changelog). */
export function PortalNav({ orgSlug }: { orgSlug: string }) {
  const pathname = usePathname();
  const base = `/p/${orgSlug}`;

  function isActive(segment: string) {
    if (segment === "") {
      // Board is active on the index and on post detail pages.
      return (
        pathname === base ||
        pathname.startsWith(`${base}/posts`) ||
        pathname === `${base}/`
      );
    }
    return pathname.startsWith(`${base}${segment}`);
  }

  return (
    <nav aria-label="Portal sections" className="flex items-center gap-1">
      {TABS.map(({ segment, label, icon: Icon }) => {
        const active = isActive(segment);
        return (
          <Link
            key={label}
            href={`${base}${segment}`}
            aria-current={active ? "page" : undefined}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
              active
                ? "bg-accent/15 text-accent-soft"
                : "text-ink-muted hover:bg-surface-overlay hover:text-ink"
            )}
          >
            <Icon size={14} aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
