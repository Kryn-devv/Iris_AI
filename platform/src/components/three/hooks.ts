"use client";

/**
 * Shared R3F hooks for the journey scene.
 */
import { useRef, type RefObject } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { journeyStore } from "@/components/marketing/journeyState";

/**
 * Chapters live at fixed depths along the camera path; anything far outside
 * the current scroll window is hidden entirely (cheaper than relying on
 * frustum culling for huge instanced clouds).
 */
export function useChapterVisible(
  ref: RefObject<THREE.Group | null>,
  range: [number, number],
  margin = 0.06
) {
  useFrame(() => {
    const group = ref.current;
    if (!group) return;
    const p = journeyStore.progress;
    group.visible = p >= range[0] - margin && p <= range[1] + margin;
  });
}

/** Elapsed time that keeps flowing even if the clock pauses on tab switch. */
export function useJourneyClock(): RefObject<number> {
  const time = useRef(0);
  useFrame((_, delta) => {
    // Cap delta so a hidden-tab resume doesn't teleport animations.
    time.current += Math.min(delta, 0.05);
  });
  return time;
}
