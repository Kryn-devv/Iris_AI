import type { Metadata } from "next";
import Link from "next/link";
import { MailX } from "lucide-react";
import { db } from "@/lib/db";
import { getCurrentUser } from "@/lib/auth/session";
import { brand } from "@/config/brand";
import { Badge } from "@/components/ui/badge";
import { AcceptInviteButton } from "@/components/settings/invite-accept";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: `Invitation · ${brand.name}` };

function Chrome({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center bg-void px-4 py-10">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden"
      >
        <div className="absolute left-1/2 top-1/3 h-[420px] w-[640px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/10 blur-[120px]" />
      </div>
      <Link
        href="/"
        className="relative mb-8 flex items-center gap-2.5 font-display text-sm font-bold tracking-[0.25em] text-ink"
      >
        <span className="inline-block h-3 w-3 rounded-full bg-accent-gradient shadow-glow" />
        {brand.wordmark}
      </Link>
      <div className="glass relative w-full max-w-sm rounded-2xl p-7">{children}</div>
    </div>
  );
}

export default async function InvitePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  const invite = await db.invite.findUnique({
    where: { token },
    include: { org: { select: { id: true, name: true, slug: true } } },
  });

  const invalid =
    !invite || invite.acceptedAt !== null || invite.expiresAt < new Date();

  if (invalid) {
    return (
      <Chrome>
        <div className="text-center">
          <MailX size={22} className="mx-auto mb-3 text-ink-faint" />
          <h1 className="mb-1 text-lg font-semibold text-ink">
            This invite isn&apos;t valid
          </h1>
          <p className="mb-6 text-sm text-ink-muted">
            The link may have expired, been revoked, or already been used. Ask a
            workspace admin to send you a new one.
          </p>
          <Link href="/login" className="text-sm text-accent-soft hover:underline">
            Go to sign in
          </Link>
        </div>
      </Chrome>
    );
  }

  const user = await getCurrentUser();
  const nextPath = `/invite/${token}`;
  const membership = user
    ? await db.membership.findUnique({
        where: { userId_orgId: { userId: user.id, orgId: invite.org.id } },
        select: { id: true },
      })
    : null;

  return (
    <Chrome>
      <h1 className="mb-1 text-lg font-semibold text-ink">
        Join {invite.org.name}
      </h1>
      <p className="mb-4 text-sm text-ink-muted">
        You&apos;ve been invited to the{" "}
        <span className="font-medium text-ink">{invite.org.name}</span> workspace
        on {brand.name} as{" "}
        <Badge tone="accent">
          {invite.role.charAt(0) + invite.role.slice(1).toLowerCase()}
        </Badge>
      </p>
      <p className="mb-6 text-xs text-ink-faint">
        Invite sent to {invite.email} · expires{" "}
        {invite.expiresAt.toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          year: "numeric",
        })}
      </p>

      {user ? (
        membership ? (
          <div className="space-y-3">
            <p className="text-sm text-ink-muted">
              You&apos;re already a member of this workspace.
            </p>
            <Link
              href={`/app/${invite.org.slug}/dashboard`}
              className="block w-full rounded-lg bg-accent px-4 py-2 text-center text-sm font-medium text-white shadow-glow hover:bg-accent-strong"
            >
              Open {invite.org.name}
            </Link>
          </div>
        ) : (
          <div className="space-y-3">
            {user.email.toLowerCase() !== invite.email.toLowerCase() && (
              <p className="rounded-lg border border-line bg-surface p-3 text-xs text-ink-muted">
                You&apos;re signed in as{" "}
                <span className="text-ink">{user.email}</span>; this invite was
                sent to <span className="text-ink">{invite.email}</span>. You can
                still accept it with this account.
              </p>
            )}
            <AcceptInviteButton orgSlug={invite.org.slug} token={token} />
          </div>
        )
      ) : (
        <div className="space-y-3">
          <Link
            href={`/register?next=${encodeURIComponent(nextPath)}`}
            className="block w-full rounded-lg bg-accent px-4 py-2 text-center text-sm font-medium text-white shadow-glow hover:bg-accent-strong"
          >
            Create an account to join
          </Link>
          <Link
            href={`/login?next=${encodeURIComponent(nextPath)}`}
            className="block w-full rounded-lg border border-line bg-surface-overlay px-4 py-2 text-center text-sm font-medium text-ink hover:bg-line/60"
          >
            I already have an account
          </Link>
        </div>
      )}
    </Chrome>
  );
}
