"use client";

/**
 * Chapter 4 — PRIORITIZATION. A floating decision matrix: a luminous grid
 * plane with feature cards that physically rise or sink to their demand level
 * (votes, revenue impact, AI confidence) as the camera passes, each standing
 * on a light pillar whose height is its priority.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { CHAPTERS, MATRIX_FEATURES } from "@/components/marketing/copy";
import { journeyStore } from "@/components/marketing/journeyState";
import { PALETTE } from "../palette";
import { makeFeatureCardTexture, makeGlowTexture } from "../textures";
import { hash11, segmentProgress, easeInOutCubic, lerp } from "../timeline";
import { useChapterVisible, useJourneyClock } from "../hooks";

const RANGE = CHAPTERS.find((c) => c.id === "priorities")!.range;
const CENTER_Z = -210;
const FLOOR_Y = -4.5;

export function PriorityMatrix() {
  const group = useRef<THREE.Group>(null);
  const cardRefs = useRef<(THREE.Group | null)[]>([]);
  const beamRefs = useRef<(THREE.Mesh | null)[]>([]);
  const clock = useJourneyClock();
  useChapterVisible(group, RANGE, 0.08);

  const gridGeo = useMemo(() => {
    const size = 15;
    const step = 1.5;
    const pts: number[] = [];
    for (let i = -size; i <= size; i += step) {
      pts.push(-size, 0, CENTER_Z + i, size, 0, CENTER_Z + i);
      pts.push(i, 0, CENTER_Z - size, i, 0, CENTER_Z + size);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pts), 3));
    return geo;
  }, []);

  const cards = useMemo(
    () =>
      MATRIX_FEATURES.map((f, i) => ({
        ...f,
        texture: makeFeatureCardTexture(f.name, f.votes, f.revenue, f.confidence),
        x: (i - (MATRIX_FEATURES.length - 1) / 2) * 4.6,
        z: CENTER_Z + (i % 2 === 0 ? -3.5 : 3.5) + hash11(i + 31) * 2,
        stagger: hash11(i + 991) * 0.25,
        phase: hash11(i + 881) * Math.PI * 2,
      })),
    []
  );

  const beamTex = useMemo(() => makeGlowTexture(PALETTE.accent, 64), []);

  useFrame(() => {
    const g = group.current;
    if (!g || !g.visible) return;
    const t = clock.current;
    const local = segmentProgress(journeyStore.progress, RANGE);

    for (let i = 0; i < cards.length; i++) {
      const card = cardRefs.current[i];
      const beam = beamRefs.current[i];
      const spec = cards[i];
      if (!card) continue;
      const rise = easeInOutCubic(segmentProgress(local, [0.08 + spec.stagger, 0.62 + spec.stagger]));
      // Start mid-air, then rise to score (winners) or sink toward the floor.
      const startY = FLOOR_Y + 4 + hash11(i + 771) * 2;
      const targetY = FLOOR_Y + 1.2 + spec.score * 8.5;
      const bob = Math.sin(t * 0.8 + spec.phase) * 0.15;
      const y = lerp(startY, targetY, rise) + bob;
      card.position.set(spec.x, y, spec.z);
      card.rotation.y = Math.sin(t * 0.25 + spec.phase) * 0.06;
      if (beam) {
        const h = Math.max(y - FLOOR_Y - 1, 0.01);
        beam.position.set(spec.x, FLOOR_Y + h / 2, spec.z);
        beam.scale.set(0.5 + spec.score * 0.6, h, 1);
        (beam.material as THREE.MeshBasicMaterial).opacity =
          (0.12 + spec.score * 0.3) * rise;
      }
    }
  });

  return (
    <group ref={group}>
      <lineSegments geometry={gridGeo} position={[0, FLOOR_Y, 0]}>
        <lineBasicMaterial
          color={PALETTE.accent}
          transparent
          opacity={0.16}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          fog={false}
        />
      </lineSegments>

      {cards.map((card, i) => (
        <group key={card.name}>
          {/* Light pillar — demand made physical. */}
          <mesh
            ref={(el) => {
              beamRefs.current[i] = el;
            }}
            position={[card.x, FLOOR_Y, card.z]}
          >
            <planeGeometry args={[1, 1]} />
            <meshBasicMaterial
              map={beamTex}
              transparent
              opacity={0}
              blending={THREE.AdditiveBlending}
              depthWrite={false}
              side={THREE.DoubleSide}
              fog={false}
            />
          </mesh>
          <group
            ref={(el) => {
              cardRefs.current[i] = el;
            }}
            position={[card.x, FLOOR_Y + 4, card.z]}
          >
            <mesh>
              <planeGeometry args={[3.3, 2.0625]} />
              <meshBasicMaterial
                map={card.texture}
                transparent
                opacity={0.97}
                side={THREE.DoubleSide}
                depthWrite={false}
              />
            </mesh>
          </group>
        </group>
      ))}
    </group>
  );
}
