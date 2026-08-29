"use client";

/**
 * Chapter 7 — SURVEYS. The cosmos calms (Scene raises the fog; the starfield
 * dims itself): elegant glass survey panels float past — NPS, multiple
 * choice, open text — with exactly one question lit at a time.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { CHAPTERS, SURVEY_PANELS } from "@/components/marketing/copy";
import { journeyStore } from "@/components/marketing/journeyState";
import { PALETTE } from "../palette";
import { makeGlowTexture, makeSurveyPanelTexture } from "../textures";
import { hash11, segmentProgress, clamp01, lerp } from "../timeline";
import { useChapterVisible, useJourneyClock } from "../hooks";

const RANGE = CHAPTERS.find((c) => c.id === "surveys")!.range;

const PANEL_POS: [number, number, number][] = [
  [-1.6, 0.2, -430],
  [1.7, -0.1, -442],
  [-1.2, 0.3, -454],
];

export function SurveySpace({ moteCount = 110 }: { moteCount?: number }) {
  const group = useRef<THREE.Group>(null);
  const panelRefs = useRef<(THREE.Group | null)[]>([]);
  const glowRefs = useRef<(THREE.Sprite | null)[]>([]);
  const clock = useJourneyClock();
  useChapterVisible(group, RANGE, 0.08);

  const panels = useMemo(
    () =>
      SURVEY_PANELS.map((p, i) => ({
        texture: makeSurveyPanelTexture(p),
        pos: PANEL_POS[i],
        phase: hash11(i + 55) * Math.PI * 2,
      })),
    []
  );

  const { moteGeo, moteMat, glowTex } = useMemo(() => {
    const positions = new Float32Array(moteCount * 3);
    for (let i = 0; i < moteCount; i++) {
      positions[i * 3] = (hash11(i + 15) * 2 - 1) * 16;
      positions[i * 3 + 1] = (hash11(i + 115) * 2 - 1) * 9;
      positions[i * 3 + 2] = -420 - hash11(i + 215) * 44;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.PointsMaterial({
      size: 0.18,
      sizeAttenuation: true,
      map: makeGlowTexture(PALETTE.aurora, 64),
      color: new THREE.Color(PALETTE.aurora),
      transparent: true,
      opacity: 0.4,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      fog: false,
    });
    return { moteGeo: geo, moteMat: mat, glowTex: makeGlowTexture(PALETTE.aurora) };
  }, [moteCount]);

  useFrame(() => {
    const g = group.current;
    if (!g || !g.visible) return;
    const t = clock.current;
    const local = segmentProgress(journeyStore.progress, RANGE);

    for (let i = 0; i < panels.length; i++) {
      const panel = panelRefs.current[i];
      const glow = glowRefs.current[i];
      const spec = panels[i];
      if (!panel) continue;
      // One lit question at a time: window i of 3 across the chapter.
      const mid = (i + 0.5) / panels.length;
      const lit = clamp01(1 - Math.abs(local - mid) * panels.length * 1.6);
      panel.position.set(
        spec.pos[0] + Math.sin(t * 0.3 + spec.phase) * 0.15,
        spec.pos[1] + Math.sin(t * 0.4 + spec.phase * 2) * 0.12,
        spec.pos[2]
      );
      panel.rotation.y = Math.sin(t * 0.2 + spec.phase) * 0.05;
      const scale = lerp(0.92, 1, lit);
      panel.scale.setScalar(scale);
      const mesh = panel.children[0] as THREE.Mesh;
      const mat = mesh.material as THREE.MeshBasicMaterial;
      mat.opacity = lerp(0.3, 1, lit);
      // Dim the artwork itself when unlit (color tints toward gray).
      const v = lerp(0.45, 1, lit);
      mat.color.setRGB(v, v, v);
      if (glow) {
        glow.position.copy(panel.position);
        const gs = 9 * lit;
        glow.scale.set(gs, gs, 1);
        (glow.material as THREE.SpriteMaterial).opacity = lit * 0.22;
      }
    }
  });

  return (
    <group ref={group}>
      <points geometry={moteGeo} material={moteMat} frustumCulled={false} />
      {panels.map((panel, i) => (
        <group key={i}>
          <sprite
            ref={(el) => {
              glowRefs.current[i] = el;
            }}
            position={panel.pos}
            scale={[0, 0, 1]}
          >
            <spriteMaterial
              map={glowTex}
              transparent
              opacity={0}
              blending={THREE.AdditiveBlending}
              depthWrite={false}
              fog={false}
            />
          </sprite>
          <group
            ref={(el) => {
              panelRefs.current[i] = el;
            }}
            position={panel.pos}
          >
            <mesh>
              <planeGeometry args={[4.8, 3.2]} />
              <meshBasicMaterial
                map={panel.texture}
                transparent
                opacity={0.4}
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
