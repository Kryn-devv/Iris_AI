"use client";

/**
 * Elegant opt-in toggle for the synthesized ambient soundscape.
 * Off by default; never autoplays. Fades in/out via the audio engine.
 */
import { useEffect, useState } from "react";
import { Volume2, VolumeX } from "lucide-react";
import { ambientAudio } from "./audio";
import { journeyStore } from "./journeyState";
import { cn } from "@/lib/utils";

export function SoundToggle() {
  const [on, setOn] = useState(false);

  useEffect(() => ambientAudio.onChange(setOn), []);

  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={on ? "Turn ambient sound off" : "Turn ambient sound on"}
      data-magnetic
      onClick={() => ambientAudio.toggle(journeyStore.chapterIndex)}
      className={cn(
        "relative flex h-9 w-9 items-center justify-center rounded-full border transition-colors",
        on
          ? "border-accent/60 bg-accent/15 text-accent-soft shadow-glow"
          : "border-line text-ink-faint hover:border-line-strong hover:text-ink-muted"
      )}
    >
      {on ? <Volume2 className="h-4 w-4" aria-hidden /> : <VolumeX className="h-4 w-4" aria-hidden />}
      {on && (
        <span
          aria-hidden
          className="absolute -right-0.5 -top-0.5 h-2 w-2 animate-pulse-soft rounded-full bg-aurora"
        />
      )}
    </button>
  );
}
