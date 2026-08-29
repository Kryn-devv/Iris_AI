/**
 * Procedural canvas-generated textures — the only "assets" in the journey.
 * No files, no network: every card, label, glow sprite and panel is drawn
 * with Canvas2D at creation time and uploaded once as a THREE.CanvasTexture.
 *
 * Client-only: call these inside useMemo in components mounted with ssr:false.
 */
import * as THREE from "three";
import { PALETTE } from "./palette";

const FONT_STACK = `"Space Grotesk", "Inter", system-ui, -apple-system, sans-serif`;

function makeCanvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D context unavailable");
  return [canvas, ctx];
}

function finalize(canvas: HTMLCanvasElement): THREE.CanvasTexture {
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return tex;
}

export function roundRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Soft radial glow sprite (for stars, node halos, particle bursts). */
export function makeGlowTexture(color = "#ffffff", size = 128): THREE.CanvasTexture {
  const [canvas, ctx] = makeCanvas(size, size);
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, color);
  g.addColorStop(0.25, color + "cc");
  g.addColorStop(0.6, color + "33");
  g.addColorStop(1, color + "00");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return finalize(canvas);
}

/** Large soft nebula blob for depth haze (very low alpha, additive). */
export function makeNebulaTexture(color: string, size = 256): THREE.CanvasTexture {
  const [canvas, ctx] = makeCanvas(size, size);
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, color + "40");
  g.addColorStop(0.5, color + "18");
  g.addColorStop(1, color + "00");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return finalize(canvas);
}

/** Generic frosted-glass card with abstract content bars — shared by all
 *  instanced background cards (one texture, hundreds of instances). */
export function makeGlassCardTexture(): THREE.CanvasTexture {
  const w = 256;
  const h = 160;
  const [canvas, ctx] = makeCanvas(w, h);
  roundRectPath(ctx, 4, 4, w - 8, h - 8, 18);
  const g = ctx.createLinearGradient(0, 0, w, h);
  g.addColorStop(0, "rgba(158, 146, 255, 0.16)");
  g.addColorStop(1, "rgba(66, 214, 235, 0.10)");
  ctx.fillStyle = g;
  ctx.fill();
  ctx.strokeStyle = "rgba(235, 238, 248, 0.28)";
  ctx.lineWidth = 2;
  ctx.stroke();
  // Abstract "message" bars.
  ctx.fillStyle = "rgba(235, 238, 248, 0.30)";
  roundRectPath(ctx, 26, 32, 90, 12, 6);
  ctx.fill();
  ctx.fillStyle = "rgba(235, 238, 248, 0.18)";
  roundRectPath(ctx, 26, 62, w - 66, 10, 5);
  ctx.fill();
  roundRectPath(ctx, 26, 84, w - 96, 10, 5);
  ctx.fill();
  roundRectPath(ctx, 26, 106, w - 130, 10, 5);
  ctx.fill();
  return finalize(canvas);
}

function wrapLines(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number
): string[] {
  const words = text.split(" ");
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    const probe = line ? line + " " + word : word;
    if (ctx.measureText(probe).width > maxWidth && line) {
      lines.push(line);
      line = word;
    } else {
      line = probe;
    }
  }
  if (line) lines.push(line);
  return lines;
}

/** Crisp readable feedback snippet card (chaos chapter foreground). */
export function makeSnippetTexture(text: string, source: string): THREE.CanvasTexture {
  const w = 512;
  const h = 288;
  const [canvas, ctx] = makeCanvas(w, h);
  roundRectPath(ctx, 6, 6, w - 12, h - 12, 28);
  const g = ctx.createLinearGradient(0, 0, w, h);
  g.addColorStop(0, "rgba(19, 22, 34, 0.92)");
  g.addColorStop(1, "rgba(26, 30, 46, 0.86)");
  ctx.fillStyle = g;
  ctx.fill();
  ctx.strokeStyle = "rgba(158, 146, 255, 0.45)";
  ctx.lineWidth = 2.5;
  ctx.stroke();
  // Source chip.
  ctx.font = `600 22px ${FONT_STACK}`;
  const chipW = ctx.measureText(source.toUpperCase()).width + 36;
  roundRectPath(ctx, 34, 32, chipW, 40, 20);
  ctx.fillStyle = "rgba(124, 108, 255, 0.22)";
  ctx.fill();
  ctx.strokeStyle = "rgba(158, 146, 255, 0.5)";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.fillStyle = PALETTE.accentSoft;
  ctx.textBaseline = "middle";
  ctx.fillText(source.toUpperCase(), 52, 53);
  // Snippet text.
  ctx.font = `500 38px ${FONT_STACK}`;
  ctx.fillStyle = PALETTE.ink;
  const lines = wrapLines(ctx, `“${text}”`, w - 96);
  lines.slice(0, 3).forEach((line, i) => {
    ctx.fillText(line, 40, 128 + i * 52);
  });
  return finalize(canvas);
}

/** Small rounded label chip (intake channels around the funnel). */
export function makeChipTexture(label: string, hex: string = PALETTE.aurora): THREE.CanvasTexture {
  const w = 512;
  const h = 128;
  const [canvas, ctx] = makeCanvas(w, h);
  ctx.font = `600 44px ${FONT_STACK}`;
  const tw = ctx.measureText(label).width;
  const bw = Math.min(w - 8, tw + 96);
  const x = (w - bw) / 2;
  roundRectPath(ctx, x, 14, bw, h - 28, (h - 28) / 2);
  ctx.fillStyle = "rgba(13, 15, 24, 0.85)";
  ctx.fill();
  ctx.strokeStyle = hex + "99";
  ctx.lineWidth = 3;
  ctx.stroke();
  // Dot.
  ctx.beginPath();
  ctx.arc(x + 44, h / 2, 9, 0, Math.PI * 2);
  ctx.fillStyle = hex;
  ctx.fill();
  ctx.fillStyle = PALETTE.ink;
  ctx.textBaseline = "middle";
  ctx.fillText(label, x + 72, h / 2 + 2);
  return finalize(canvas);
}

/** Large stage banner for the roadmap regions (NOW / IN PROGRESS / …). */
export function makeStageTexture(name: string, hex: string): THREE.CanvasTexture {
  const w = 1024;
  const h = 256;
  const [canvas, ctx] = makeCanvas(w, h);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `700 128px ${FONT_STACK}`;
  ctx.shadowColor = hex;
  ctx.shadowBlur = 48;
  ctx.fillStyle = hex;
  ctx.fillText(name, w / 2, h / 2);
  ctx.shadowBlur = 0;
  ctx.fillStyle = "rgba(235, 238, 248, 0.92)";
  ctx.fillText(name, w / 2, h / 2);
  return finalize(canvas);
}

/** Feature card for the prioritization matrix (name + signals). */
export function makeFeatureCardTexture(
  name: string,
  votes: number,
  revenue: string,
  confidence: number
): THREE.CanvasTexture {
  const w = 512;
  const h = 320;
  const [canvas, ctx] = makeCanvas(w, h);
  roundRectPath(ctx, 6, 6, w - 12, h - 12, 26);
  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, "rgba(26, 30, 46, 0.94)");
  g.addColorStop(1, "rgba(13, 15, 24, 0.9)");
  ctx.fillStyle = g;
  ctx.fill();
  ctx.strokeStyle = "rgba(124, 108, 255, 0.55)";
  ctx.lineWidth = 2.5;
  ctx.stroke();
  ctx.fillStyle = PALETTE.ink;
  ctx.font = `700 40px ${FONT_STACK}`;
  ctx.fillText(name, 36, 72);
  ctx.font = `500 28px ${FONT_STACK}`;
  ctx.fillStyle = PALETTE.inkMuted;
  ctx.fillText(`▲ ${votes.toLocaleString("en-US")} votes`, 36, 136);
  ctx.fillText(`Revenue impact ${revenue}`, 36, 182);
  ctx.fillText(`AI confidence`, 36, 228);
  // Confidence bar.
  roundRectPath(ctx, 36, 252, w - 72, 18, 9);
  ctx.fillStyle = "rgba(235, 238, 248, 0.12)";
  ctx.fill();
  roundRectPath(ctx, 36, 252, (w - 72) * confidence, 18, 9);
  const bar = ctx.createLinearGradient(36, 0, w - 36, 0);
  bar.addColorStop(0, PALETTE.accent);
  bar.addColorStop(1, PALETTE.aurora);
  ctx.fillStyle = bar;
  ctx.fill();
  return finalize(canvas);
}

/** Draws the voting card frame + live counter. The vote chapter re-invokes
 *  this (throttled) on its own canvas as counts tick upward. */
export function drawVoteCard(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  name: string,
  count: number,
  charge: number // 0..1 → border/glow energy while counting
) {
  ctx.clearRect(0, 0, w, h);
  roundRectPath(ctx, 8, 8, w - 16, h - 16, 30);
  const g = ctx.createLinearGradient(0, 0, w, h);
  g.addColorStop(0, "rgba(19, 22, 34, 0.95)");
  g.addColorStop(1, "rgba(26, 30, 46, 0.9)");
  ctx.fillStyle = g;
  ctx.fill();
  ctx.strokeStyle = `rgba(158, 146, 255, ${0.35 + charge * 0.6})`;
  ctx.lineWidth = 3 + charge * 3;
  ctx.stroke();
  ctx.fillStyle = PALETTE.ink;
  ctx.font = `700 52px ${FONT_STACK}`;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  ctx.fillText(name, 44, 96);
  // Counter.
  ctx.font = `700 96px ${FONT_STACK}`;
  const label = count.toLocaleString("en-US");
  const grad = ctx.createLinearGradient(44, 0, 44 + ctx.measureText(label).width, 0);
  grad.addColorStop(0, PALETTE.accentSoft);
  grad.addColorStop(1, PALETTE.aurora);
  ctx.fillStyle = grad;
  ctx.fillText(label, 44, 220);
  // Upvote arrow pill.
  const px = w - 150;
  const py = 140;
  roundRectPath(ctx, px, py, 104, 116, 26);
  ctx.fillStyle = `rgba(124, 108, 255, ${0.2 + charge * 0.35})`;
  ctx.fill();
  ctx.strokeStyle = `rgba(158, 146, 255, ${0.5 + charge * 0.5})`;
  ctx.lineWidth = 2.5;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(px + 52, py + 26);
  ctx.lineTo(px + 80, py + 66);
  ctx.lineTo(px + 62, py + 66);
  ctx.lineTo(px + 62, py + 92);
  ctx.lineTo(px + 42, py + 92);
  ctx.lineTo(px + 42, py + 66);
  ctx.lineTo(px + 24, py + 66);
  ctx.closePath();
  ctx.fillStyle = PALETTE.ink;
  ctx.fill();
}

/** Survey panel drawn entirely in canvas: NPS scale / choices / open text. */
export function makeSurveyPanelTexture(panel: {
  kind: "NPS" | "CHOICE" | "TEXT";
  prompt: string;
  hint?: string;
  choices?: string[];
}): THREE.CanvasTexture {
  const w = 768;
  const h = 512;
  const [canvas, ctx] = makeCanvas(w, h);
  roundRectPath(ctx, 8, 8, w - 16, h - 16, 34);
  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, "rgba(19, 22, 34, 0.92)");
  g.addColorStop(1, "rgba(13, 15, 24, 0.88)");
  ctx.fillStyle = g;
  ctx.fill();
  ctx.strokeStyle = "rgba(66, 214, 235, 0.4)";
  ctx.lineWidth = 2.5;
  ctx.stroke();
  ctx.fillStyle = PALETTE.aurora;
  ctx.font = `600 26px ${FONT_STACK}`;
  ctx.fillText(panel.kind === "NPS" ? "NPS SURVEY" : panel.kind === "CHOICE" ? "MULTIPLE CHOICE" : "OPEN QUESTION", 48, 72);
  ctx.fillStyle = PALETTE.ink;
  ctx.font = `600 42px ${FONT_STACK}`;
  const lines = wrapLines(ctx, panel.prompt, w - 96);
  lines.slice(0, 2).forEach((line, i) => ctx.fillText(line, 48, 136 + i * 54));
  const bodyY = 150 + Math.min(lines.length, 2) * 54;

  if (panel.kind === "NPS") {
    const cell = (w - 96 - 10 * 10) / 11;
    for (let i = 0; i <= 10; i++) {
      const x = 48 + i * (cell + 10);
      roundRectPath(ctx, x, bodyY, cell, 78, 14);
      const hot = i >= 9;
      ctx.fillStyle = hot ? "rgba(124, 108, 255, 0.45)" : "rgba(235, 238, 248, 0.07)";
      ctx.fill();
      ctx.strokeStyle = hot ? PALETTE.accentSoft : "rgba(235, 238, 248, 0.22)";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = hot ? PALETTE.ink : PALETTE.inkMuted;
      ctx.font = `600 32px ${FONT_STACK}`;
      ctx.textAlign = "center";
      ctx.fillText(String(i), x + cell / 2, bodyY + 50);
      ctx.textAlign = "left";
    }
    if (panel.hint) {
      ctx.fillStyle = PALETTE.inkMuted;
      ctx.font = `400 24px ${FONT_STACK}`;
      ctx.fillText(panel.hint, 48, bodyY + 130);
    }
  } else if (panel.kind === "CHOICE" && panel.choices) {
    panel.choices.slice(0, 3).forEach((choice, i) => {
      const y = bodyY + i * 74;
      roundRectPath(ctx, 48, y, w - 96, 58, 16);
      const active = i === 0;
      ctx.fillStyle = active ? "rgba(66, 214, 235, 0.14)" : "rgba(235, 238, 248, 0.05)";
      ctx.fill();
      ctx.strokeStyle = active ? "rgba(66, 214, 235, 0.6)" : "rgba(235, 238, 248, 0.2)";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(84, y + 29, 12, 0, Math.PI * 2);
      ctx.strokeStyle = active ? PALETTE.aurora : PALETTE.inkMuted;
      ctx.stroke();
      if (active) {
        ctx.beginPath();
        ctx.arc(84, y + 29, 6, 0, Math.PI * 2);
        ctx.fillStyle = PALETTE.aurora;
        ctx.fill();
      }
      ctx.fillStyle = PALETTE.ink;
      ctx.font = `500 30px ${FONT_STACK}`;
      ctx.fillText(choice, 116, y + 39);
    });
  } else {
    // Open text: an input field with a blinking-caret suggestion.
    roundRectPath(ctx, 48, bodyY, w - 96, 150, 18);
    ctx.fillStyle = "rgba(235, 238, 248, 0.05)";
    ctx.fill();
    ctx.strokeStyle = "rgba(235, 238, 248, 0.22)";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = PALETTE.inkMuted;
    ctx.font = `400 28px ${FONT_STACK}`;
    ctx.fillText("Tell us anything…", 76, bodyY + 52);
    ctx.fillStyle = PALETTE.accentSoft;
    ctx.fillRect(76, bodyY + 76, 3, 40);
  }
  return finalize(canvas);
}

/** Changelog milestone label. */
export function makeMilestoneTexture(version: string, title: string): THREE.CanvasTexture {
  const w = 640;
  const h = 256;
  const [canvas, ctx] = makeCanvas(w, h);
  ctx.textAlign = "center";
  ctx.font = `700 88px ${FONT_STACK}`;
  ctx.shadowColor = PALETTE.accent;
  ctx.shadowBlur = 36;
  ctx.fillStyle = PALETTE.accentSoft;
  ctx.fillText(version, w / 2, 108);
  ctx.shadowBlur = 0;
  ctx.fillStyle = PALETTE.ink;
  ctx.fillText(version, w / 2, 108);
  ctx.font = `500 40px ${FONT_STACK}`;
  ctx.fillStyle = PALETTE.inkMuted;
  ctx.fillText(title, w / 2, 178);
  return finalize(canvas);
}

/** Named mini-card docked at roadmap stages / orbiting the finale. */
export function makeMiniCardTexture(name: string, hex: string): THREE.CanvasTexture {
  const w = 384;
  const h = 128;
  const [canvas, ctx] = makeCanvas(w, h);
  roundRectPath(ctx, 4, 4, w - 8, h - 8, 20);
  ctx.fillStyle = "rgba(19, 22, 34, 0.92)";
  ctx.fill();
  ctx.strokeStyle = hex + "88";
  ctx.lineWidth = 2.5;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(44, h / 2, 8, 0, Math.PI * 2);
  ctx.fillStyle = hex;
  ctx.fill();
  ctx.fillStyle = PALETTE.ink;
  ctx.font = `600 34px ${FONT_STACK}`;
  ctx.textBaseline = "middle";
  ctx.fillText(name, 72, h / 2 + 2);
  return finalize(canvas);
}
