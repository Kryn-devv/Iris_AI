/**
 * Scroll-timeline utilities. The whole journey maps window scroll to a single
 * progress value in [0, 1]; chapters own sub-ranges of it (see copy.ts) and
 * every 3D/DOM animation derives from chapter-local progress computed here.
 */
import { CHAPTERS } from "@/components/marketing/copy";

/** Total scrollable height of the journey, in viewport-heights. */
export const TOTAL_VH = 950;

export const CHAPTER_RANGES: [number, number][] = CHAPTERS.map((c) => c.range);

export function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

export function clamp(v: number, min: number, max: number): number {
  return v < min ? min : v > max ? max : v;
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Local progress of `p` inside window [a, b], clamped to 0..1. */
export function segmentProgress(p: number, range: [number, number]): number {
  return clamp01((p - range[0]) / (range[1] - range[0]));
}

/** 0→1→0 envelope: rises over `fadeIn`, holds, falls over the last `fadeOut`. */
export function fadeWindow(local: number, fadeIn = 0.14, fadeOut = 0.14): number {
  const up = clamp01(local / fadeIn);
  const down = clamp01((1 - local) / fadeOut);
  return Math.min(up, down);
}

export function smoothstep(t: number): number {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
}

export function easeOutCubic(t: number): number {
  const x = clamp01(t);
  return 1 - Math.pow(1 - x, 3);
}

export function easeInOutCubic(t: number): number {
  const x = clamp01(t);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
}

export function easeOutExpo(t: number): number {
  const x = clamp01(t);
  return x === 1 ? 1 : 1 - Math.pow(2, -10 * x);
}

/** Frame-rate independent damping factor (use with lerp). */
export function damp(lambda: number, dt: number): number {
  return 1 - Math.exp(-lambda * dt);
}

/** Which chapter index contains progress p (nearest when between ranges). */
export function chapterIndexAt(p: number): number {
  for (let i = CHAPTER_RANGES.length - 1; i >= 0; i--) {
    if (p >= CHAPTER_RANGES[i][0]) return i;
  }
  return 0;
}

/**
 * Cheap deterministic pseudo-random for stable per-instance variation
 * (no Math.random in render paths → identical layout every visit).
 */
export function hash11(seed: number): number {
  let x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  x -= Math.floor(x);
  return x;
}

export function hash3(seed: number): [number, number, number] {
  return [hash11(seed), hash11(seed + 101.3), hash11(seed + 517.7)];
}
