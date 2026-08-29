"use client";

/**
 * Chapter 6 — ROADMAP UNIVERSE. The world opens up: a luminous tube path
 * winds through four large stage-regions in depth (NOW → IN PROGRESS →
 * PLANNED → FUTURE), each a glowing ring with a stage banner and docked
 * feature cards. Energy motes march along the path.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { CHAPTERS, ROADMAP_STAGES } from "@/components/marketing/copy";
import { PALETTE } from "../palette";
import { makeGlowTexture, makeMiniCardTexture, makeStageTexture } from "../textures";
import { hash11 } from "../timeline";
import { useChapterVisible, useJourneyClock } from "../hooks";

const RANGE = CHAPTERS.find((c) => c.id === "roadmap")!.range;

const STAGE_POS: [number, number, number][] = [
  [0, 0, -302],
  [-4, 0.5, -332],
  [4, 0.5, -362],
  [0, 0, -392],
];

const MOTE_COUNT = 46;

export function RoadmapPath() {
  const group = useRef<THREE.Group>(null);
  const ringRefs = useRef<(THREE.Mesh | null)[]>([]);
  const motesRef = useRef<THREE.Points>(null);
  const clock = useJourneyClock();
  useChapterVisible(group, RANGE, 0.08);

  const { curve, tubeGeo } = useMemo(() => {
    const pts = [
      new THREE.Vector3(0, 0, -282),
      ...STAGE_POS.map((p) => new THREE.Vector3(p[0], p[1] - 1.2, p[2])),
      new THREE.Vector3(0, 0, -408),
    ];
    const c = new THREE.CatmullRomCurve3(pts, false, "catmullrom", 0.4);
    const geo = new THREE.TubeGeometry(c, 140, 0.05, 8, false);
    return { curve: c, tubeGeo: geo };
  }, []);

  const stages = useMemo(
    () =>
      ROADMAP_STAGES.map((s, i) => ({
        ...s,
        banner: makeStageTexture(s.name, s.hex),
        halo: makeGlowTexture(s.hex),
        cardTextures: s.cards.map((name) => makeMiniCardTexture(name, s.hex)),
        pos: STAGE_POS[i],
        phase: hash11(i + 47) * Math.PI * 2,
      })),
    []
  );

  const { moteGeo, moteMat, moteOffsets } = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute(
      "position",
      new THREE.BufferAttribute(new Float32Array(MOTE_COUNT * 3), 3)
    );
    const mat = new THREE.PointsMaterial({
      size: 0.5,
      sizeAttenuation: true,
      map: makeGlowTexture(PALETTE.aurora, 64),
      color: new THREE.Color(PALETTE.aurora),
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      fog: false,
    });
    const offsets = new Float32Array(MOTE_COUNT);
    for (let i = 0; i < MOTE_COUNT; i++) offsets[i] = hash11(i + 777);
    return { moteGeo: geo, moteMat: mat, moteOffsets: offsets };
  }, []);

  const motePoint = useMemo(() => new THREE.Vector3(), []);

  useFrame(() => {
    const g = group.current;
    if (!g || !g.visible) return;
    const t = clock.current;

    for (let i = 0; i < stages.length; i++) {
      const ring = ringRefs.current[i];
      if (ring) {
        ring.rotation.z = t * 0.12 * (i % 2 === 0 ? 1 : -1);
        const pulse = 1 + Math.sin(t * 0.7 + stages[i].phase) * 0.03;
        ring.scale.setScalar(pulse);
      }
    }

    const attr = moteGeo.getAttribute("position") as THREE.BufferAttribute;
    const arr = attr.array as Float32Array;
    for (let i = 0; i < MOTE_COUNT; i++) {
      const u = (t * 0.035 + moteOffsets[i]) % 1;
      curve.getPointAt(u, motePoint);
      arr[i * 3] = motePoint.x;
      arr[i * 3 + 1] = motePoint.y + 0.06;
      arr[i * 3 + 2] = motePoint.z;
    }
    attr.needsUpdate = true;
  });

  return (
    <group ref={group}>
      {/* The luminous path itself. */}
      <mesh geometry={tubeGeo}>
        <meshBasicMaterial
          color={PALETTE.accentSoft}
          transparent
          opacity={0.55}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          fog={false}
        />
      </mesh>
      <points geometry={moteGeo} material={moteMat} frustumCulled={false} />

      {stages.map((stage, i) => (
        <group key={stage.name} position={stage.pos}>
          {/* Stage region ring. */}
          <mesh
            ref={(el) => {
              ringRefs.current[i] = el;
            }}
          >
            <torusGeometry args={[4.6, 0.045, 8, 96]} />
            <meshBasicMaterial
              color={stage.hex}
              transparent
              opacity={0.65}
              blending={THREE.AdditiveBlending}
              depthWrite={false}
              fog={false}
            />
          </mesh>
          {/* Region halo. */}
          <sprite scale={[13, 13, 1]}>
            <spriteMaterial
              map={stage.halo}
              transparent
              opacity={0.3}
              blending={THREE.AdditiveBlending}
              depthWrite={false}
              fog={false}
            />
          </sprite>
          {/* Stage banner, always facing the camera. */}
          <sprite position={[0, 3.4, 0]} scale={[8.5, 2.125, 1]}>
            <spriteMaterial map={stage.banner} transparent depthWrite={false} fog={false} />
          </sprite>
          {/* Docked feature cards. */}
          {stage.cardTextures.map((tex, j) => (
            <mesh
              key={j}
              position={[j % 2 === 0 ? -2.4 : 2.4, -1.1 - j * 0.9, 0.4]}
              rotation={[0, (j % 2 === 0 ? 1 : -1) * 0.12, 0]}
            >
              <planeGeometry args={[3, 1]} />
              <meshBasicMaterial
                map={tex}
                transparent
                opacity={0.96}
                side={THREE.DoubleSide}
                depthWrite={false}
              />
            </mesh>
          ))}
        </group>
      ))}
    </group>
  );
}
