"use client";

/**
 * Chapter 3 — AI ANALYSIS (the showpiece). A neural world: glowing nodes
 * scattered like raw feedback, pulling together into clusters as the chapter
 * plays; connection lines light up while pulses of signal travel along them.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { CHAPTERS } from "@/components/marketing/copy";
import { journeyStore } from "@/components/marketing/journeyState";
import { PALETTE } from "../palette";
import { makeGlowTexture } from "../textures";
import { hash11, segmentProgress, easeInOutCubic, lerp } from "../timeline";
import { useChapterVisible, useJourneyClock } from "../hooks";

const RANGE = CHAPTERS.find((c) => c.id === "analysis")!.range;
const FORM: [number, number] = [0.335, 0.425]; // clusters coalesce over this window
const CENTER = new THREE.Vector3(0, 0, -148);

const CLUSTER_COUNT = 6;

export function NeuralField({ nodeCount = 150, pulseCount = 70 }: { nodeCount?: number; pulseCount?: number }) {
  const group = useRef<THREE.Group>(null);
  const lineRef = useRef<THREE.LineSegments>(null);
  const haloRefs = useRef<(THREE.Sprite | null)[]>([]);
  const clock = useJourneyClock();
  useChapterVisible(group, RANGE, 0.08);

  const world = useMemo(() => {
    // Cluster centers arranged loosely around the camera path.
    const clusterCenters: THREE.Vector3[] = [];
    for (let i = 0; i < CLUSTER_COUNT; i++) {
      const a = (i / CLUSTER_COUNT) * Math.PI * 2 + 0.7;
      clusterCenters.push(
        new THREE.Vector3(
          Math.cos(a) * (9 + hash11(i + 61) * 5),
          Math.sin(a * 1.3) * 5.5,
          CENTER.z - 14 + Math.sin(a) * 16 + hash11(i + 161) * 6
        )
      );
    }

    // Nodes: scattered position + cluster-target position.
    const scatter = new Float32Array(nodeCount * 3);
    const target = new Float32Array(nodeCount * 3);
    const clusterOf = new Uint8Array(nodeCount);
    for (let i = 0; i < nodeCount; i++) {
      const o = i * 3;
      const c = i % CLUSTER_COUNT;
      clusterOf[i] = c;
      scatter[o] = CENTER.x + (hash11(i + 71) * 2 - 1) * 26;
      scatter[o + 1] = CENTER.y + (hash11(i + 171) * 2 - 1) * 14;
      scatter[o + 2] = CENTER.z + (hash11(i + 271) * 2 - 1) * 26;
      const cc = clusterCenters[c];
      const r = 1.2 + hash11(i + 371) * 3.4;
      const phi = Math.acos(2 * hash11(i + 471) - 1);
      const theta = hash11(i + 571) * Math.PI * 2;
      target[o] = cc.x + r * Math.sin(phi) * Math.cos(theta);
      target[o + 1] = cc.y + r * Math.cos(phi);
      target[o + 2] = cc.z + r * Math.sin(phi) * Math.sin(theta);
    }

    // Edges: 2 nearest neighbours inside each cluster (in target space) plus
    // a chain of inter-cluster trunk links.
    const edges: [number, number][] = [];
    const byCluster: number[][] = Array.from({ length: CLUSTER_COUNT }, () => []);
    for (let i = 0; i < nodeCount; i++) byCluster[clusterOf[i]].push(i);
    const d2 = (a: number, b: number) => {
      const dx = target[a * 3] - target[b * 3];
      const dy = target[a * 3 + 1] - target[b * 3 + 1];
      const dz = target[a * 3 + 2] - target[b * 3 + 2];
      return dx * dx + dy * dy + dz * dz;
    };
    for (const members of byCluster) {
      for (const i of members) {
        let n1 = -1;
        let n2 = -1;
        let d1 = Infinity;
        let dd2 = Infinity;
        for (const j of members) {
          if (j === i) continue;
          const d = d2(i, j);
          if (d < d1) {
            dd2 = d1;
            n2 = n1;
            d1 = d;
            n1 = j;
          } else if (d < dd2) {
            dd2 = d;
            n2 = j;
          }
        }
        if (n1 >= 0 && i < n1) edges.push([i, n1]);
        if (n2 >= 0 && i < n2) edges.push([i, n2]);
      }
    }
    for (let c = 0; c < CLUSTER_COUNT; c++) {
      const a = byCluster[c][0];
      const b = byCluster[(c + 1) % CLUSTER_COUNT][1] ?? byCluster[(c + 1) % CLUSTER_COUNT][0];
      edges.push([a, b]);
      const a2 = byCluster[c][2] ?? a;
      const b2 = byCluster[(c + 2) % CLUSTER_COUNT][3] ?? byCluster[(c + 2) % CLUSTER_COUNT][0];
      if (c % 2 === 0) edges.push([a2, b2]);
    }

    // Node points geometry (positions mutate per frame).
    const nodeGeo = new THREE.BufferGeometry();
    const nodePos = new Float32Array(nodeCount * 3);
    nodePos.set(scatter);
    nodeGeo.setAttribute("position", new THREE.BufferAttribute(nodePos, 3));
    const nodeColors = new Float32Array(nodeCount * 3);
    const cA = new THREE.Color(PALETTE.accentSoft);
    const cB = new THREE.Color(PALETTE.aurora);
    const tmp = new THREE.Color();
    for (let i = 0; i < nodeCount; i++) {
      tmp.copy(clusterOf[i] % 2 === 0 ? cA : cB);
      const dim = 0.6 + hash11(i + 671) * 0.4;
      nodeColors[i * 3] = tmp.r * dim;
      nodeColors[i * 3 + 1] = tmp.g * dim;
      nodeColors[i * 3 + 2] = tmp.b * dim;
    }
    nodeGeo.setAttribute("color", new THREE.BufferAttribute(nodeColors, 3));

    // Edge line geometry (positions mutate per frame).
    const edgeGeo = new THREE.BufferGeometry();
    const edgePos = new Float32Array(edges.length * 6);
    edgeGeo.setAttribute("position", new THREE.BufferAttribute(edgePos, 3));

    // Pulses.
    const pulseGeo = new THREE.BufferGeometry();
    const pulsePos = new Float32Array(pulseCount * 3);
    const pulseColor = new Float32Array(pulseCount * 3);
    pulseGeo.setAttribute("position", new THREE.BufferAttribute(pulsePos, 3));
    pulseGeo.setAttribute("color", new THREE.BufferAttribute(pulseColor, 3));
    const pulseParams = new Float32Array(pulseCount * 3); // edge idx, speed, phase
    for (let i = 0; i < pulseCount; i++) {
      pulseParams[i * 3] = Math.floor(hash11(i + 771) * edges.length);
      pulseParams[i * 3 + 1] = 0.25 + hash11(i + 871) * 0.5;
      pulseParams[i * 3 + 2] = hash11(i + 971);
    }

    return { clusterCenters, scatter, target, edges, nodeGeo, edgeGeo, pulseGeo, pulseParams };
  }, [nodeCount, pulseCount]);

  const { nodeMat, edgeMat, pulseMat, haloTex } = useMemo(() => {
    const glow = makeGlowTexture("#ffffff", 64);
    return {
      nodeMat: new THREE.PointsMaterial({
        size: 0.85,
        sizeAttenuation: true,
        map: glow,
        vertexColors: true,
        transparent: true,
        opacity: 0.95,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        fog: false,
      }),
      edgeMat: new THREE.LineBasicMaterial({
        color: new THREE.Color(PALETTE.aurora),
        transparent: true,
        opacity: 0.1,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        fog: false,
      }),
      pulseMat: new THREE.PointsMaterial({
        size: 0.55,
        sizeAttenuation: true,
        map: glow,
        vertexColors: true,
        transparent: true,
        opacity: 1,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        fog: false,
      }),
      haloTex: makeGlowTexture(PALETTE.accent),
    };
  }, []);

  useFrame(() => {
    const g = group.current;
    if (!g || !g.visible) return;
    const t = clock.current;
    const p = journeyStore.progress;
    const form = easeInOutCubic(segmentProgress(p, FORM));

    const { scatter, target, edges, nodeGeo, edgeGeo, pulseGeo, pulseParams } = world;
    const nodeAttr = nodeGeo.getAttribute("position") as THREE.BufferAttribute;
    const nodeArr = nodeAttr.array as Float32Array;
    for (let i = 0; i < nodeCount; i++) {
      const o = i * 3;
      const jx = Math.sin(t * 0.6 + i * 1.7) * 0.18;
      const jy = Math.cos(t * 0.5 + i * 2.3) * 0.18;
      nodeArr[o] = lerp(scatter[o], target[o], form) + jx;
      nodeArr[o + 1] = lerp(scatter[o + 1], target[o + 1], form) + jy;
      nodeArr[o + 2] = lerp(scatter[o + 2], target[o + 2], form);
    }
    nodeAttr.needsUpdate = true;

    const edgeAttr = edgeGeo.getAttribute("position") as THREE.BufferAttribute;
    const edgeArr = edgeAttr.array as Float32Array;
    for (let e = 0; e < edges.length; e++) {
      const [a, b] = edges[e];
      const o = e * 6;
      edgeArr[o] = nodeArr[a * 3];
      edgeArr[o + 1] = nodeArr[a * 3 + 1];
      edgeArr[o + 2] = nodeArr[a * 3 + 2];
      edgeArr[o + 3] = nodeArr[b * 3];
      edgeArr[o + 4] = nodeArr[b * 3 + 1];
      edgeArr[o + 5] = nodeArr[b * 3 + 2];
    }
    edgeAttr.needsUpdate = true;
    edgeMat.opacity = 0.06 + form * 0.34;

    const pulseAttr = pulseGeo.getAttribute("position") as THREE.BufferAttribute;
    const pulseArr = pulseAttr.array as Float32Array;
    const pulseColAttr = pulseGeo.getAttribute("color") as THREE.BufferAttribute;
    const pulseCol = pulseColAttr.array as Float32Array;
    const energy = form;
    for (let i = 0; i < pulseCount; i++) {
      const e = pulseParams[i * 3];
      const u = (t * pulseParams[i * 3 + 1] + pulseParams[i * 3 + 2]) % 1;
      const [a, b] = edges[e as number];
      const o = i * 3;
      pulseArr[o] = lerp(nodeArr[a * 3], nodeArr[b * 3], u);
      pulseArr[o + 1] = lerp(nodeArr[a * 3 + 1], nodeArr[b * 3 + 1], u);
      pulseArr[o + 2] = lerp(nodeArr[a * 3 + 2], nodeArr[b * 3 + 2], u);
      // Bright mid-flight, dark at endpoints; additive blending hides black.
      const bright = Math.sin(u * Math.PI) * energy;
      pulseCol[o] = 0.6 * bright;
      pulseCol[o + 1] = 0.9 * bright;
      pulseCol[o + 2] = 1.0 * bright;
    }
    pulseAttr.needsUpdate = true;
    pulseColAttr.needsUpdate = true;

    for (let c = 0; c < CLUSTER_COUNT; c++) {
      const halo = haloRefs.current[c];
      if (!halo) continue;
      const s = (3.5 + Math.sin(t * 0.9 + c * 1.4) * 0.5) * (0.2 + form);
      halo.scale.set(s, s, 1);
      (halo.material as THREE.SpriteMaterial).opacity = form * 0.4;
    }
  });

  return (
    <group ref={group}>
      <points geometry={world.nodeGeo} material={nodeMat} frustumCulled={false} />
      <lineSegments ref={lineRef} geometry={world.edgeGeo} material={edgeMat} frustumCulled={false} />
      <points geometry={world.pulseGeo} material={pulseMat} frustumCulled={false} />
      {world.clusterCenters.map((cc, i) => (
        <sprite
          key={i}
          position={cc}
          ref={(el) => {
            haloRefs.current[i] = el;
          }}
        >
          <spriteMaterial
            map={haloTex}
            transparent
            opacity={0}
            blending={THREE.AdditiveBlending}
            depthWrite={false}
            fog={false}
          />
        </sprite>
      ))}
    </group>
  );
}
