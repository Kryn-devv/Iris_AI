"use client";

/**
 * Scene root: assembles the whole universe, drives the global fog (the
 * cosmos calms during the surveys chapter) and scales particle budgets by
 * the quality tier decided at boot.
 */
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { CHAPTERS } from "@/components/marketing/copy";
import { journeyStore } from "@/components/marketing/journeyState";
import { PALETTE } from "./palette";
import { segmentProgress, fadeWindow, lerp } from "./timeline";
import { CameraRig } from "./CameraRig";
import { Starfield, Nebula } from "./chapters/Starfield";
import { CoreStar } from "./chapters/CoreStar";
import { ChaosField } from "./chapters/ChaosField";
import { CaptureFunnel } from "./chapters/CaptureFunnel";
import { NeuralField } from "./chapters/NeuralField";
import { PriorityMatrix } from "./chapters/PriorityMatrix";
import { VoteField } from "./chapters/VoteField";
import { RoadmapPath } from "./chapters/RoadmapPath";
import { SurveySpace } from "./chapters/SurveySpace";
import { ChangelogTimeline } from "./chapters/ChangelogTimeline";
import { Finale } from "./chapters/Finale";

const HERO_RANGE = CHAPTERS.find((c) => c.id === "hero")!.range;
const SURVEY_RANGE = CHAPTERS.find((c) => c.id === "surveys")!.range;

/** Hero core hides once the camera has flown well past it. */
function HeroCore({ fragments }: { fragments: number }) {
  const group = useRef<THREE.Group>(null);
  useFrame(() => {
    if (!group.current) return;
    group.current.visible = journeyStore.progress < HERO_RANGE[1] + 0.08;
  });
  return (
    <group ref={group}>
      <CoreStar position={[0, 0, 0]} fragmentCount={fragments} />
    </group>
  );
}

/** Fog controller — must live inside the Canvas to reach the scene. */
function FogRig() {
  const scene = useThree((s) => s.scene);
  const fog = useMemo(() => new THREE.FogExp2(PALETTE.void, 0.004), []);
  useFrame(() => {
    if (scene.fog !== fog) scene.fog = fog;
    const calm = fadeWindow(
      segmentProgress(journeyStore.progress, SURVEY_RANGE),
      0.3,
      0.3
    );
    fog.density = lerp(0.004, 0.02, calm);
  });
  return null;
}

export interface SceneProps {
  /** 0..1 particle budget multiplier (mobile ≈ 0.35). */
  particleScale: number;
}

export default function Scene({ particleScale }: SceneProps) {
  const n = (base: number) => Math.max(8, Math.round(base * particleScale));

  return (
    <>
      <color attach="background" args={[PALETTE.void]} />
      <FogRig />
      <CameraRig />

      {/* Persistent cosmos. */}
      <Starfield count={n(2400)} />
      <Nebula />

      {/* The journey, in scroll order. */}
      <HeroCore fragments={n(1300)} />
      <ChaosField cardCount={n(240)} />
      <CaptureFunnel particleCount={n(900)} />
      <NeuralField nodeCount={n(150)} pulseCount={n(70)} />
      <PriorityMatrix />
      <VoteField ambientCards={n(70)} />
      <RoadmapPath />
      <SurveySpace moteCount={n(110)} />
      <ChangelogTimeline />
      <Finale fragmentCount={n(500)} />
    </>
  );
}
