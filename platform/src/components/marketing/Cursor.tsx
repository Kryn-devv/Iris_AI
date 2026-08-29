"use client";

/**
 * Custom cursor for fine pointers only: a crisp dot, a lagging ring, and a
 * short comet trail — all rAF-interpolated. Magnetic pull toward elements
 * marked with [data-magnetic] (the element itself leans in slightly), ring
 * morphs over interactive targets. Disabled on touch and reduced motion.
 */
import { useEffect, useRef, useState } from "react";

const TRAIL = 3;

export function Cursor() {
  const [active, setActive] = useState(false);
  const dotRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);
  const trailRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const fine = window.matchMedia("(pointer: fine)").matches;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!fine || reduced) return;
    setActive(true);
  }, []);

  useEffect(() => {
    if (!active) return;
    document.documentElement.classList.add("nv-cursor");

    const mouse = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    const dot = { x: mouse.x, y: mouse.y };
    const ring = { x: mouse.x, y: mouse.y };
    const trail = Array.from({ length: TRAIL }, () => ({ x: mouse.x, y: mouse.y }));
    let magnet: HTMLElement | null = null;
    let interactive = false;
    let visible = false;
    let raf = 0;

    const onMove = (e: PointerEvent) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
      visible = true;
    };
    const onOver = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      magnet = t?.closest?.("[data-magnetic]") ?? null;
      interactive = !!t?.closest?.("a, button, [role='button'], input, textarea, select");
    };
    const onLeave = () => {
      visible = false;
    };
    const onEnter = () => {
      visible = true;
    };

    const frame = () => {
      raf = requestAnimationFrame(frame);
      let tx = mouse.x;
      let ty = mouse.y;
      let ringScale = interactive ? 1.7 : 1;
      if (magnet && magnet.isConnected) {
        const rect = magnet.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        // Cursor pulled toward the CTA; the CTA leans toward the cursor.
        tx = mouse.x + (cx - mouse.x) * 0.42;
        ty = mouse.y + (cy - mouse.y) * 0.42;
        ringScale = 1.9;
        const dx = (mouse.x - cx) * 0.14;
        const dy = (mouse.y - cy) * 0.14;
        magnet.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px)`;
        magnet.style.transition = "transform 120ms ease-out";
      } else if (magnet) {
        magnet = null;
      }

      dot.x += (tx - dot.x) * 0.5;
      dot.y += (ty - dot.y) * 0.5;
      ring.x += (tx - ring.x) * 0.18;
      ring.y += (ty - ring.y) * 0.18;
      let px = dot.x;
      let py = dot.y;
      for (let i = 0; i < TRAIL; i++) {
        const t = trail[i];
        t.x += (px - t.x) * 0.32;
        t.y += (py - t.y) * 0.32;
        px = t.x;
        py = t.y;
        const el = trailRefs.current[i];
        if (el) {
          el.style.transform = `translate3d(${t.x}px, ${t.y}px, 0) translate(-50%, -50%)`;
          el.style.opacity = visible ? String(0.28 - i * 0.08) : "0";
        }
      }
      if (dotRef.current) {
        dotRef.current.style.transform = `translate3d(${dot.x}px, ${dot.y}px, 0) translate(-50%, -50%) scale(${interactive ? 0.6 : 1})`;
        dotRef.current.style.opacity = visible ? "1" : "0";
      }
      if (ringRef.current) {
        ringRef.current.style.transform = `translate3d(${ring.x}px, ${ring.y}px, 0) translate(-50%, -50%) scale(${ringScale})`;
        ringRef.current.style.opacity = visible ? "1" : "0";
      }
    };

    // Reset any magnet transform when the pointer moves away.
    const onOut = (e: PointerEvent) => {
      const left = (e.target as HTMLElement | null)?.closest?.("[data-magnetic]");
      if (left && left instanceof HTMLElement) {
        left.style.transform = "";
      }
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerover", onOver, { passive: true });
    document.addEventListener("pointerout", onOut, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    document.documentElement.addEventListener("pointerenter", onEnter);
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerover", onOver);
      document.removeEventListener("pointerout", onOut);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      document.documentElement.removeEventListener("pointerenter", onEnter);
      document.documentElement.classList.remove("nv-cursor");
    };
  }, [active]);

  if (!active) return null;

  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-[80]">
      {/* Hide the native cursor while ours is live. */}
      <style>{`.nv-cursor, .nv-cursor a, .nv-cursor button, .nv-cursor [role='button'] { cursor: none !important; }`}</style>
      {Array.from({ length: TRAIL }).map((_, i) => (
        <div
          key={i}
          ref={(el) => {
            trailRefs.current[i] = el;
          }}
          className="absolute left-0 top-0 rounded-full bg-accent-soft"
          style={{ width: 5 - i, height: 5 - i, opacity: 0 }}
        />
      ))}
      <div
        ref={ringRef}
        className="absolute left-0 top-0 h-9 w-9 rounded-full border border-accent-soft/60"
        style={{ opacity: 0, transition: "opacity 200ms ease" }}
      />
      <div
        ref={dotRef}
        className="absolute left-0 top-0 h-1.5 w-1.5 rounded-full bg-ink"
        style={{ opacity: 0, transition: "opacity 200ms ease" }}
      />
    </div>
  );
}
