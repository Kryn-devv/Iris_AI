"use client";

/**
 * HTML copy overlays for every chapter — real DOM text (accessible, crawlable)
 * pinned over the canvas and synced to scroll. Styles are written imperatively
 * from the journey store so scrolling never re-renders React.
 */
import { useEffect, useRef } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { CHAPTERS, type ChapterCopy } from "./copy";
import { journeyStore } from "./journeyState";
import {
  segmentProgress,
  fadeWindow,
  smoothstep,
  clamp01,
} from "@/components/three/timeline";
import { cn } from "@/lib/utils";

const TONE_CLASS: Record<string, string> = {
  accent: "text-accent-soft",
  aurora: "text-aurora",
  ember: "text-ember",
  success: "text-success",
};

function chapterAlpha(chapter: ChapterCopy, p: number): number {
  const local = segmentProgress(p, chapter.range);
  if (chapter.id === "hero") {
    return p <= chapter.range[0] ? 1 : 1 - smoothstep(segmentProgress(local, [0.5, 0.95]));
  }
  if (chapter.id === "finale") {
    return smoothstep(segmentProgress(local, [0.12, 0.5]));
  }
  return fadeWindow(local, 0.16, 0.14);
}

function OverlayChapter({ chapter }: { chapter: ChapterCopy }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const blockRef = useRef<HTMLDivElement>(null);
  const revealRefs = useRef<(HTMLElement | null)[]>([]);

  useEffect(
    () =>
      journeyStore.subscribe((p) => {
        const root = rootRef.current;
        const block = blockRef.current;
        if (!root || !block) return;
        const local = segmentProgress(p, chapter.range);
        const alpha = chapterAlpha(chapter, p);
        const hidden = alpha < 0.004;
        root.style.visibility = hidden ? "hidden" : "visible";
        if (hidden) return;
        const dir = local < 0.5 ? 1 : -1;
        block.style.opacity = alpha.toFixed(3);
        block.style.transform = `translateY(${((1 - alpha) * 26 * dir).toFixed(1)}px)`;
        root.style.pointerEvents = alpha > 0.5 ? "" : "none";
        // Staggered reveals (stats, bullets).
        const items = revealRefs.current;
        for (let i = 0; i < items.length; i++) {
          const el = items[i];
          if (!el) continue;
          const v = smoothstep(segmentProgress(local, [0.18 + i * 0.07, 0.3 + i * 0.07]));
          el.style.opacity = v.toFixed(3);
          el.style.transform = `translateY(${((1 - v) * 18).toFixed(1)}px)`;
        }
      }),
    [chapter]
  );

  let reveal = 0;
  const Heading: "h1" | "h2" = chapter.id === "hero" ? "h1" : "h2";

  return (
    <div
      ref={rootRef}
      className={cn(
        "pointer-events-none fixed inset-0 z-30 flex items-center",
        chapter.align === "center" && "justify-center text-center",
        chapter.align === "left" && "justify-center md:justify-start",
        chapter.align === "right" && "justify-center md:justify-end"
      )}
      style={{ visibility: chapter.id === "hero" ? "visible" : "hidden" }}
    >
      <div
        ref={blockRef}
        className={cn(
          "relative mx-6 max-w-xl md:mx-16 lg:mx-24",
          chapter.align === "center" && "max-w-3xl"
        )}
        style={{ opacity: chapter.id === "hero" ? 1 : 0 }}
      >
        {/* Soft scrim so copy stays readable over bright 3D moments. */}
        <div
          aria-hidden
          className="absolute -inset-12 -z-10"
          style={{
            background:
              "radial-gradient(ellipse at center, rgb(7 8 14 / 0.62) 0%, rgb(7 8 14 / 0.3) 55%, transparent 78%)",
          }}
        />

        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.34em] text-accent-soft sm:text-xs">
          {chapter.kicker}
        </p>
        <Heading
          className={cn(
            "mt-4 font-display font-bold leading-[1.02] tracking-tight text-ink",
            chapter.align === "center"
              ? "text-4xl sm:text-6xl lg:text-7xl"
              : "text-3xl sm:text-5xl lg:text-6xl"
          )}
        >
          {chapter.id === "hero" || chapter.id === "finale" ? (
            <span className="text-gradient">{chapter.headline}</span>
          ) : (
            chapter.headline
          )}
        </Heading>
        <p
          className={cn(
            "mt-5 text-base leading-relaxed text-ink-muted sm:text-lg",
            chapter.align === "center" && "mx-auto max-w-xl"
          )}
        >
          {chapter.sub}
        </p>

        {chapter.bullets && (
          <ul
            className={cn(
              "mt-6 flex flex-wrap gap-2",
              chapter.align === "center" && "justify-center",
              chapter.align === "right" && "md:justify-end"
            )}
          >
            {chapter.bullets.map((bullet) => {
              const i = reveal++;
              return (
                <li
                  key={bullet}
                  ref={(el) => {
                    revealRefs.current[i] = el;
                  }}
                  className="glass rounded-full px-3.5 py-1.5 text-xs font-medium text-ink sm:text-sm"
                  style={{ opacity: 0 }}
                >
                  <span aria-hidden className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-aurora align-middle" />
                  {bullet}
                </li>
              );
            })}
          </ul>
        )}

        {chapter.stats && (
          <dl className="mt-7 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {chapter.stats.map((stat) => {
              const i = reveal++;
              return (
                <div
                  key={stat.label}
                  ref={(el) => {
                    revealRefs.current[i] = el;
                  }}
                  className="glass rounded-xl px-4 py-3 text-left"
                  style={{ opacity: 0 }}
                >
                  <dd
                    className={cn(
                      "font-display text-xl font-bold tracking-tight sm:text-2xl",
                      TONE_CLASS[stat.tone ?? "accent"]
                    )}
                  >
                    {stat.value}
                  </dd>
                  <dt className="mt-0.5 text-[11px] uppercase tracking-[0.18em] text-ink-faint">
                    {stat.label}
                  </dt>
                </div>
              );
            })}
          </dl>
        )}

        {chapter.ctas && (
          <div
            className={cn(
              "mt-9 flex flex-wrap items-center gap-4",
              chapter.align === "center" && "justify-center"
            )}
          >
            {chapter.ctas.map((cta) =>
              cta.variant === "primary" ? (
                <Link
                  key={cta.label}
                  href={cta.href}
                  data-magnetic
                  className="pointer-events-auto inline-flex h-12 items-center rounded-full bg-accent px-7 text-sm font-semibold text-white shadow-glow-lg transition-colors hover:bg-accent-strong"
                >
                  {cta.label}
                </Link>
              ) : (
                <Link
                  key={cta.label}
                  href={cta.href}
                  data-magnetic
                  className="pointer-events-auto inline-flex h-12 items-center rounded-full border border-line-strong px-7 text-sm font-medium text-ink transition-colors hover:border-accent-soft/60 hover:text-accent-soft"
                >
                  {cta.label}
                </Link>
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ScrollHint() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(
    () =>
      journeyStore.subscribe((p) => {
        const el = ref.current;
        if (!el) return;
        const alpha = 1 - clamp01(p / 0.02);
        el.style.opacity = alpha.toFixed(3);
        el.style.visibility = alpha < 0.01 ? "hidden" : "visible";
      }),
    []
  );

  return (
    <div
      ref={ref}
      aria-hidden
      className="pointer-events-none fixed inset-x-0 bottom-7 z-30 flex flex-col items-center gap-2"
    >
      <span className="text-[11px] font-medium uppercase tracking-[0.3em] text-ink-faint">
        Scroll to enter
      </span>
      <motion.div
        animate={{ y: [0, 7, 0] }}
        transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
        className="flex h-9 w-6 items-start justify-center rounded-full border border-line-strong pt-1.5"
      >
        <ChevronDown className="h-3.5 w-3.5 text-accent-soft" />
      </motion.div>
    </div>
  );
}

export function Overlay() {
  return (
    <div aria-live="off">
      {CHAPTERS.map((chapter) => (
        <OverlayChapter key={chapter.id} chapter={chapter} />
      ))}
      <ScrollHint />
    </div>
  );
}
