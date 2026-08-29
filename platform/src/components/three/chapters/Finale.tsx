"use client";

/**
 * Final chapter — the camera pulls back and the opening chaos has become a
 * calm, organized ecosystem: the intelligence core with tidy orbital rings of
 * feedback cards circling it, plus a few named feature cards in slow orbit.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { CHAPTERS, ROADMAP_STAGES } from "@/components/marketing/copy";
import { PALETTE } from "../palette";
import { makeGlassCardTexture, makeMiniCardTexture } from "../textures";
import { hash11 } from "../timeline";
import { useChapterVisible, useJourneyClock } from "../hooks";
import { CoreStar } from "./CoreStar";

const RANGE = CHAPTERS.find((c) => c.id === "finale")!.range;
const CENTER: [number, number, number] = [0, 0, -612];

const RINGS = [
  { radius: 6.2, tilt: 0.35, speed: 0.05, count: 26 },
  { radius: 8.6, tilt: -0.22, speed: 0.036, count: 34 },
  { radius: 11.2, tilt: 0.12, speed: 0.026, count: 42 },
];

const _m = new THREE.Matrix4();
const _pos = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _scl = new THREE.Vector3();
const _euler = new THREE.Euler();

export function Finale({ fragmentCount = 500 }: { fragmentCount?: number }) {
  const group = useRef<THREE.Group>(null);
  const instRef = useRef<THREE.InstancedMesh>(null);
  const namedRefs = useRef<(THREE.Group | null)[]>([]);
  const clock = useJourneyClock();
  useChapterVisible(group, RANGE, 0.08);

  const totalCards = RINGS.reduce((sum, r) => sum + r.count, 0);

  const { cardGeo, cardMat, cardParams } = useMemo(() => {
    const geo = new THREE.PlaneGeometry(1.15, 0.72);
    const mat = new THREE.MeshBasicMaterial({
      map: makeGlassCardTexture(),
      transparent: true,
      opacity: 0.85,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    // ring index, angle0, y-jitter, scale
    const params = new Float32Array(totalCards * 4);
    let idx = 0;
    RINGS.forEach((ring, r) => {
      for (let i = 0; i < ring.count; i++) {
        const o = idx * 4;
        params[o] = r;
        params[o + 1] = (i / ring.count) * Math.PI * 2;
        params[o + 2] = (hash11(idx + 91) * 2 - 1) * 0.35;
        params[o + 3] = 0.7 + hash11(idx + 191) * 0.5;
        idx++;
      }
    });
    return { cardGeo: geo, cardMat: mat, cardParams: params };
  }, [totalCards]);

  const namedCards = useMemo(
    () =>
      ROADMAP_STAGES.flatMap((s) => s.cards.slice(0, 1).map((name) => ({ name, hex: s.hex }))).map(
        (c, i) => ({
          texture: makeMiniCardTexture(c.name, c.hex),
          angle0: (i / 4) * Math.PI * 2 + 0.4,
          radius: 7.4 + (i % 2) * 2.2,
          tilt: 0.1 + (i % 3) * 0.1,
        })
      ),
    []
  );

  useFrame(() => {
    const g = group.current;
    if (!g || !g.visible) return;
    const t = clock.current;

    const mesh = instRef.current;
    if (mesh) {
      for (let i = 0; i < totalCards; i++) {
        const o = i * 4;
        const ring = RINGS[cardParams[o]];
        const angle = cardParams[o + 1] + t * ring.speed;
        const x = Math.cos(angle) * ring.radius;
        const z = Math.sin(angle) * ring.radius;
        const y = Math.sin(angle) * Math.sin(ring.tilt) * ring.radius * 0.4 + cardParams[o + 2];
        _pos.set(CENTER[0] + x, CENTER[1] + y, CENTER[2] + z);
        // Cards face outward along their orbit, gently banked.
        _euler.set(0, -angle + Math.PI / 2, ring.tilt * 0.5);
        _quat.setFromEuler(_euler);
        _scl.setScalar(cardParams[o + 3]);
        _m.compose(_pos, _quat, _scl);
        mesh.setMatrixAt(i, _m);
      }
      mesh.instanceMatrix.needsUpdate = true;
    }

    for (let i = 0; i < namedCards.length; i++) {
      const card = namedRefs.current[i];
      if (!card) continue;
      const spec = namedCards[i];
      const angle = spec.angle0 + t * 0.045;
      card.position.set(
        CENTER[0] + Math.cos(angle) * spec.radius,
        CENTER[1] + Math.sin(angle * 1.3) * 1.2,
        CENTER[2] + Math.sin(angle) * spec.radius
      );
      card.rotation.y = -angle + Math.PI / 2;
    }
  });

  return (
    <group ref={group}>
      <CoreStar position={CENTER} scale={1.15} fragmentCount={fragmentCount} calm seed={7} />

      {/* Orbit guide rings — the chaos is now structure. */}
      {RINGS.map((ring, i) => (
        <mesh
          key={i}
          position={CENTER}
          rotation={[Math.PI / 2 + ring.tilt * 0.4, 0, ring.tilt]}
        >
          <torusGeometry args={[ring.radius, 0.012, 6, 128]} />
          <meshBasicMaterial
            color={i === 1 ? PALETTE.aurora : PALETTE.accentSoft}
            transparent
            opacity={0.28}
            blending={THREE.AdditiveBlending}
            depthWrite={false}
            fog={false}
          />
        </mesh>
      ))}

      <instancedMesh
        ref={instRef}
        args={[cardGeo, cardMat, totalCards]}
        frustumCulled={false}
      />

      {namedCards.map((card, i) => (
        <group
          key={i}
          ref={(el) => {
            namedRefs.current[i] = el;
          }}
        >
          <mesh>
            <planeGeometry args={[2.6, 0.87]} />
            <meshBasicMaterial
              map={card.texture}
              transparent
              opacity={0.95}
              side={THREE.DoubleSide}
              depthWrite={false}
            />
          </mesh>
        </group>
      ))}
    </group>
  );
}
