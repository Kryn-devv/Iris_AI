/**
 * Central brand configuration.
 *
 * Every user-visible brand string, color, and metadata value in the product
 * resolves through this file (plus the CSS variables in `globals.css`), so a
 * future rebrand is a one-file change — no component edits required.
 */
export const brand = {
  /** Product name, always rendered via this constant — never hardcoded. */
  name: "Novaris",
  /** Uppercase display form used in the marketing universe. */
  wordmark: "NOVARIS",
  /** One-line positioning statement. */
  tagline: "Stop guessing what to build.",
  /** Supporting line under the tagline. */
  subline:
    "Listen to your users. Understand what matters. Build with confidence.",
  /** Short product-category descriptor for meta tags and footers. */
  category: "Feedback intelligence platform",
  description:
    "Novaris turns scattered customer feedback into a single intelligent system: capture everything, understand what matters with AI, prioritize with real demand, and ship a roadmap your users can follow.",
  /** Used for support / from addresses in copy. */
  domain: "novaris.app",
  /** Session cookie name — brand-agnostic on purpose. */
  cookieName: "platform_session",
  guestCookieName: "platform_guest",
  social: {
    x: "https://x.com/novarishq",
    github: "https://github.com/novarishq",
  },
} as const;

export type Brand = typeof brand;
