/* IRIS Hologram — a volumetric 3D particle sphere on a 2D canvas.
 *
 * No WebGL, no libraries. The depth illusion comes from stacked cues:
 *   - ~1000 fibonacci-sphere points drawn as pre-rendered glow sprites
 *   - two-tone depth colour (accent up front, deep indigo behind)
 *   - a faint triangulated mesh between neighbouring points
 *   - perspective projection + mouse parallax on the whole scene
 *   - a breathing nucleus, orbit rings and drifting background dust
 *
 * States driven by the assistant:
 *   idle      — slow drift, gentle breathing
 *   listening — expands with mic level, sonar ripples
 *   thinking  — fast axial swirl, comet trails, scanline
 *   speaking  — rhythmic pulse + equatorial waveform ring
 */
"use strict";

class Hologram {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.accent = opts.accent || "#5eead4";
    this.state = "idle";
    this.level = 0;            // 0..1 mic / speech amplitude
    this.targetLevel = 0;
    this.quality = opts.quality || "high";
    this.reducedMotion = !!opts.reducedMotion;

    this.rotX = 0.35;
    this.rotY = 0;
    this.pulse = 0;
    this.time = 0;
    this.stateBlend = { expand: 0, swirl: 0, pulseAmt: 0 };

    // Per-state hue: the sphere itself signals what IRIS is doing.
    this.stateColors = {
      idle: opts.accent || "#5eead4",       // teal
      listening: "#67e8f9",                  // cyan
      thinking: "#a78bfa",                   // violet
      speaking: "#5eead4",                   // teal
    };
    this.rgb = this._hexToRGB(this.stateColors.idle);
    this._spriteKey = "";

    // Mouse parallax (lerped toward the cursor, off for reduced motion).
    this.parX = 0; this.parY = 0;
    this.parTX = 0; this.parTY = 0;
    this._onMouse = (e) => {
      const nx = (e.clientX / window.innerWidth) * 2 - 1;
      const ny = (e.clientY / window.innerHeight) * 2 - 1;
      this.parTX = nx; this.parTY = ny;
    };
    if (!this.reducedMotion) window.addEventListener("pointermove", this._onMouse, { passive: true });

    const counts = { low: 380, medium: 700, high: 1050 };
    this.pointCount = counts[this.quality] || 1050;
    this.ringCount = 3;
    this.ripples = [];         // sonar rings while listening
    this._lastRipple = 0;

    this._buildGeometry();
    this._buildSprites();
    this._resize = this._resize.bind(this);
    window.addEventListener("resize", this._resize);
    this._resize();

    this._frame = this._frame.bind(this);
    this.running = true;
    requestAnimationFrame(this._frame);
  }

  _buildGeometry() {
    // Fibonacci sphere: evenly distributed points.
    this.points = [];
    const n = this.pointCount;
    const phi = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < n; i++) {
      const y = 1 - (i / (n - 1)) * 2;
      const radius = Math.sqrt(1 - y * y);
      const theta = phi * i;
      this.points.push({
        x: Math.cos(theta) * radius,
        y: y,
        z: Math.sin(theta) * radius,
        jitter: Math.random() * Math.PI * 2,
        size: 0.55 + Math.random() * 1.15,
        tw: 0.5 + Math.random(),           // twinkle rate
      });
    }

    // Static wireframe: connect each sampled point to its nearest neighbours.
    // Precomputed once — rotation preserves adjacency on a rigid sphere.
    this.edges = [];
    const meshEvery = this.quality === "low" ? 7 : 5;
    const sampled = [];
    for (let i = 0; i < n; i += meshEvery) sampled.push(i);
    for (const i of sampled) {
      const a = this.points[i];
      let best = -1, best2 = -1, bd = 9, bd2 = 9;
      for (const j of sampled) {
        if (j === i) continue;
        const b = this.points[j];
        const d = (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2;
        if (d < bd) { bd2 = bd; best2 = best; bd = d; best = j; }
        else if (d < bd2) { bd2 = d; best2 = j; }
      }
      if (best >= 0 && i < best) this.edges.push([i, best]);
      if (best2 >= 0 && i < best2) this.edges.push([i, best2]);
    }

    // Orbit ring particles (electron-style accents) + comets for "thinking".
    this.rings = [];
    for (let r = 0; r < this.ringCount; r++) {
      const tilt = (Math.PI / this.ringCount) * r + 0.4;
      const speed = 0.012 + r * 0.004;
      const cnt = this.quality === "low" ? 24 : 42;
      const ringPoints = [];
      for (let i = 0; i < cnt; i++) ringPoints.push((Math.PI * 2 * i) / cnt);
      this.rings.push({ tilt, speed, offset: Math.random() * 6, points: ringPoints, comet: Math.random() * 6.28 });
    }

    // Background dust: slow parallax starfield behind the sphere.
    this.dust = [];
    const dustCount = this.quality === "low" ? 40 : 90;
    for (let i = 0; i < dustCount; i++) {
      this.dust.push({
        x: Math.random(), y: Math.random(),
        z: 0.25 + Math.random() * 0.75,     // pseudo-depth for parallax
        s: 0.4 + Math.random() * 1.1,
        p: Math.random() * Math.PI * 2,
      });
    }
  }

  /* Pre-rendered radial-glow sprites: drawing ~1000 gradient-lit particles per
   * frame is only affordable as drawImage() of a tiny cached canvas. */
  _buildSprites() {
    const mk = (r, g, b, coreA) => {
      const s = 32, c = document.createElement("canvas");
      c.width = c.height = s;
      const g2 = c.getContext("2d");
      const grad = g2.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
      grad.addColorStop(0, `rgba(255,255,255,${coreA})`);
      grad.addColorStop(0.25, `rgba(${r},${g},${b},0.85)`);
      grad.addColorStop(0.6, `rgba(${r},${g},${b},0.25)`);
      grad.addColorStop(1, "rgba(0,0,0,0)");
      g2.fillStyle = grad;
      g2.fillRect(0, 0, s, s);
      return c;
    };
    const [ar, ag, ab] = this._accentRGB();
    this.spriteNear = mk(ar, ag, ab, 0.9);          // bright accent, hot core
    this.spriteFar = mk(99, 102, 241, 0.25);        // cool indigo, soft
    this.spriteDust = mk(148, 163, 199, 0.18);      // faint slate dust
  }

  _resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.max(1, rect.width * dpr);
    this.canvas.height = Math.max(1, rect.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.cx = rect.width / 2;
    this.cy = rect.height / 2;
    this.baseR = Math.min(rect.width, rect.height) * 0.30;
  }

  setState(state) {
    if (["idle", "listening", "thinking", "speaking"].includes(state)) this.state = state;
  }

  setLevel(level) {
    this.targetLevel = Math.max(0, Math.min(1, level));
  }

  setAccent(color) {
    this.accent = color;
    this.stateColors.idle = color;
    this.stateColors.speaking = color;
    this._buildSprites();
  }

  destroy() {
    this.running = false;
    window.removeEventListener("resize", this._resize);
    window.removeEventListener("pointermove", this._onMouse);
  }

  _accentRGB() {
    return this.rgb.map(Math.round);
  }

  _hexToRGB(hex) {
    const c = hex.replace("#", "");
    return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
  }

  _frame() {
    if (!this.running) return;
    const ctx = this.ctx;
    const rect = this.canvas.getBoundingClientRect();
    const w = rect.width, h = rect.height;
    this.time += 1 / 60;
    const t = this.time;

    // Smooth state blending.
    const target = {
      expand: this.state === "listening" ? 1 : 0,
      swirl: this.state === "thinking" ? 1 : 0,
      pulseAmt: this.state === "speaking" ? 1 : 0,
    };
    for (const k in target) {
      this.stateBlend[k] += (target[k] - this.stateBlend[k]) * 0.06;
    }
    this.level += (this.targetLevel - this.level) * 0.18;

    const motionScale = this.reducedMotion ? 0.25 : 1;
    const b = this.stateBlend;

    // Glide the palette toward the state colour; sprites are cached per
    // rounded colour so the rebuild only happens a few times per transition.
    const targetRGB = this._hexToRGB(this.stateColors[this.state] || this.accent);
    for (let i = 0; i < 3; i++) this.rgb[i] += (targetRGB[i] - this.rgb[i]) * 0.05;
    const key = this.rgb.map((v) => v >> 3).join(",");
    if (key !== this._spriteKey) { this._spriteKey = key; this._buildSprites(); }

    // Parallax follows the pointer with heavy damping.
    this.parX += (this.parTX - this.parX) * 0.04;
    this.parY += (this.parTY - this.parY) * 0.04;

    this.rotY += (0.0028 + b.swirl * 0.028 + b.pulseAmt * 0.004) * motionScale;
    this.rotX = 0.35 + Math.sin(t * 0.21) * 0.07 * motionScale + this.parY * 0.22;
    const rotYr = this.rotY + this.parX * 0.35;

    // Breathing at rest; pulse when speaking; expand with mic level.
    const breathe = 0.02 * Math.sin(t * 1.1) * (1 - b.expand - b.pulseAmt * 0.5);
    this.pulse = b.pulseAmt * (0.5 + 0.5 * Math.sin(t * 9)) * 0.12 +
                 b.expand * (0.10 + this.level * 0.22) + breathe;

    const R = this.baseR * (1 + this.pulse);
    const [ar, ag, ab] = this._accentRGB();

    ctx.clearRect(0, 0, w, h);

    // ── Background dust (parallax starfield) ──
    ctx.globalCompositeOperation = "lighter";
    for (const d of this.dust) {
      const twinkle = 0.35 + 0.3 * Math.sin(t * d.z * 1.6 + d.p);
      const dx = (d.x * w + this.parX * -18 * d.z + w) % w;
      const dy = (d.y * h + Math.sin(t * 0.05 + d.p) * 6 * d.z + this.parY * -12 * d.z + h) % h;
      const s = d.s * (1.6 - d.z) * 6;
      ctx.globalAlpha = twinkle * 0.5;
      ctx.drawImage(this.spriteDust, dx - s / 2, dy - s / 2, s, s);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";

    // ── Ambient core glow ──
    const glow = ctx.createRadialGradient(this.cx, this.cy, R * 0.05, this.cx, this.cy, R * 2.0);
    const coreAlpha = 0.11 + b.expand * 0.10 + b.pulseAmt * 0.08 + b.swirl * 0.05;
    glow.addColorStop(0, `rgba(${ar},${ag},${ab},${coreAlpha})`);
    glow.addColorStop(0.45, `rgba(${ar},${ag},${ab},${coreAlpha * 0.22})`);
    glow.addColorStop(0.8, "rgba(99,102,241,0.03)");
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);

    ctx.globalCompositeOperation = "lighter";

    // ── Nucleus: hot inner core with halo ──
    const coreR = R * (0.16 + 0.02 * Math.sin(t * 2.2) + b.pulseAmt * 0.05 + b.expand * this.level * 0.10);
    const core = ctx.createRadialGradient(this.cx, this.cy, 0, this.cx, this.cy, coreR * 2.4);
    core.addColorStop(0, `rgba(255,255,255,${0.55 + b.pulseAmt * 0.25})`);
    core.addColorStop(0.18, `rgba(${ar},${ag},${ab},0.5)`);
    core.addColorStop(0.5, `rgba(${ar},${ag},${ab},0.10)`);
    core.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(this.cx, this.cy, coreR * 2.4, 0, 6.2832);
    ctx.fill();

    const sinY = Math.sin(rotYr), cosY = Math.cos(rotYr);
    const sinX = Math.sin(this.rotX), cosX = Math.cos(this.rotX);
    const persp = 3.2;
    const jitterAmt = b.swirl * 0.05 + b.expand * this.level * 0.06;

    // Project every point once per frame; reuse for particles and mesh.
    const proj = this._proj || (this._proj = new Float32Array(this.pointCount * 4));
    for (let i = 0; i < this.points.length; i++) {
      const p = this.points[i];
      let px = p.x, py = p.y, pz = p.z;
      if (jitterAmt > 0.002) {
        const j = Math.sin(t * 3 + p.jitter) * jitterAmt;
        px *= 1 + j; py *= 1 + j; pz *= 1 + j;
      }
      const x1 = px * cosY - pz * sinY;
      const z1 = px * sinY + pz * cosY;
      const y1 = py * cosX - z1 * sinX;
      const z2 = py * sinX + z1 * cosX;
      const scale = persp / (persp + z2);
      proj[i * 4] = this.cx + x1 * R * scale;
      proj[i * 4 + 1] = this.cy + y1 * R * scale;
      proj[i * 4 + 2] = (z2 + 1) / 2;   // depth 0 near .. 1 far
      proj[i * 4 + 3] = scale;
    }

    // ── Wireframe mesh (depth-faded hairlines) ──
    const meshAlpha = 0.05 + b.swirl * 0.05 + b.expand * 0.03;
    ctx.lineWidth = 0.55;
    for (const [i, j] of this.edges) {
      const di = proj[i * 4 + 2], dj = proj[j * 4 + 2];
      const depth = (di + dj) / 2;
      const a = (1 - depth) * meshAlpha;
      if (a <= 0.008) continue;
      ctx.strokeStyle = `rgba(${ar},${ag},${ab},${a.toFixed(3)})`;
      ctx.beginPath();
      ctx.moveTo(proj[i * 4], proj[i * 4 + 1]);
      ctx.lineTo(proj[j * 4], proj[j * 4 + 1]);
      ctx.stroke();
    }

    // ── Sphere particles as glow sprites, two-tone by depth ──
    for (let i = 0; i < this.points.length; i++) {
      const p = this.points[i];
      const sx = proj[i * 4], sy = proj[i * 4 + 1];
      const depth = proj[i * 4 + 2], scale = proj[i * 4 + 3];
      const twinkle = 0.85 + 0.15 * Math.sin(t * p.tw * 2.4 + p.jitter);
      const alpha = (0.80 - depth * 0.62 + b.expand * 0.1) * twinkle;
      if (alpha <= 0.02) continue;
      const size = p.size * scale * (1 + b.pulseAmt * 0.3) * 7;
      ctx.globalAlpha = Math.min(1, alpha);
      ctx.drawImage(depth < 0.45 ? this.spriteNear : this.spriteFar, sx - size / 2, sy - size / 2, size, size);
    }
    ctx.globalAlpha = 1;

    // ── Orbit rings + comets ──
    for (const ring of this.rings) {
      const rr = 1.28;
      const angleOffset = t * ring.speed * 60 * motionScale + ring.offset;
      const ct = Math.cos(ring.tilt), st = Math.sin(ring.tilt);
      const drawRingPoint = (a, sizeMul, alphaMul) => {
        const px = Math.cos(a) * rr, pz = Math.sin(a) * rr;
        const ty = -pz * st, tz = pz * ct;
        const x1 = px * cosY - tz * sinY;
        const z1 = px * sinY + tz * cosY;
        const y1 = ty * cosX - z1 * sinX;
        const z2 = ty * sinX + z1 * cosX;
        const scale = persp / (persp + z2);
        const depth = (z2 + 1) / 2;
        const alpha = (0.30 - depth * 0.22) * alphaMul;
        if (alpha <= 0.02) return;
        const s = 6 * scale * sizeMul;
        ctx.globalAlpha = Math.min(1, alpha);
        ctx.drawImage(this.spriteNear, this.cx + x1 * R * scale - s / 2, this.cy + y1 * R * scale - s / 2, s, s);
      };
      for (const a0 of ring.points) {
        drawRingPoint(a0 + angleOffset * 0.05, 0.85, 0.6 + b.swirl * 0.8 + b.pulseAmt * 0.3);
      }
      // Comet: a bright head with a fading trail, prominent while thinking.
      if (b.swirl > 0.04) {
        ring.comet += (0.05 + ring.speed * 2) * motionScale;
        for (let k = 0; k < 10; k++) {
          drawRingPoint(ring.comet - k * 0.05, 1.6 - k * 0.12, b.swirl * (1.6 - k * 0.16));
        }
      }
    }
    ctx.globalAlpha = 1;

    // ── Sonar ripples while listening ──
    if (b.expand > 0.15 && t - this._lastRipple > (this.reducedMotion ? 2.4 : 1.1)) {
      this._lastRipple = t;
      this.ripples.push({ born: t });
    }
    for (let i = this.ripples.length - 1; i >= 0; i--) {
      const age = t - this.ripples[i].born;
      if (age > 1.8) { this.ripples.splice(i, 1); continue; }
      const rp = R * (1.05 + age * 0.55);
      const a = (1 - age / 1.8) * 0.20 * Math.max(b.expand, 0.15);
      ctx.strokeStyle = `rgba(${ar},${ag},${ab},${a.toFixed(3)})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.ellipse(this.cx, this.cy, rp, rp * (0.42 + 0.1 * Math.abs(sinX)), 0, 0, 6.2832);
      ctx.stroke();
    }

    // ── Equatorial waveform ring while speaking ──
    if (b.pulseAmt > 0.05) {
      const bars = 56;
      const wr = R * 1.18;
      for (let i = 0; i < bars; i++) {
        const a = (i / bars) * Math.PI * 2 + t * 0.4;
        const amp = (Math.sin(t * 10 + i * 1.7) * 0.5 + 0.5) * (0.05 + this.level * 0.12 + 0.05);
        const inner = wr, outer = wr * (1 + amp * b.pulseAmt);
        const ca = Math.cos(a), sa = Math.sin(a) * 0.42;
        ctx.strokeStyle = `rgba(${ar},${ag},${ab},${(0.22 * b.pulseAmt).toFixed(3)})`;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.moveTo(this.cx + ca * inner, this.cy + sa * inner);
        ctx.lineTo(this.cx + ca * outer, this.cy + sa * outer);
        ctx.stroke();
      }
    }

    // ── Scanline shimmer while thinking ──
    if (b.swirl > 0.05) {
      const scanY = this.cy + Math.sin(t * 2.4) * R * 0.8;
      const grad = ctx.createLinearGradient(this.cx - R, scanY, this.cx + R, scanY);
      grad.addColorStop(0, "rgba(0,0,0,0)");
      grad.addColorStop(0.5, `rgba(${ar},${ag},${ab},${0.20 * b.swirl})`);
      grad.addColorStop(1, "rgba(0,0,0,0)");
      ctx.strokeStyle = grad;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(this.cx - R * 1.15, scanY);
      ctx.lineTo(this.cx + R * 1.15, scanY);
      ctx.stroke();
    }

    ctx.globalCompositeOperation = "source-over";
    requestAnimationFrame(this._frame);
  }
}

window.IrisHologram = Hologram;
