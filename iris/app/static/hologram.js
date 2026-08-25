/* IRIS Hologram — a 3D particle sphere rendered on a 2D canvas.
 *
 * No WebGL, no libraries: ~900 points on a fibonacci sphere, rotated in 3D,
 * projected with perspective, drawn with additive glow. The sphere reacts to
 * the assistant state machine:
 *   idle      — slow drift
 *   listening — expands, brightens, ripples with mic level
 *   thinking  — fast axial swirl with orbit trails
 *   speaking  — rhythmic pulse
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
    this.speedY = 0.0028;
    this.pulse = 0;
    this.time = 0;
    this.stateBlend = { expand: 0, swirl: 0, pulseAmt: 0 };

    const counts = { low: 320, medium: 620, high: 950 };
    this.pointCount = counts[this.quality] || 950;
    this.ringCount = 3;

    this._buildGeometry();
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
        size: 0.6 + Math.random() * 1.1,
      });
    }
    // Orbit ring particles (electron-style accents).
    this.rings = [];
    for (let r = 0; r < this.ringCount; r++) {
      const tilt = (Math.PI / this.ringCount) * r + 0.4;
      const speed = 0.012 + r * 0.004;
      const ringPoints = [];
      const cnt = this.quality === "low" ? 24 : 42;
      for (let i = 0; i < cnt; i++) {
        ringPoints.push((Math.PI * 2 * i) / cnt);
      }
      this.rings.push({ tilt, speed, offset: Math.random() * 6, points: ringPoints });
    }
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
  }

  destroy() {
    this.running = false;
    window.removeEventListener("resize", this._resize);
  }

  _accentRGB() {
    const c = this.accent.replace("#", "");
    return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
  }

  _frame() {
    if (!this.running) return;
    const ctx = this.ctx;
    const w = this.canvas.getBoundingClientRect().width;
    const h = this.canvas.getBoundingClientRect().height;
    this.time += 1 / 60;

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
    this.speedY = (0.0028 + b.swirl * 0.028 + b.pulseAmt * 0.004) * motionScale;
    this.rotY += this.speedY;
    this.rotX = 0.35 + Math.sin(this.time * 0.21) * 0.07 * motionScale;

    this.pulse = b.pulseAmt * (0.5 + 0.5 * Math.sin(this.time * 9)) * 0.12 +
                 b.expand * (0.10 + this.level * 0.22);

    const R = this.baseR * (1 + this.pulse);
    const [ar, ag, ab] = this._accentRGB();

    ctx.clearRect(0, 0, w, h);

    // Ambient core glow.
    const glow = ctx.createRadialGradient(this.cx, this.cy, R * 0.05, this.cx, this.cy, R * 1.9);
    const coreAlpha = 0.10 + b.expand * 0.10 + b.pulseAmt * 0.08 + b.swirl * 0.05;
    glow.addColorStop(0, `rgba(${ar},${ag},${ab},${coreAlpha})`);
    glow.addColorStop(0.55, `rgba(${ar},${ag},${ab},${coreAlpha * 0.25})`);
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);

    ctx.globalCompositeOperation = "lighter";

    const sinY = Math.sin(this.rotY), cosY = Math.cos(this.rotY);
    const sinX = Math.sin(this.rotX), cosX = Math.cos(this.rotX);
    const persp = 3.2;
    const jitterAmt = b.swirl * 0.05 + b.expand * this.level * 0.06;

    // Sphere particles.
    for (let i = 0; i < this.points.length; i++) {
      const p = this.points[i];
      let px = p.x, py = p.y, pz = p.z;

      if (jitterAmt > 0.002) {
        const j = Math.sin(this.time * 3 + p.jitter) * jitterAmt;
        px *= 1 + j; py *= 1 + j; pz *= 1 + j;
      }

      // Rotate Y then X.
      let x1 = px * cosY - pz * sinY;
      let z1 = px * sinY + pz * cosY;
      let y1 = py * cosX - z1 * sinX;
      let z2 = py * sinX + z1 * cosX;

      const scale = persp / (persp + z2);
      const sx = this.cx + x1 * R * scale;
      const sy = this.cy + y1 * R * scale;

      const depth = (z2 + 1) / 2;               // 0 near .. 1 far
      const alpha = 0.72 - depth * 0.58 + b.expand * 0.1;
      if (alpha <= 0.02) continue;
      const size = p.size * scale * (1 + b.pulseAmt * 0.3);

      ctx.fillStyle = `rgba(${ar},${ag},${ab},${alpha.toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(sx, sy, size, 0, 6.2832);
      ctx.fill();
    }

    // Orbit rings.
    for (const ring of this.rings) {
      const rr = R * 1.28;
      const angleOffset = this.time * ring.speed * 60 * motionScale + ring.offset;
      for (const a0 of ring.points) {
        const a = a0 + angleOffset * 0.05;
        let px = Math.cos(a) * rr / R, py = 0, pz = Math.sin(a) * rr / R;
        // Tilt ring.
        const ct = Math.cos(ring.tilt), st = Math.sin(ring.tilt);
        const ty = py * ct - pz * st;
        const tz = py * st + pz * ct;
        // Scene rotation.
        let x1 = px * cosY - tz * sinY;
        let z1 = px * sinY + tz * cosY;
        let y1 = ty * cosX - z1 * sinX;
        let z2 = ty * sinX + z1 * cosX;
        const scale = persp / (persp + z2);
        const sx = this.cx + x1 * R * scale;
        const sy = this.cy + y1 * R * scale;
        const depth = (z2 + 1) / 2;
        const alpha = (0.30 - depth * 0.22) * (0.6 + b.swirl * 0.8 + b.pulseAmt * 0.3);
        if (alpha <= 0.02) continue;
        ctx.fillStyle = `rgba(${ar},${ag},${ab},${alpha.toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(sx, sy, 0.9 * scale, 0, 6.2832);
        ctx.fill();
      }
    }

    // Equator scanline shimmer while thinking.
    if (b.swirl > 0.05) {
      const scanY = this.cy + Math.sin(this.time * 2.4) * R * 0.8;
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
