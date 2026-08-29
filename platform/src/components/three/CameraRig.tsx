"use client";

/**
 * The choreographed camera: a keyframed flight path through the whole
 * universe. Scroll progress picks a segment, each segment eases smoothly,
 * and pointer parallax + idle drift are layered on top. All math reuses
 * module-scope scratch vectors — zero per-frame allocation.
 */
import { useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { journeyStore } from "@/components/marketing/journeyState";
import { smoothstep, lerp, damp, clamp01 } from "./timeline";

interface Keyframe {
  t: number;
  pos: [number, number, number];
  look: [number, number, number];
  fov: number;
}

/** One continuous journey: hero core → chaos → funnel → neural world →
 *  matrix → votes → roadmap → surveys → changelog → finale pull-back. */
const KEYFRAMES: Keyframe[] = [
  { t: 0.0, pos: [0, 0.8, 15], look: [0, 0, 0], fov: 55 },
  { t: 0.095, pos: [0, 0.4, 9.5], look: [0, 0, -30], fov: 58 },
  { t: 0.15, pos: [0.5, 0, -32], look: [0, 0, -52], fov: 60 },
  { t: 0.205, pos: [-1, -0.5, -58], look: [0, 0.5, -82], fov: 60 },
  { t: 0.26, pos: [0, 1.5, -72], look: [0, -0.4, -95], fov: 58 },
  { t: 0.315, pos: [0, 0.3, -90], look: [0, -0.4, -116], fov: 62 },
  { t: 0.38, pos: [3, 1.5, -126], look: [-2, 0, -148], fov: 60 },
  { t: 0.445, pos: [-3, -1, -160], look: [0, 0, -184], fov: 60 },
  { t: 0.5, pos: [0, 5, -188], look: [0, -1, -210], fov: 55 },
  { t: 0.545, pos: [0, 2, -218], look: [0, 0.5, -240], fov: 58 },
  { t: 0.6, pos: [1.5, 0.5, -243], look: [-0.5, 0.4, -261], fov: 56 },
  { t: 0.645, pos: [-1, 0, -272], look: [0.5, 0, -292], fov: 58 },
  { t: 0.68, pos: [3.2, 1.2, -290], look: [0, 0.4, -308], fov: 58 },
  { t: 0.705, pos: [-3.6, 0.9, -322], look: [1, 0.4, -342], fov: 58 },
  { t: 0.73, pos: [3.6, 0.6, -352], look: [-1, 0.4, -372], fov: 58 },
  { t: 0.755, pos: [0, 1, -384], look: [0, 0, -406], fov: 58 },
  { t: 0.8, pos: [0.4, 0.2, -420], look: [-0.6, 0.1, -437], fov: 52 },
  { t: 0.845, pos: [-0.2, 0.1, -448], look: [0.4, 0.1, -466], fov: 52 },
  { t: 0.88, pos: [1.6, 0.3, -476], look: [-1.2, 0, -498], fov: 56 },
  { t: 0.925, pos: [1.6, 0.3, -545], look: [-1.2, 0, -565], fov: 56 },
  { t: 0.96, pos: [0, 2.5, -582], look: [0, 0, -612], fov: 58 },
  { t: 1.0, pos: [0, 7.5, -588], look: [0, 0, -612], fov: 64 },
];

const _basePos = new THREE.Vector3();
const _baseLook = new THREE.Vector3();
const _a = new THREE.Vector3();
const _b = new THREE.Vector3();
const _forward = new THREE.Vector3();
const _right = new THREE.Vector3();
const _upv = new THREE.Vector3();
const _targetPos = new THREE.Vector3();
const _targetLook = new THREE.Vector3();
const UP = new THREE.Vector3(0, 1, 0);

function sample(p: number, outPos: THREE.Vector3, outLook: THREE.Vector3): number {
  const n = KEYFRAMES.length;
  let i = 0;
  while (i < n - 2 && p > KEYFRAMES[i + 1].t) i++;
  const k0 = KEYFRAMES[i];
  const k1 = KEYFRAMES[i + 1];
  const span = Math.max(k1.t - k0.t, 1e-5);
  // Smooth each segment: subtle ease at chapter boundaries, still continuous.
  const u = smoothstep(clamp01((p - k0.t) / span));
  _a.set(k0.pos[0], k0.pos[1], k0.pos[2]);
  _b.set(k1.pos[0], k1.pos[1], k1.pos[2]);
  outPos.lerpVectors(_a, _b, u);
  _a.set(k0.look[0], k0.look[1], k0.look[2]);
  _b.set(k1.look[0], k1.look[1], k1.look[2]);
  outLook.lerpVectors(_a, _b, u);
  return lerp(k0.fov, k1.fov, u);
}

export function CameraRig() {
  const smoothedPos = useRef(new THREE.Vector3(0, 0.8, 15));
  const smoothedLook = useRef(new THREE.Vector3(0, 0, 0));
  const smoothedFov = useRef(55);
  const parallax = useRef({ x: 0, y: 0 });
  const time = useRef(0);

  useFrame((state, delta) => {
    const dt = Math.min(delta, 0.05);
    time.current += dt;
    const t = time.current;
    const camera = state.camera as THREE.PerspectiveCamera;
    const p = journeyStore.progress;

    const fov = sample(p, _basePos, _baseLook);

    // Idle cinematic drift (strongest in the hero, subtle elsewhere).
    const driftAmp = p < 0.1 ? 0.35 : 0.12;
    _basePos.x += Math.sin(t * 0.23) * driftAmp;
    _basePos.y += Math.sin(t * 0.31 + 1.7) * driftAmp * 0.6;

    // Damped pointer parallax.
    const k = damp(4, dt);
    parallax.current.x += (journeyStore.pointerX - parallax.current.x) * k;
    parallax.current.y += (journeyStore.pointerY - parallax.current.y) * k;
    const px = parallax.current.x;
    const py = parallax.current.y;

    _forward.subVectors(_baseLook, _basePos).normalize();
    _right.crossVectors(_forward, UP).normalize();
    _upv.crossVectors(_right, _forward).normalize();

    const posAmp = p < 0.1 ? 0.7 : 0.35;
    const lookAmp = p < 0.1 ? 1.4 : 0.8;
    _targetPos
      .copy(_basePos)
      .addScaledVector(_right, px * posAmp)
      .addScaledVector(_upv, -py * posAmp * 0.7);
    _targetLook
      .copy(_baseLook)
      .addScaledVector(_right, px * lookAmp)
      .addScaledVector(_upv, -py * lookAmp * 0.6);

    // Damp toward targets so fast scrolling still feels weighty.
    const move = damp(7, dt);
    smoothedPos.current.lerp(_targetPos, move);
    smoothedLook.current.lerp(_targetLook, move);
    smoothedFov.current = lerp(smoothedFov.current, fov, move);

    camera.position.copy(smoothedPos.current);
    camera.lookAt(smoothedLook.current);
    if (Math.abs(camera.fov - smoothedFov.current) > 0.01) {
      camera.fov = smoothedFov.current;
      camera.updateProjectionMatrix();
    }
  });

  return null;
}
