"use client";

/**
 * Chapter 2 — CAPTURE. A glowing intake structure: particle streams spiral
 * down converging curves into a bright throat, ringed by rotating tori, with
 * billboarded channel labels orbiting the mouth (boards, requests, widgets…).
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { CHAPTERS, INTAKE_CHANNELS } from "@/components/marketing/copy";
import { PALETTE } from "../palette";
import { makeChipTexture, makeGlowTexture } from "../textures";
import { hash11, lerp, easeInOutCubic } from "../timeline";
import { useChapterVisible, useJourneyClock } from "../hooks";

const RANGE = CHAPTERS.find((c) => c.id === "capture")!.range;

const MOUTH_Z = -80; // funnel entry
const THROAT_Z = -98; // convergence point
const BEAM_Z = -126; // stream continues toward the neural chapter
const MOUTH_RADIUS = 11;

export function CaptureFunnel({ particleCount = 900 }: { particleCount?: number }) {
  const group = useRef<THREE.Group>(null);
  const ringsRef = useRef<THREE.Group>(null);
  const chipsRef = useRef<THREE.Group>(null);
  const clock = useJourneyClock();
  useChapterVisible(group, RANGE, 0.08);

  const { geometry, material, params } = useMemo(() => {
    const positions = new Float32Array(particleCount * 3);
    const colors = new Float32Array(particleCount * 3);
    // Per particle: phase offset, speed, angle0, radius jitter.
    const prm = new Float32Array(particleCount * 4);
    const cA = new THREE.Color(PALETTE.accentSoft);
    const cB = new THREE.Color(PALETTE.aurora);
    for (let i = 0; i < particleCount; i++) {
      const o3 = i * 3;
      const o4 = i * 4;
      prm[o4] = hash11(i + 51);
      prm[o4 + 1] = 0.05 + hash11(i + 151) * 0.09;
      prm[o4 + 2] = hash11(i + 251) * Math.PI * 2;
      prm[o4 + 3] = 0.65 + hash11(i + 351) * 0.5;
      const c = hash11(i + 451) < 0.6 ? cA : cB;
      colors[o3] = c.r;
      colors[o3 + 1] = c.g;
      colors[o3 + 2] = c.b;
      positions[o3] = 0;
      positions[o3 + 1] = 0;
      positions[o3 + 2] = THROAT_Z;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    const mat = new THREE.PointsMaterial({
      size: 0.24,
      sizeAttenuation: true,
      map: makeGlowTexture("#ffffff", 64),
      vertexColors: true,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      fog: false,
    });
    return { geometry: geo, material: mat, params: prm };
  }, [particleCount]);

  const chips = useMemo(
    () =>
      INTAKE_CHANNELS.map((label, i) => ({
        texture: makeChipTexture(label, i % 2 === 0 ? PALETTE.aurora : PALETTE.accentSoft),
        angle0: (i / INTAKE_CHANNELS.length) * Math.PI * 2,
      })),
    []
  );

  const throatGlow = useMemo(() => makeGlowTexture(PALETTE.aurora), []);

  useFrame(() => {
    const g = group.current;
    if (!g || !g.visible) return;
    const t = clock.current;

    // Spiral stream: u flows 0→1 mouth→throat, then a tight beam onward.
    const posAttr = geometry.getAttribute("position") as THREE.BufferAttribute;
    const arr = posAttr.array as Float32Array;
    for (let i = 0; i < particleCount; i++) {
      const o3 = i * 3;
      const o4 = i * 4;
      const u = (t * params[o4 + 1] + params[o4]) % 1;
      if (u < 0.78) {
        const v = easeInOutCubic(u / 0.78);
        const radius = lerp(MOUTH_RADIUS * params[o4 + 3], 0.35, v);
        const angle = params[o4 + 2] + v * Math.PI * 5;
        arr[o3] = Math.cos(angle) * radius;
        arr[o3 + 1] = Math.sin(angle) * radius * 0.75;
        arr[o3 + 2] = lerp(MOUTH_Z, THROAT_Z, v);
      } else {
        const v = (u - 0.78) / 0.22;
        const angle = params[o4 + 2] + v * 6;
        arr[o3] = Math.cos(angle) * 0.3;
        arr[o3 + 1] = Math.sin(angle) * 0.3;
        arr[o3 + 2] = lerp(THROAT_Z, BEAM_Z, v);
      }
    }
    posAttr.needsUpdate = true;

    if (ringsRef.current) {
      ringsRef.current.children.forEach((ring, i) => {
        ring.rotation.z = t * (0.15 + i * 0.08) * (i % 2 === 0 ? 1 : -1);
      });
    }
    if (chipsRef.current) {
      const children = chipsRef.current.children;
      for (let i = 0; i < children.length; i++) {
        const angle = chips[i].angle0 + t * 0.12;
        children[i].position.set(
          Math.cos(angle) * 8.4,
          Math.sin(angle) * 4.6,
          MOUTH_Z - 3 + Math.sin(t * 0.4 + i) * 0.6
        );
      }
    }
  });

  return (
    <group ref={group}>
      <points frustumCulled={false}>
        <primitive object={geometry} attach="geometry" />
        <primitive object={material} attach="material" />
      </points>

      {/* Intake rings around the funnel mouth. */}
      <group ref={ringsRef}>
        {[MOUTH_RADIUS + 0.8, MOUTH_RADIUS * 0.62, MOUTH_RADIUS * 0.3].map((r, i) => (
          <mesh key={i} position={[0, 0, lerp(MOUTH_Z, THROAT_Z, i / 3)]}>
            <torusGeometry args={[r, 0.035 + i * 0.015, 8, 96]} />
            <meshBasicMaterial
              color={i === 2 ? PALETTE.aurora : PALETTE.accent}
              transparent
              opacity={0.5 - i * 0.08}
              blending={THREE.AdditiveBlending}
              depthWrite={false}
              fog={false}
            />
          </mesh>
        ))}
      </group>

      {/* Bright throat. */}
      <sprite position={[0, 0, THROAT_Z]} scale={[7, 7, 1]}>
        <spriteMaterial
          map={throatGlow}
          transparent
          opacity={0.85}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          fog={false}
        />
      </sprite>

      {/* Orbiting channel labels (sprites always face the camera). */}
      <group ref={chipsRef}>
        {chips.map((chip, i) => (
          <sprite key={i} scale={[4.6, 1.15, 1]}>
            <spriteMaterial map={chip.texture} transparent depthWrite={false} fog={false} />
          </sprite>
        ))}
      </group>
    </group>
  );
}
