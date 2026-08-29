/**
 * Tiny imperative store shared between the DOM overlay, the 3D scene and the
 * audio engine. Values mutate at scroll speed — components read them inside
 * rAF/useFrame loops or subscribe for change callbacks, so React never
 * re-renders on scroll.
 *
 * SSR-safe: no window/document access at module level.
 */
import { chapterIndexAt } from "@/components/three/timeline";

type Listener = (progress: number) => void;
type ChapterListener = (chapterIndex: number) => void;

class JourneyStore {
  /** Global scroll progress 0..1 across the whole journey. */
  progress = 0;
  /** Lenis scroll velocity (px/frame-ish), for subtle speed effects. */
  velocity = 0;
  /** Normalized pointer, -1..1 both axes (0,0 = center). */
  pointerX = 0;
  pointerY = 0;
  /** Current chapter index into CHAPTERS. */
  chapterIndex = 0;
  /** Whether the tab is visible (rendering paused otherwise). */
  visible = true;
  /** Quality scale 0..1 applied to particle budgets (set once at boot). */
  particleScale = 1;
  /** True on coarse-pointer / narrow viewports. */
  isMobile = false;

  private listeners = new Set<Listener>();
  private chapterListeners = new Set<ChapterListener>();
  private scrollRequest: ((p: number) => void) | null = null;

  setProgress(p: number, velocity = 0) {
    this.progress = p;
    this.velocity = velocity;
    const idx = chapterIndexAt(p);
    if (idx !== this.chapterIndex) {
      this.chapterIndex = idx;
      this.chapterListeners.forEach((fn) => fn(idx));
    }
    this.listeners.forEach((fn) => fn(p));
  }

  setPointer(x: number, y: number) {
    this.pointerX = x;
    this.pointerY = y;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.progress);
    return () => this.listeners.delete(fn);
  }

  onChapterChange(fn: ChapterListener): () => void {
    this.chapterListeners.add(fn);
    return () => this.chapterListeners.delete(fn);
  }

  /** Wired by Journey to lenis.scrollTo — lets the chapter rail navigate. */
  bindScrollTo(fn: ((p: number) => void) | null) {
    this.scrollRequest = fn;
  }

  requestScrollTo(progress: number) {
    this.scrollRequest?.(progress);
  }
}

export const journeyStore = new JourneyStore();
