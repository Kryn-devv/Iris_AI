import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/auth/session";
import { brand } from "@/config/brand";
import { RegisterForm } from "@/components/settings/auth-forms";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: `Create account · ${brand.name}` };

/** Only allow same-origin path redirects (must start with a single "/"). */
function safeNext(raw: string | string[] | undefined): string | null {
  if (typeof raw !== "string") return null;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) {
    return null;
  }
  return raw;
}

export default async function RegisterPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const next = safeNext(sp.next);

  const user = await getCurrentUser();
  if (user) redirect(next ?? "/app");

  return (
    <>
      <h1 className="mb-1 text-lg font-semibold text-ink">Create your account</h1>
      <p className="mb-6 text-sm text-ink-muted">
        {brand.tagline} Start collecting feedback in minutes.
      </p>
      <RegisterForm next={next} />
    </>
  );
}
