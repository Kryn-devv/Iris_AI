"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

export type SettingsTab = { href: string; label: string };

/** Horizontal sub-nav for the settings section (link-based tabs). */
export function SettingsNav({ tabs }: { tabs: SettingsTab[] }) {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Settings sections"
      className="mb-6 inline-flex items-center gap-1 rounded-lg border border-line bg-surface p-1"
    >
      {tabs.map((tab) => {
        const active =
          pathname === tab.href || pathname.startsWith(`${tab.href}/`);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
              active
                ? "bg-surface-overlay text-ink shadow-sm"
                : "text-ink-faint hover:text-ink-muted"
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
