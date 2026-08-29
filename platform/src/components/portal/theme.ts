import type * as React from "react";

/**
 * Portal theming helpers — pure, importable from server and client code.
 * The org's `brandColor` (a hex string) is applied as an inline override of
 * the accent CSS variables so every `text-accent` / `bg-accent` token in the
 * subtree picks it up without any raw hex inside components.
 */

/** "#7c6cff" | "7c6cff" | "#fff" -> "124 108 255" (space-separated channels). */
export function hexToRgbChannels(hex: string): string | null {
  const m = /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return null;
  let h = m[1]!;
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `${r} ${g} ${b}`;
}

function mixTowards(channels: string, target: number, amount: number): string {
  return channels
    .split(" ")
    .map((c) => Math.round(Number(c) + (target - Number(c)) * amount))
    .join(" ");
}

/**
 * Inline style overriding the accent tokens with the org brand color.
 * Returns undefined when no valid color is set (default theme applies).
 */
export function accentStyle(
  brandColor: string | null | undefined
): React.CSSProperties | undefined {
  if (!brandColor) return undefined;
  const base = hexToRgbChannels(brandColor);
  if (!base) return undefined;
  return {
    "--c-accent": base,
    "--c-accent-soft": mixTowards(base, 255, 0.28),
    "--c-accent-strong": mixTowards(base, 0, 0.18),
  } as React.CSSProperties;
}

/**
 * Extract a privacy-friendly YouTube embed URL from a watch/share/shorts URL.
 * Returns null when the URL is not recognizably YouTube.
 */
export function youTubeEmbedUrl(url: string): string | null {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    let id: string | null = null;
    if (host === "youtu.be") {
      id = u.pathname.slice(1).split("/")[0] ?? null;
    } else if (host === "youtube.com" || host === "m.youtube.com" || host === "youtube-nocookie.com") {
      if (u.pathname === "/watch") id = u.searchParams.get("v");
      else if (u.pathname.startsWith("/shorts/")) id = u.pathname.split("/")[2] ?? null;
      else if (u.pathname.startsWith("/embed/")) id = u.pathname.split("/")[2] ?? null;
    }
    if (!id || !/^[A-Za-z0-9_-]{6,20}$/.test(id)) return null;
    return `https://www.youtube-nocookie.com/embed/${id}`;
  } catch {
    return null;
  }
}

/** True when the URL points at a directly playable video file. */
export function isVideoFile(url: string): boolean {
  return /\.(mp4|webm|ogg|mov)(\?.*)?$/i.test(url);
}
