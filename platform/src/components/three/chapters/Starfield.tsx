"use client";

/**
 * Persistent star/dust field spanning the entire camera tunnel, plus large
 * additive nebula sprites for depth haze. Built once; the survey chapter
 * calms it down by fading opacity.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { PALETTE } from "../palette";
import { makeGlowTexture, makeNebulaTexture } from "../textures";
import { hash11, segmentProgress, fadeWindow, lerp } from "../timeline";
import { CHAPTERS } from "@/components/marketing/copy";
import { journeyStore } from "@/components/marketing/journeyState";

const SURVEY_RANGE = CHAPTERS.find((c) => c.id === "surveys")!.range;

export function Starfield({ count = 2400 }: { count?: number }) {
  const matRef = useRef<THREE.PointsMaterial>(null);

  const { geometry, material } = useMemo(() => {
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const cAccent = new THREE.Color(PALETTE.accentSoft);
    const cAurora = new THREE.Color(PALETTE.aurora);
    const cInk = new THREE.Color(PALETTE.ink);
    for (let i = 0; i < count; i++) {
      const o = i * 3;
      positions[o] = (hash11(i) * 2 - 1) * 70;
      positions[o + 1] = (hash11(i + 9000) * 2 - 1) * 45;
      positions[o + 2] = 30 - hash11(i + 18000) * 720; // z 30 .. -690
      const roll = hash11(i + 27000);
      const c = roll < 0.55 ? cInk : roll < 0.8 ? cAccent : cAurora;
      const dim = 0.35 + hash11(i + 36000) * 0.65;
      colors[o] = c.r * dim;
      colors[o + 1] = c.g * dim;
      colors[o + 2] = c.b * dim;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    const mat = new THREE.PointsMaterial({
      size: 0.32,
      sizeAttenuation: true,
      map: makeGlowTexture("#ffffff", 64),
      vertexColors: true,
      transparent: true,
      opacity: 0.85,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    return { geometry: geo, material: mat };
  }, [count]);

  useFrame(() => {
    const mat = matRef.current;
    if (!mat) return;
    // The cosmos calms during the surveys chapter.
    const calm = fadeWindow(segmentProgress(journeyStore.progress, SURVEY_RANGE), 0.25, 0.25);
    mat.opacity = lerp(0.85, 0.3, calm);
  });

  return (
    <points frustumCulled={false}>
      <primitive object={geometry} attach="geometry" />
      <primitive object={material} attach="material" ref={matRef} />
    </points>
  );
}

/** Sparse huge additive blobs placed along the journey for parallax depth. */
export function Nebula() {
  const sprites = useMemo(() => {
    const colors = [PALETTE.accent, PALETTE.aurora, PALETTE.accentStrong, PALETTE.ember];
    const maps = colors.map((c) => makeNebulaTexture(c));
    const items: { pos: [number, number, number]; scale: number; map: THREE.Texture; opacity: number }[] = [];
    for (let i = 0; i < 16; i++) {
      const colorIdx = i % 4 === 3 && i > 8 ? 3 : i % 3; // ember only deep in
      items.push({
        pos: [
          (hash11(i + 41) * 2 - 1) * 42,
          (hash11(i + 141) * 2 - 1) * 22,
          -20 - i * 42 - hash11(i + 241) * 20,
        ],
        scale: 34 + hash11(i + 341) * 40,
        map: maps[colorIdx],
        opacity: 0.16 + hash11(i + 441) * 0.14,
      });
    }
    return items;
  }, []);

  return (
    <group>
      {sprites.map((s, i) => (
        <sprite key={i} position={s.pos} scale={[s.scale, s.scale, 1]}>
          <spriteMaterial
            map={s.map}
            transparent
            opacity={s.opacity}
            blending={THREE.AdditiveBlending}
            depthWrite={false}
            fog={false}
          />
        </sprite>
      ))}
    </group>
  );
}
