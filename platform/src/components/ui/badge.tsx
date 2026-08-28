import * as React from "react";
import { cn } from "@/lib/utils";

type Tone =
  | "neutral"
  | "accent"
  | "aurora"
  | "success"
  | "warning"
  | "danger"
  | "ember";

const tones: Record<Tone, string> = {
  neutral: "bg-line/50 text-ink-muted border-line",
  accent: "bg-accent/15 text-accent-soft border-accent/30",
  aurora: "bg-aurora/10 text-aurora border-aurora/30",
  success: "bg-success/10 text-success border-success/30",
  warning: "bg-warning/10 text-warning border-warning/30",
  danger: "bg-danger/10 text-danger border-danger/30",
  ember: "bg-ember/10 text-ember border-ember/30",
};

export function Badge({
  tone = "neutral",
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4",
        tones[tone],
        className
      )}
      {...props}
    />
  );
}

export type { Tone as BadgeTone };
