"use client";

/**
 * Decides which experience to serve:
 * - SSR / first paint: the static Fallback (full copy — crawlable, robust).
 * - Capable clients (motion OK + WebGL OK): the 3D journey, loaded ssr:false,
 *   behind a branded veil that lifts once the canvas is live.
 * - Reduced motion / no WebGL / runtime 3D crash: the static Fallback.
 */
import { Component, useEffect, useState, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { AnimatePresence, motion } from "framer-motion";
import { brand } from "@/config/brand";
import { Fallback } from "./Fallback";

const Journey = dynamic(() => import("./Journey"), {
  ssr: false,
  loading: () => null,
});

function webglAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    const gl =
      canvas.getContext("webgl2") ||
      canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl");
    return !!gl;
  } catch {
    return false;
  }
}

class JourneyBoundary extends Component<
  { children: ReactNode; onError: () => void },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onError();
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function Veil({ visible }: { visible: boolean }) {
  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="veil"
          initial={{ opacity: 1 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: 0.9, ease: "easeInOut" } }}
          className="fixed inset-0 z-[70] flex items-center justify-center bg-void"
          aria-hidden
        >
          <div className="flex flex-col items-center gap-4">
            <span className="relative flex h-3 w-3">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/60" />
              <span className="relative inline-flex h-3 w-3 rounded-full bg-accent-soft" />
            </span>
            <span className="animate-pulse-soft font-display text-sm font-bold tracking-[0.4em] text-ink-muted">
              {brand.wordmark}
            </span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Experience() {
  const [mode, setMode] = useState<"pending" | "journey" | "static">("pending");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || !webglAvailable()) {
      setMode("static");
    } else {
      window.scrollTo(0, 0);
      setMode("journey");
    }
  }, []);

  if (mode !== "journey") {
    // Server render, the pre-decision frame, and the static experience.
    return <Fallback />;
  }

  return (
    <>
      <Veil visible={!ready} />
      <JourneyBoundary onError={() => setMode("static")}>
        <Journey onReady={() => setReady(true)} />
      </JourneyBoundary>
    </>
  );
}
