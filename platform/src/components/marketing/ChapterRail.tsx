"use client";

/**
 * Game-like chapter rail on the right edge (desktop): one dot per chapter,
 * the active one lit. Click / keyboard to fly to any chapter via Lenis.
 */
import { useEffect, useState } from "react";
import { CHAPTERS } from "./copy";
import { journeyStore } from "./journeyState";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  hero: "Enter",
  chaos: "The chaos",
  capture: "Capture",
  analysis: "AI analysis",
  priorities: "Prioritize",
  voting: "Voting",
  roadmap: "Roadmap",
  surveys: "Surveys",
  changelog: "Changelog",
  finale: "Begin",
};

export function ChapterRail() {
  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => {
    setActiveIdx(journeyStore.chapterIndex);
    return journeyStore.onChapterChange(setActiveIdx);
  }, []);

  return (
    <nav
      aria-label="Journey chapters"
      className="fixed right-5 top-1/2 z-40 hidden -translate-y-1/2 lg:block"
    >
      <ul className="flex flex-col items-end gap-3">
        {CHAPTERS.map((chapter, i) => {
          const active = i === activeIdx;
          return (
            <li key={chapter.id} className="group flex items-center gap-3">
              <span
                className={cn(
                  "translate-x-1 text-[11px] font-medium tracking-wide text-ink-muted opacity-0 transition-all duration-200",
                  "group-focus-within:translate-x-0 group-focus-within:opacity-100 group-hover:translate-x-0 group-hover:opacity-100",
                  active && "text-accent-soft"
                )}
                aria-hidden
              >
                {LABELS[chapter.id]}
              </span>
              <button
                type="button"
                aria-label={`Go to chapter: ${LABELS[chapter.id]}`}
                aria-current={active ? "step" : undefined}
                onClick={() => journeyStore.requestScrollTo(chapter.range[0] + 0.004)}
                className={cn(
                  "h-2.5 w-2.5 rounded-full border transition-all duration-300",
                  active
                    ? "scale-125 border-accent-soft bg-accent shadow-glow"
                    : "border-line-strong bg-transparent hover:border-accent-soft/70 hover:bg-accent/30"
                )}
              />
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
