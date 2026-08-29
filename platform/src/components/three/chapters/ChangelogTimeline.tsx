"use client";

/**
 * Chapter 8 — CHANGELOG. A glowing milestone timeline the camera travels
 * along: five version markers with labels; the nearest one flares as the
 * camera passes, and passed versions recede behind.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { CHAPTERS, CHANGELOG_MILESTONES } from "@/components/marketing/copy";
import { PALETTE } from "../palette";
import { makeGlowTexture, makeMilestoneTexture } from "../textures";
import { hash11, clamp01 } from "../timeline";
import { useChapterVisible, useJourneyClock } from "../hooks";

const RANGE = CHAPTERS.find((c) => c.id === "changelog")!.range;

const LINE_X = -1.4;
const LINE_Y = -0.6;
const FIRST_Z = -482;
const SPACING = 16;

export function ChangelogTimeline() {
  const group = useRef<THREE.Group>(null);
  const nodeRefs = useRef<(THREE.Sprite | null)[]>([]);
  const labelRefs = useRef<(THREE.Mesh | null)[]>([]);
  const camera = useThree((s) => s.camera);
  const clock = useJourneyClock();
  useChapterVisible(group, RANGE, 0.08);

  const milestones = useMemo(
    () =>
      CHANGELOG_MILESTONES.map((m, i) => ({
        ...m,
        texture: makeMilestoneTexture(m.version, m.title),
        z: FIRST_Z - i * SPACING,
        phase: hash11(i + 87) * Math.PI * 2,
      })),
    []
  );

  const lineLength = SPACING * (CHANGELOG_MILESTONES.length - 1) + 24;
  const midZ = FIRST_Z - (SPACING * (CHANGELOG_MILESTONES.length - 1)) / 2;

  const { nodeTex, tickGeo, tickMat } = useMemo(() => {
    const tex = makeGlowTexture(PALETTE.accentSoft);
    // Sparse ticks drifting along the line.
    const count = 40;
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      positions[i * 3] = LINE_X;
      positions[i * 3 + 1] = LINE_Y;
      positions[i * 3 + 2] = FIRST_Z + 10 - hash11(i + 33) * (lineLength + 10);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.PointsMaterial({
      size: 0.3,
      sizeAttenuation: true,
      map: makeGlowTexture("#ffffff", 64),
      color: new THREE.Color(PALETTE.aurora),
      transparent: true,
      opacity: 0.7,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      fog: false,
    });
    return { nodeTex: tex, tickGeo: geo, tickMat: mat };
  }, [lineLength]);

  useFrame((_, delta) => {
    const g = group.current;
    if (!g || !g.visible) return;
    const t = clock.current;
    const camZ = camera.position.z;
    const dt = Math.min(delta, 0.05);

    // Ticks march slowly toward the future.
    const attr = tickGeo.getAttribute("position") as THREE.BufferAttribute;
    const arr = attr.array as Float32Array;
    for (let i = 0; i < arr.length / 3; i++) {
      let z = arr[i * 3 + 2] - dt * 1.4; // steady drift
      if (z < FIRST_Z - lineLength) z = FIRST_Z + 10;
      arr[i * 3 + 2] = z;
    }
    attr.needsUpdate = true;

    for (let i = 0; i < milestones.length; i++) {
      const m = milestones[i];
      // 1 when the camera is right beside this milestone.
      const near = clamp01(1 - Math.abs(camZ - m.z) / 9);
      const passed = camZ < m.z ? 1 : 0;
      const node = nodeRefs.current[i];
      if (node) {
        const s = 1.6 + near * 2.6 + Math.sin(t * 1.2 + m.phase) * 0.15;
        node.scale.set(s, s, 1);
        (node.material as THREE.SpriteMaterial).opacity =
          0.35 + near * 0.6 - passed * 0.15;
      }
      const label = labelRefs.current[i];
      if (label) {
        label.position.y = LINE_Y + 1.9 + near * 0.25;
        const mat = label.material as THREE.MeshBasicMaterial;
        mat.opacity = 0.4 + near * 0.6 - passed * 0.2;
        const scale = 0.9 + near * 0.25;
        label.scale.setScalar(scale);
      }
    }
  });

  return (
    <group ref={group}>
      {/* The timeline itself: a thin luminous rail along the flight path. */}
      <mesh position={[LINE_X, LINE_Y, midZ]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.03, 0.03, lineLength, 6]} />
        <meshBasicMaterial
          color={PALETTE.accentSoft}
          transparent
          opacity={0.6}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          fog={false}
        />
      </mesh>
      <points geometry={tickGeo} material={tickMat} frustumCulled={false} />

      {milestones.map((m, i) => (
        <group key={m.version} position={[LINE_X, LINE_Y, m.z]}>
          <sprite
            ref={(el) => {
              nodeRefs.current[i] = el;
            }}
          >
            <spriteMaterial
              map={nodeTex}
              transparent
              opacity={0.4}
              blending={THREE.AdditiveBlending}
              depthWrite={false}
              fog={false}
            />
          </sprite>
          <mesh position={[0, 0, 0]}>
            <sphereGeometry args={[0.16, 16, 16]} />
            <meshBasicMaterial color={PALETTE.ink} fog={false} />
          </mesh>
          <mesh
            ref={(el) => {
              labelRefs.current[i] = el;
            }}
            position={[0.4, 1.3, 0]}
          >
            <planeGeometry args={[3.4, 1.36]} />
            <meshBasicMaterial
              map={m.texture}
              transparent
              opacity={0.5}
              side={THREE.DoubleSide}
              depthWrite={false}
            />
          </mesh>
        </group>
      ))}
    </group>
  );
}
