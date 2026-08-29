import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { brand } from "@/config/brand";
import { Avatar } from "@/components/ui/misc";
import { getPortalOrg } from "@/components/portal/data";
import { accentStyle } from "@/components/portal/theme";
import { PortalNav } from "@/components/portal/nav";

export const dynamic = "force-dynamic";

type Props = {
  children: React.ReactNode;
  params: Promise<{ orgSlug: string }>;
};

export async function generateMetadata({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}): Promise<Metadata> {
  const { orgSlug } = await params;
  const org = await getPortalOrg(orgSlug);
  if (!org) return { title: "Portal not found" };
  return {
    title: {
      default: `${org.name} — Feedback`,
      template: `%s · ${org.name}`,
    },
    description:
      org.portalIntro ??
      `Share ideas, vote on what matters, and follow what ${org.name} ships next.`,
  };
}

export default async function PortalLayout({ children, params }: Props) {
  const { orgSlug } = await params;
  const org = await getPortalOrg(orgSlug);
  if (!org) notFound();

  return (
    <div
      style={accentStyle(org.brandColor)}
      className="flex min-h-screen flex-col bg-surface"
    >
      {/* Ambient accent glow behind the header */}
      <div className="relative">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-radial-fade"
        />
        <header className="relative mx-auto w-full max-w-5xl px-4 pt-10 sm:pt-14">
          <div className="flex items-center gap-3">
            <Avatar name={org.name} src={org.logoUrl} size={40} />
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase tracking-widest text-ink-faint">
                {org.name}
              </p>
              <h1 className="truncate font-display text-xl font-semibold tracking-tight text-ink sm:text-2xl">
                {org.portalHeadline ?? `Help shape ${org.name}`}
              </h1>
            </div>
          </div>
          {org.portalIntro && (
            <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-muted">
              {org.portalIntro}
            </p>
          )}
          <div className="mt-6 border-b border-line pb-3">
            <PortalNav orgSlug={org.slug} />
          </div>
        </header>
      </div>

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 pb-20 pt-8">
        {children}
      </main>

      <footer className="border-t border-line py-6">
        <div className="mx-auto flex w-full max-w-5xl items-center justify-center px-4">
          <Link
            href="/"
            className="text-xs text-ink-faint transition-colors hover:text-ink-muted"
          >
            Powered by{" "}
            <span className="font-medium text-accent-soft">{brand.name}</span>
          </Link>
        </div>
      </footer>
    </div>
  );
}
