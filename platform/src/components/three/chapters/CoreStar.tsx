"use client";

/**
 * The intelligence core: layered procedural icosahedrons with a fresnel
 * shell, an additive halo, and an instanced swarm of drifting data fragments.
 * Used at full energy in the hero and reused, calmer, in the finale.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { PALETTE } from "../palette";
import { makeGlowTexture } from "../textures";
import { hash11 } from "../timeline";
import { useJourneyClock } from "../hooks";

const _m = new THREE.Matrix4();
const _pos = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _scl = new THREE.Vector3();
const _euler = new THREE.Euler();

const FRESNEL_VERT = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`;

const FRESNEL_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uIntensity;
  uniform float uPower;
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    float fres = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), uPower);
    gl_FragColor = vec4(uColor, fres * uIntensity);
  }
`;

export interface CoreStarProps {
  position?: [number, number, number];
  scale?: number;
  /** Number of drifting fragments (already quality-scaled by the caller). */
  fragmentCount?: number;
  /** Calm mode: slower spin, tighter swarm (finale). */
  calm?: boolean;
  seed?: number;
}

export function CoreStar({
  position = [0, 0, 0],
  scale = 1,
  fragmentCount = 1200,
  calm = false,
  seed = 1,
}: CoreStarProps) {
  const group = useRef<THREE.Group>(null);
  const shellRef = useRef<THREE.Mesh>(null);
  const midRef = useRef<THREE.LineSegments>(null);
  const innerRef = useRef<THREE.Mesh>(null);
  const fragRef = useRef<THREE.InstancedMesh>(null);
  const haloRef = useRef<THREE.Sprite>(null);
  const clock = useJourneyClock();

  const { innerGeo, midGeo, shellGeo, fragGeo } = useMemo(() => {
    const inner = new THREE.IcosahedronGeometry(1.05, 1);
    const mid = new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.55, 1));
    const shell = new THREE.IcosahedronGeometry(2.05, 3);
    const frag = new THREE.TetrahedronGeometry(0.055);
    return { innerGeo: inner, midGeo: mid, shellGeo: shell, fragGeo: frag };
  }, []);

  const { innerMat, midMat, shellMat, fragMat, haloMat } = useMemo(() => {
    const innerM = new THREE.MeshBasicMaterial({
      color: new THREE.Color(PALETTE.accentSoft),
      transparent: true,
      opacity: 0.95,
    });
    const midM = new THREE.LineBasicMaterial({
      color: new THREE.Color(PALETTE.aurora),
      transparent: true,
      opacity: 0.5,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      fog: false,
    });
    const shellM = new THREE.ShaderMaterial({
      vertexShader: FRESNEL_VERT,
      fragmentShader: FRESNEL_FRAG,
      uniforms: {
        uColor: { value: new THREE.Color(PALETTE.accent) },
        uIntensity: { value: 0.9 },
        uPower: { value: 2.4 },
      },
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.FrontSide,
    });
    const fragM = new THREE.MeshBasicMaterial({
      color: new THREE.Color(PALETTE.accentSoft),
      transparent: true,
      opacity: 0.85,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      fog: false,
    });
    const haloM = new THREE.SpriteMaterial({
      map: makeGlowTexture(PALETTE.accent),
      transparent: true,
      opacity: 0.55,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      fog: false,
    });
    return { innerMat: innerM, midMat: midM, shellMat: shellM, fragMat: fragM, haloMat: haloM };
  }, []);

  /** Per-fragment orbit parameters: radius, phi, theta0, speed, size, wobble. */
  const fragData = useMemo(() => {
    const data = new Float32Array(fragmentCount * 6);
    for (let i = 0; i < fragmentCount; i++) {
      const s = seed * 1000 + i;
      const o = i * 6;
      data[o] = 2.8 + hash11(s) * (calm ? 4 : 9); // radius
      data[o + 1] = Math.acos(2 * hash11(s + 1) - 1); // phi
      data[o + 2] = hash11(s + 2) * Math.PI * 2; // theta0
      data[o + 3] = (0.02 + hash11(s + 3) * 0.09) * (calm ? 0.5 : 1); // speed
      data[o + 4] = 0.5 + hash11(s + 4) * 1.4; // size
      data[o + 5] = 0.2 + hash11(s + 5) * 0.8; // wobble
    }
    return data;
  }, [fragmentCount, calm, seed]);

  useFrame(() => {
    const t = clock.current;
    const g = group.current;
    if (!g) return;
    // Skip all work when this core or any ancestor chapter group is hidden.
    let o: THREE.Object3D | null = g;
    while (o) {
      if (!o.visible) return;
      o = o.parent;
    }
    const spin = calm ? 0.35 : 1;
    if (innerRef.current) {
      innerRef.current.rotation.y = t * 0.12 * spin;
      innerRef.current.rotation.x = t * 0.07 * spin;
      const pulse = 1 + Math.sin(t * 1.4) * 0.035;
      innerRef.current.scale.setScalar(pulse);
    }
    if (midRef.current) {
      midRef.current.rotation.y = -t * 0.08 * spin;
      midRef.current.rotation.z = t * 0.05 * spin;
    }
    if (shellRef.current) {
      shellRef.current.rotation.y = t * 0.04 * spin;
      const breathe = 1 + Math.sin(t * 0.9 + 1.3) * 0.05;
      shellRef.current.scale.setScalar(breathe);
    }
    if (haloRef.current) {
      const hs = 10.5 + Math.sin(t * 0.8) * 0.7;
      haloRef.current.scale.set(hs, hs, 1);
    }
    const mesh = fragRef.current;
    if (mesh) {
      for (let i = 0; i < fragmentCount; i++) {
        const o = i * 6;
        const radius = fragData[o] + Math.sin(t * fragData[o + 5] + i) * 0.35;
        const phi = fragData[o + 1] + Math.sin(t * 0.2 + i) * 0.1;
        const theta = fragData[o + 2] + t * fragData[o + 3];
        const sinPhi = Math.sin(phi);
        _pos.set(
          radius * sinPhi * Math.cos(theta),
          radius * Math.cos(phi),
          radius * sinPhi * Math.sin(theta)
        );
        _euler.set(t * fragData[o + 5], theta, 0);
        _quat.setFromEuler(_euler);
        _scl.setScalar(fragData[o + 4]);
        _m.compose(_pos, _quat, _scl);
        mesh.setMatrixAt(i, _m);
      }
      mesh.instanceMatrix.needsUpdate = true;
    }
  });

  return (
    <group ref={group} position={position} scale={scale}>
      <mesh ref={innerRef} geometry={innerGeo} material={innerMat} />
      <lineSegments ref={midRef} geometry={midGeo} material={midMat} />
      <mesh ref={shellRef} geometry={shellGeo} material={shellMat} />
      <sprite ref={haloRef} material={haloMat} scale={[10.5, 10.5, 1]} />
      <instancedMesh
        ref={fragRef}
        args={[fragGeo, fragMat, fragmentCount]}
        frustumCulled={false}
      />
    </group>
  );
}
