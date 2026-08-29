"use client";

/**
 * The full 3D journey: one persistent fixed <Canvas>, a tall scroll container
 * whose progress drives everything, Lenis smooth scrolling feeding GSAP's
 * ScrollTrigger, chapter overlays, nav, custom cursor and chapter rail.
 * Loaded with ssr:false from Experience — window access is safe here.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { PerformanceMonitor } from "@react-three/drei";
import Lenis from "lenis";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Scene from "@/components/three/Scene";
import { TOTAL_VH } from "@/components/three/timeline";
import { journeyStore } from "./journeyState";
import { ambientAudio } from "./audio";
import { Overlay } from "./Overlay";
import { Nav } from "./Nav";
import { Cursor } from "./Cursor";
import { ChapterRail } from "./ChapterRail";
import { Footer } from "./Footer";

export default function Journey({ onReady }: { onReady?: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [frameloop, setFrameloop] = useState<"always" | "never">("always");
  const [dpr, setDpr] = useState<number | [number, number]>([1, 2]);

  /** Quality tier decided once at boot: mobile gets ~35% of the particles. */
  const perf = useMemo(() => {
    const mobile =
      typeof window !== "undefined" &&
      (window.matchMedia("(pointer: coarse)").matches ||
        window.matchMedia("(max-width: 768px)").matches);
    const particleScale = mobile ? 0.35 : 1;
    journeyStore.isMobile = mobile;
    journeyStore.particleScale = particleScale;
    return { mobile, particleScale };
  }, []);

  useEffect(() => {
    gsap.registerPlugin(ScrollTrigger);

    const lenis = new Lenis({
      duration: 1.15,
      smoothWheel: true,
    });
    lenis.on("scroll", ScrollTrigger.update);
    const tick = (time: number) => lenis.raf(time * 1000);
    gsap.ticker.add(tick);
    gsap.ticker.lagSmoothing(0);

    const container = containerRef.current;
    const trigger = ScrollTrigger.create({
      trigger: container ?? undefined,
      start: "top top",
      end: "bottom bottom",
      onUpdate: (self) => {
        journeyStore.setProgress(self.progress, lenis.velocity);
      },
    });

    journeyStore.bindScrollTo((p) => {
      lenis.scrollTo(p * lenis.limit, { duration: 1.6 });
    });

    const onPointer = (e: PointerEvent) => {
      journeyStore.setPointer(
        (e.clientX / window.innerWidth) * 2 - 1,
        (e.clientY / window.innerHeight) * 2 - 1
      );
    };
    window.addEventListener("pointermove", onPointer, { passive: true });

    const onVisibility = () => {
      const hidden = document.visibilityState === "hidden";
      journeyStore.visible = !hidden;
      setFrameloop(hidden ? "never" : "always");
      ambientAudio.setSuspended(hidden);
    };
    document.addEventListener("visibilitychange", onVisibility);

    const unsubChapter = journeyStore.onChapterChange((i) => ambientAudio.setChapter(i));

    ScrollTrigger.refresh();

    return () => {
      unsubChapter();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointermove", onPointer);
      journeyStore.bindScrollTo(null);
      trigger.kill();
      gsap.ticker.remove(tick);
      lenis.destroy();
      void ambientAudio.disable();
    };
  }, []);

  return (
    <div className="bg-void">
      {/* The universe — one persistent canvas behind everything. */}
      <div className="fixed inset-0 z-0" aria-hidden>
        <Canvas
          frameloop={frameloop}
          dpr={dpr}
          camera={{ position: [0, 0.8, 15], fov: 55, near: 0.1, far: 260 }}
          gl={{
            antialias: true,
            alpha: false,
            powerPreference: "high-performance",
            stencil: false,
          }}
          onCreated={({ gl }) => {
            gl.domElement.addEventListener(
              "webglcontextlost",
              (e) => e.preventDefault(),
              false
            );
            onReady?.();
          }}
        >
          <PerformanceMonitor
            onDecline={() => setDpr(1)}
            onIncline={() => setDpr([1, 2])}
          >
            <Scene particleScale={perf.particleScale} />
          </PerformanceMonitor>
        </Canvas>
      </div>

      {/* Scroll runway: scrolling through this IS the camera flight. */}
      <div ref={containerRef} style={{ height: `${TOTAL_VH}vh` }} />

      <Overlay />
      <Nav withSound />
      <ChapterRail />
      {!perf.mobile && <Cursor />}

      {/* After the journey ends, the page grounds out in a real footer.
          z-40 lets it slide over the pinned finale copy (z-30). */}
      <div className="relative z-40">
        <Footer />
      </div>
    </div>
  );
}
