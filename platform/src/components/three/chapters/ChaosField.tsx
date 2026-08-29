"use client";

/**
 * Chapter 1 — THE CHAOS. A field of hundreds of drifting glass message cards
 * (one shared texture, instanced) plus a handful of crisp readable feedback
 * snippets. As the scroll continues everything drifts toward the capture
 * funnel's mouth and shrinks into the stream.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { CHAOS_SNIPPETS, CHAPTERS } from "@/components/marketing/copy";
import { journeyStore } from "@/components/marketing/journeyState";
import { makeGlassCardTexture, makeSnippetTexture } from "../textures";
import { hash11, hash3, segmentProgress, easeInOutCubic, lerp } from "../timeline";
import { useChapterVisible, useJourneyClock } from "../hooks";

const RANGE = CHAPTERS.find((c) => c.id === "chaos")!.range;
/** Convergence begins late in the chaos chapter and completes inside capture. */
const CONVERGE: [number, number] = [0.175, 0.3];

const CENTER = new THREE.Vector3(0, 0, -45);
const FOCUS = new THREE.Vector3(0, 0, -80); // capture funnel mouth

const _m = new THREE.Matrix4();
const _pos = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _scl = new THREE.Vector3();
const _euler = new THREE.Euler();

export function ChaosField({ cardCount = 240 }: { cardCount?: number }) {
  const group = useRef<THREE.Group>(null);
  const instRef = useRef<THREE.InstancedMesh>(null);
  const snippetRefs = useRef<(THREE.Mesh | null)[]>([]);
  const clock = useJourneyClock();
  useChapterVisible(group, [RANGE[0] - 0.02, CONVERGE[1]], 0.05);

  const { cardGeo, cardMat } = useMemo(() => {
    const geo = new THREE.PlaneGeometry(2.2, 1.375);
    const mat = new THREE.MeshBasicMaterial({
      map: makeGlassCardTexture(),
      transparent: true,
      opacity: 0.85,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    return { cardGeo: geo, cardMat: mat };
  }, []);

  /** start x/y/z, drift phase, spin speed, scale, stagger. */
  const data = useMemo(() => {
    const d = new Float32Array(cardCount * 7);
    for (let i = 0; i < cardCount; i++) {
      const o = i * 7;
      const [rx, ry, rz] = hash3(i * 3.7 + 11);
      d[o] = (rx * 2 - 1) * 24;
      d[o + 1] = (ry * 2 - 1) * 13;
      d[o + 2] = (rz * 2 - 1) * 22;
      d[o + 3] = hash11(i + 500) * Math.PI * 2; // drift phase
      d[o + 4] = 0.1 + hash11(i + 600) * 0.4; // spin speed
      d[o + 5] = 0.45 + hash11(i + 700) * 0.9; // scale
      d[o + 6] = hash11(i + 800) * 0.5; // convergence stagger
    }
    return d;
  }, [cardCount]);

  const snippets = useMemo(
    () =>
      CHAOS_SNIPPETS.map((s, i) => ({
        texture: makeSnippetTexture(s.text, s.source),
        pos: new THREE.Vector3(
          (hash11(i * 13 + 3) * 2 - 1) * 8.5,
          (hash11(i * 17 + 5) * 2 - 1) * 4.5,
          -30 - i * 4.2 - hash11(i * 19 + 7) * 2
        ),
        phase: hash11(i * 23 + 9) * Math.PI * 2,
        stagger: hash11(i * 29 + 13) * 0.4,
      })),
    []
  );

  useFrame(() => {
    const g = group.current;
    if (!g || !g.visible) return;
    const t = clock.current;
    const p = journeyStore.progress;
    const cvRaw = segmentProgress(p, CONVERGE);

    const mesh = instRef.current;
    if (mesh) {
      for (let i = 0; i < cardCount; i++) {
        const o = i * 7;
        const stagger = data[o + 6];
        const cv = easeInOutCubic(segmentProgress(cvRaw, [stagger * 0.5, 1]));
        const driftX = Math.sin(t * 0.3 + data[o + 3]) * 0.9;
        const driftY = Math.cos(t * 0.24 + data[o + 3] * 2) * 0.7;
        _pos.set(
          CENTER.x + data[o] + driftX,
          CENTER.y + data[o + 1] + driftY,
          CENTER.z + data[o + 2]
        );
        _pos.lerp(FOCUS, cv);
        _euler.set(
          Math.sin(t * data[o + 4] + i) * 0.4 * (1 - cv),
          Math.cos(t * data[o + 4] * 0.8 + i) * 0.5 * (1 - cv),
          Math.sin(t * 0.2 + data[o + 3]) * 0.2
        );
        _quat.setFromEuler(_euler);
        const s = data[o + 5] * (1 - cv * 0.92);
        _scl.setScalar(Math.max(s, 0.001));
        _m.compose(_pos, _quat, _scl);
        mesh.setMatrixAt(i, _m);
      }
      mesh.instanceMatrix.needsUpdate = true;
    }

    for (let i = 0; i < snippets.length; i++) {
      const m = snippetRefs.current[i];
      if (!m) continue;
      const sn = snippets[i];
      const cv = easeInOutCubic(segmentProgress(cvRaw, [sn.stagger * 0.4, 0.95]));
      _pos.set(
        sn.pos.x + Math.sin(t * 0.35 + sn.phase) * 0.5,
        sn.pos.y + Math.cos(t * 0.3 + sn.phase * 1.7) * 0.4,
        sn.pos.z
      );
      _pos.lerp(FOCUS, cv);
      m.position.copy(_pos);
      m.rotation.set(
        Math.sin(t * 0.2 + sn.phase) * 0.08,
        Math.cos(t * 0.17 + sn.phase) * 0.12,
        Math.sin(t * 0.13 + sn.phase) * 0.04
      );
      const s = lerp(1, 0.05, cv);
      m.scale.setScalar(s);
      const mat = m.material as THREE.MeshBasicMaterial;
      mat.opacity = 0.96 * (1 - cv * 0.9);
    }
  });

  return (
    <group ref={group}>
      <instancedMesh
        ref={instRef}
        args={[cardGeo, cardMat, cardCount]}
        frustumCulled={false}
      />
      {snippets.map((sn, i) => (
        <mesh
          key={i}
          ref={(el) => {
            snippetRefs.current[i] = el;
          }}
          position={sn.pos}
          frustumCulled={false}
        >
          <planeGeometry args={[3.4, 1.9125]} />
          <meshBasicMaterial
            map={sn.texture}
            transparent
            opacity={0.96}
            side={THREE.DoubleSide}
            depthWrite={false}
          />
        </mesh>
      ))}
    </group>
  );
}
