/**
 * Shared marketing footer — used by the journey (after the scroll container)
 * and by the static fallback. No hooks; SSR-safe.
 */
import Link from "next/link";
import { brand } from "@/config/brand";
import { FOOTER_LINKS } from "./copy";

export function Footer() {
  return (
    <footer className="relative z-10 border-t border-line bg-void">
      <div className="mx-auto max-w-6xl px-6 py-14">
        <div className="flex flex-col gap-10 md:flex-row md:items-start md:justify-between">
          <div className="max-w-sm">
            <div className="flex items-center gap-2.5">
              <span aria-hidden className="h-2.5 w-2.5 rounded-full bg-accent-soft" />
              <span className="font-display text-sm font-bold tracking-[0.28em] text-ink">
                {brand.wordmark}
              </span>
            </div>
            <p className="mt-3 text-sm text-ink-muted">{brand.category}.</p>
            <p className="mt-1 text-sm text-ink-faint">{brand.tagline}</p>
          </div>

          <nav aria-label="Footer">
            <ul className="grid grid-cols-2 gap-x-12 gap-y-3 sm:grid-cols-3">
              {FOOTER_LINKS.map((link) => (
                <li key={link.href + link.label}>
                  <Link
                    href={link.href}
                    className="text-sm text-ink-muted transition-colors hover:text-ink"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        </div>

        <div className="mt-12 flex flex-col gap-2 border-t border-line/60 pt-6 text-xs text-ink-faint sm:flex-row sm:items-center sm:justify-between">
          <p>
            © {new Date().getFullYear()} {brand.name} · {brand.domain}
          </p>
          <div className="flex items-center gap-4">
            <a
              href={brand.social.x}
              className="transition-colors hover:text-ink-muted"
              target="_blank"
              rel="noreferrer"
            >
              X
            </a>
            <a
              href={brand.social.github}
              className="transition-colors hover:text-ink-muted"
              target="_blank"
              rel="noreferrer"
            >
              GitHub
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
