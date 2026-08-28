"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  MessageSquare,
  Lightbulb,
  Sparkles,
  ThumbsUp,
  Map,
  ClipboardList,
  Megaphone,
  Users,
  BarChart3,
  Plug,
  Settings,
  LogOut,
  ChevronsUpDown,
  Globe,
  Menu,
  X,
} from "lucide-react";
import { brand } from "@/config/brand";
import { cn } from "@/lib/utils";
import { Avatar } from "@/components/ui/misc";
import type { SessionUser } from "@/lib/auth/session";

const NAV = [
  { seg: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { seg: "feedback", label: "Feedback", icon: MessageSquare },
  { seg: "requests", label: "Feature Requests", icon: Lightbulb },
  { seg: "insights", label: "Insights", icon: Sparkles },
  { seg: "votes", label: "Votes", icon: ThumbsUp },
  { seg: "roadmap", label: "Roadmap", icon: Map },
  { seg: "surveys", label: "Surveys", icon: ClipboardList },
  { seg: "changelog", label: "Changelog", icon: Megaphone },
  { seg: "users", label: "Users", icon: Users },
  { seg: "analytics", label: "Analytics", icon: BarChart3 },
  { seg: "integrations", label: "Integrations", icon: Plug },
  { seg: "settings", label: "Settings", icon: Settings },
] as const;

type Org = { name: string; slug: string };

export function AppShell({
  user,
  org,
  orgs,
  role,
  children,
}: {
  user: SessionUser;
  org: Org;
  orgs: Org[];
  role: string;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [switcherOpen, setSwitcherOpen] = React.useState(false);
  const [mobileOpen, setMobileOpen] = React.useState(false);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  const sidebar = (
    <div className="flex h-full flex-col">
      {/* Brand */}
      <Link
        href="/"
        className="flex items-center gap-2 px-4 py-4 font-display text-sm font-bold tracking-[0.2em] text-ink"
      >
        <span className="inline-block h-2.5 w-2.5 rounded-full bg-accent-gradient shadow-glow" />
        {brand.wordmark}
      </Link>

      {/* Org switcher */}
      <div className="relative px-3 pb-3">
        <button
          onClick={() => setSwitcherOpen((v) => !v)}
          className="flex w-full items-center justify-between rounded-lg border border-line bg-surface px-3 py-2 text-left text-sm hover:border-line-strong"
        >
          <span className="truncate font-medium text-ink">{org.name}</span>
          <ChevronsUpDown size={14} className="shrink-0 text-ink-faint" />
        </button>
        {switcherOpen && (
          <div className="absolute left-3 right-3 z-30 mt-1 overflow-hidden rounded-lg border border-line bg-surface-overlay shadow-card">
            {orgs.map((o) => (
              <Link
                key={o.slug}
                href={`/app/${o.slug}/dashboard`}
                onClick={() => setSwitcherOpen(false)}
                className={cn(
                  "block px-3 py-2 text-sm hover:bg-line/40",
                  o.slug === org.slug ? "text-accent-soft" : "text-ink-muted"
                )}
              >
                {o.name}
              </Link>
            ))}
            <Link
              href="/onboarding"
              onClick={() => setSwitcherOpen(false)}
              className="block border-t border-line px-3 py-2 text-xs text-ink-faint hover:bg-line/40"
            >
              + New workspace
            </Link>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3">
        {NAV.map(({ seg, label, icon: Icon }) => {
          const href = `/app/${org.slug}/${seg}`;
          const active = pathname.startsWith(href);
          return (
            <Link
              key={seg}
              href={href}
              onClick={() => setMobileOpen(false)}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors",
                active
                  ? "bg-accent/12 text-accent-soft"
                  : "text-ink-muted hover:bg-surface-overlay hover:text-ink"
              )}
            >
              <Icon size={15} className={active ? "text-accent-soft" : "text-ink-faint"} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Portal link + user */}
      <div className="border-t border-line p-3">
        <a
          href={`/p/${org.slug}`}
          target="_blank"
          rel="noreferrer"
          className="mb-2 flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium text-ink-muted hover:bg-surface-overlay hover:text-ink"
        >
          <Globe size={15} className="text-ink-faint" />
          Public portal
        </a>
        <div className="flex items-center gap-2 rounded-lg px-3 py-2">
          <Avatar name={user.name} src={user.avatarUrl} size={26} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-ink">{user.name}</p>
            <p className="truncate text-[10px] uppercase tracking-wide text-ink-faint">
              {role.toLowerCase()}
            </p>
          </div>
          <button
            onClick={logout}
            aria-label="Log out"
            title="Log out"
            className="text-ink-faint hover:text-danger"
          >
            <LogOut size={14} />
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen bg-surface">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-60 border-r border-line bg-void/60 lg:block">
        {sidebar}
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-void/80"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-line bg-surface">
            <button
              onClick={() => setMobileOpen(false)}
              aria-label="Close menu"
              className="absolute right-3 top-4 text-ink-faint"
            >
              <X size={18} />
            </button>
            {sidebar}
          </aside>
        </div>
      )}

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col lg:pl-60">
        <header className="sticky top-0 z-10 flex h-12 items-center gap-3 border-b border-line bg-surface/80 px-4 backdrop-blur lg:hidden">
          <button
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
            className="text-ink-muted"
          >
            <Menu size={18} />
          </button>
          <span className="font-display text-xs font-bold tracking-[0.2em]">
            {brand.wordmark}
          </span>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
