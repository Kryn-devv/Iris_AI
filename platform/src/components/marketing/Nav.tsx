"use client";

/**
 * Fixed glass header: wordmark, sound toggle (journey mode only), login and
 * the primary CTA. Keyboard reachable; magnetic targets for the cursor.
 */
import Link from "next/link";
import { motion } from "framer-motion";
import { brand } from "@/config/brand";
import { SoundToggle } from "./SoundToggle";

export function Nav({ withSound = false }: { withSound?: boolean }) {
  return (
    <motion.header
      initial={{ y: -24, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.7, delay: 0.3, ease: [0.22, 1, 0.36, 1] }}
      className="fixed inset-x-0 top-0 z-50"
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link
          href="/"
          data-magnetic
          className="glass flex items-center gap-2.5 rounded-full px-4 py-2"
          aria-label={`${brand.name} home`}
        >
          <span aria-hidden className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-pulse-soft rounded-full bg-accent" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-accent-soft" />
          </span>
          <span className="font-display text-sm font-bold tracking-[0.28em] text-ink">
            {brand.wordmark}
          </span>
        </Link>

        <div className="glass flex items-center gap-1.5 rounded-full p-1.5 sm:gap-2">
          {withSound && <SoundToggle />}
          <Link
            href="/login"
            data-magnetic
            className="rounded-full px-3 py-1.5 text-sm font-medium text-ink-muted transition-colors hover:text-ink"
          >
            Log in
          </Link>
          <Link
            href="/register"
            data-magnetic
            className="rounded-full bg-accent px-4 py-1.5 text-sm font-semibold text-white shadow-glow transition-colors hover:bg-accent-strong"
          >
            Get started
          </Link>
        </div>
      </div>
    </motion.header>
  );
}
