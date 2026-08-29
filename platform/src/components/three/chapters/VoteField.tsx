"use client";

/**
 * Chapter 5 — VOTING. Glowing feature cards whose vote counters tick upward
 * as the camera flies past, with ripple rings and tiny particle bursts while
 * they charge. Counters are redrawn onto per-card canvas textures (throttled).
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { CHAPTERS, VOTE_CARDS } from "@/components/marketing/copy";
import { journeyStore } from "@/components/marketing/journeyState";
import { PALETTE } from "../palette";
import { drawVoteCard, makeGlassCardTexture, makeGlowTexture } from "../textures";
import { hash11, segmentProgress, easeOutExpo, clamp01 } from "../timeline";
import { useChapterVisible, useJourneyClock } from "../hooks";

const RANGE = CHAPTERS.find((c) => c.id === "voting")!.range;
const BURSTS_PER_CARD = 26;

const _m = new THREE.Matrix4();
const _pos = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _scl = new THREE.Vector3();
const _euler = new THREE.Euler();

interface LiveCard {
  name: string;
  target: number;
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  texture: THREE.CanvasTexture;
  position: THREE.Vector3;
  window: [number, number];
  lastDrawn: number;
  lastDrawTime: number;
  phase: number;
  burstDirs: Float32Array;
  burstGeo: THREE.BufferGeometry;
}

export function VoteField({ ambientCards = 70 }: { ambientCards?: number }) {
  const group = useRef<THREE.Group>(null);
  const ringRefs = useRef<(THREE.Mesh | null)[]>([]);
  const ambientRef = useRef<THREE.InstancedMesh>(null);
  const clock = useJourneyClock();
  useChapterVisible(group, RANGE, 0.08);

  const cards = useMemo<LiveCard[]>(
    () =>
      VOTE_CARDS.map((c, i) => {
        const canvas = document.createElement("canvas");
        canvas.width = 640;
        canvas.height = 288;
        const ctx = canvas.getContext("2d")!;
        drawVoteCard(ctx, 640, 288, c.name, 0, 0);
        const texture = new THREE.CanvasTexture(canvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.anisotropy = 4;
        const dirs = new Float32Array(BURSTS_PER_CARD * 4); // dirx,diry,speed,phase
        for (let b = 0; b < BURSTS_PER_CARD; b++) {
          const a = (b / BURSTS_PER_CARD) * Math.PI * 2 + hash11(b + i * 97) * 0.5;
          dirs[b * 4] = Math.cos(a);
          dirs[b * 4 + 1] = Math.sin(a);
          dirs[b * 4 + 2] = 0.45 + hash11(b + i * 131) * 0.5;
          dirs[b * 4 + 3] = hash11(b + i * 173);
        }
        const burstGeo = new THREE.BufferGeometry();
        burstGeo.setAttribute(
          "position",
          new THREE.BufferAttribute(new Float32Array(BURSTS_PER_CARD * 3), 3)
        );
        burstGeo.setAttribute(
          "color",
          new THREE.BufferAttribute(new Float32Array(BURSTS_PER_CARD * 3), 3)
        );
        return {
          name: c.name,
          target: c.votes,
          canvas,
          ctx,
          texture,
          position: new THREE.Vector3(
            i % 2 === 0 ? -3.2 : 3.4,
            (i - 1) * 1.4,
            -248 - i * 9
          ),
          window: [0.1 + i * 0.14, 0.62 + i * 0.1] as [number, number],
          lastDrawn: -1,
          lastDrawTime: 0,
          phase: hash11(i + 313) * Math.PI * 2,
          burstDirs: dirs,
          burstGeo,
        };
      }),
    []
  );

  const { glassGeo, glassMat, ambientData, burstMat } = useMemo(() => {
    const geo = new THREE.PlaneGeometry(1.9, 1.2);
    const mat = new THREE.MeshBasicMaterial({
      map: makeGlassCardTexture(),
      transparent: true,
      opacity: 0.5,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const data = new Float32Array(ambientCards * 5);
    for (let i = 0; i < ambientCards; i++) {
      const o = i * 5;
      data[o] = (hash11(i + 21) * 2 - 1) * 20;
      data[o + 1] = (hash11(i + 121) * 2 - 1) * 10;
      data[o + 2] = -238 - hash11(i + 221) * 48;
      data[o + 3] = hash11(i + 321) * Math.PI * 2;
      data[o + 4] = 0.35 + hash11(i + 421) * 0.6;
    }
    const bMat = new THREE.PointsMaterial({
      size: 0.28,
      sizeAttenuation: true,
      map: makeGlowTexture(PALETTE.accentSoft, 64),
      vertexColors: true,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      fog: false,
    });
    return { glassGeo: geo, glassMat: mat, ambientData: data, burstMat: bMat };
  }, [ambientCards]);

  useFrame(() => {
    const g = group.current;
    if (!g || !g.visible) return;
    const t = clock.current;
    const local = segmentProgress(journeyStore.progress, RANGE);
    const now = performance.now();

    for (let i = 0; i < cards.length; i++) {
      const card = cards[i];
      const w = card.window;
      const raw = segmentProgress(local, w);
      const count = Math.round(card.target * easeOutExpo(raw));
      const charging = raw > 0 && raw < 1 ? 1 : 0;
      // Redraw the canvas only when the number changes (throttled to ~16/s).
      if (count !== card.lastDrawn && now - card.lastDrawTime > 60) {
        drawVoteCard(card.ctx, 640, 288, card.name, count, charging * (0.4 + 0.6 * Math.sin(t * 6) ** 2));
        card.texture.needsUpdate = true;
        card.lastDrawn = count;
        card.lastDrawTime = now;
      }

      const ring = ringRefs.current[i];
      if (ring) {
        const u = (t * 0.9 + card.phase) % 1;
        const active = charging || raw >= 1 ? 1 : 0;
        const settleFade = raw >= 1 ? 0.35 : 1;
        ring.position.copy(card.position);
        ring.scale.setScalar(1 + u * 1.6);
        (ring.material as THREE.MeshBasicMaterial).opacity =
          active * (1 - u) * 0.5 * settleFade;
      }

      {
        const posAttr = card.burstGeo.getAttribute("position") as THREE.BufferAttribute;
        const colAttr = card.burstGeo.getAttribute("color") as THREE.BufferAttribute;
        const pArr = posAttr.array as Float32Array;
        const cArr = colAttr.array as Float32Array;
        const energy = charging ? 1 : raw >= 1 ? 0.25 : 0;
        for (let b = 0; b < BURSTS_PER_CARD; b++) {
          const o4 = b * 4;
          const u = (t * card.burstDirs[o4 + 2] + card.burstDirs[o4 + 3]) % 1;
          const r = 1.4 + u * 2.2;
          const o3 = b * 3;
          pArr[o3] = card.position.x + card.burstDirs[o4] * r;
          pArr[o3 + 1] = card.position.y + card.burstDirs[o4 + 1] * r * 0.7 + u * 0.6;
          pArr[o3 + 2] = card.position.z;
          const bright = clamp01(Math.sin(u * Math.PI)) * energy;
          cArr[o3] = 0.62 * bright;
          cArr[o3 + 1] = 0.57 * bright;
          cArr[o3 + 2] = 1.0 * bright;
        }
        posAttr.needsUpdate = true;
        colAttr.needsUpdate = true;
      }
    }

    const ambient = ambientRef.current;
    if (ambient) {
      for (let i = 0; i < ambientCards; i++) {
        const o = i * 5;
        _pos.set(
          ambientData[o] + Math.sin(t * 0.25 + ambientData[o + 3]) * 0.8,
          ambientData[o + 1] + Math.cos(t * 0.2 + ambientData[o + 3] * 1.6) * 0.6,
          ambientData[o + 2]
        );
        _euler.set(
          Math.sin(t * 0.15 + i) * 0.3,
          Math.cos(t * 0.12 + i) * 0.35,
          0
        );
        _quat.setFromEuler(_euler);
        _scl.setScalar(ambientData[o + 4]);
        _m.compose(_pos, _quat, _scl);
        ambient.setMatrixAt(i, _m);
      }
      ambient.instanceMatrix.needsUpdate = true;
    }
  });

  return (
    <group ref={group}>
      <instancedMesh
        ref={ambientRef}
        args={[glassGeo, glassMat, ambientCards]}
        frustumCulled={false}
      />
      {cards.map((card, i) => (
        <group key={card.name}>
          <mesh position={card.position}>
            <planeGeometry args={[4.2, 1.89]} />
            <meshBasicMaterial
              map={card.texture}
              transparent
              opacity={0.98}
              side={THREE.DoubleSide}
              depthWrite={false}
            />
          </mesh>
          <mesh
            ref={(el) => {
              ringRefs.current[i] = el;
            }}
            position={card.position}
          >
            <ringGeometry args={[1.35, 1.42, 48]} />
            <meshBasicMaterial
              color={PALETTE.accentSoft}
              transparent
              opacity={0}
              blending={THREE.AdditiveBlending}
              depthWrite={false}
              side={THREE.DoubleSide}
              fog={false}
            />
          </mesh>
          <points geometry={card.burstGeo} material={burstMat} frustumCulled={false} />
        </group>
      ))}
    </group>
  );
}
