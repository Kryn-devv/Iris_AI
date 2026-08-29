import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { db } from "@/lib/db";
import { requireUserPage } from "@/lib/auth/guards";
import { brand } from "@/config/brand";
import { OnboardingForm } from "@/components/settings/onboarding-form";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: `New workspace · ${brand.name}` };

/**
 * Post-signup onboarding: name the first workspace. Also reachable from the
 * org switcher to create additional workspaces.
 */
export default async function OnboardingPage() {
  const user = await requireUserPage();
  const existing = await db.membership.findFirst({
    where: { userId: user.id },
    include: { org: { select: { slug: true, name: true } } },
    orderBy: { createdAt: "asc" },
  });

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center bg-void px-4 py-10">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden"
      >
        <div className="absolute left-1/2 top-1/3 h-[420px] w-[640px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/10 blur-[120px]" />
      </div>

      <div className="relative mb-8 flex items-center gap-2.5 font-display text-sm font-bold tracking-[0.25em] text-ink">
        <span className="inline-block h-3 w-3 rounded-full bg-accent-gradient shadow-glow" />
        {brand.wordmark}
      </div>

      <div className="glass relative w-full max-w-sm rounded-2xl p-7">
        <h1 className="mb-1 text-lg font-semibold text-ink">
          {existing ? "Create another workspace" : `Welcome, ${user.name.split(" ")[0]}`}
        </h1>
        <p className="mb-6 text-sm text-ink-muted">
          Name your workspace. You&apos;ll get a public feedback portal, a
          starter board, and categories to organize what comes in.
        </p>
        <OnboardingForm />
        {existing && (
          <p className="mt-5 text-center text-xs text-ink-muted">
            <Link
              href={`/app/${existing.org.slug}/dashboard`}
              className="inline-flex items-center gap-1 text-accent-soft hover:underline"
            >
              <ArrowLeft size={12} />
              Back to {existing.org.name}
            </Link>
          </p>
        )}
      </div>
    </div>
  );
}
