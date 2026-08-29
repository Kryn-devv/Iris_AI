import Link from "next/link";
import { brand } from "@/config/brand";

/** Centered card chrome shared by /login and /register. */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center bg-void px-4 py-10">
      {/* Soft ambient glow behind the card */}
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

      <div className="glass relative w-full max-w-sm rounded-2xl p-7">
        {children}
      </div>

      <p className="relative mt-8 text-xs text-ink-faint">
        {brand.category} · {brand.domain}
      </p>
    </div>
  );
}
