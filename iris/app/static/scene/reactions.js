/* IRIS scene — Tier 5: dispatch, working, docking, and the rings.
 *
 * These are the moments that make the constellation feel responsive rather than
 * decorative. Everything here is triggered by something real happening — a tool
 * starting, an agent working, the agent thinking — and everything eases.
 *
 *   DISPATCH   a luminous beam races from the orb out to an agent and back,
 *              the agent flares, and an expanding ring pings outward from it.
 *   WORKING    a steady pulsing halo, so a glance at the constellation shows
 *              who is busy.
 *   DOCKING    a working agent leaves orbit and settles near its panel, then
 *              drifts back. In quickly so you notice, out slowly so it is not
 *              abrupt.
 *   RINGS      while the orb is thinking, thin rings orbit just outside it,
 *              each tilted differently, with pulses travelling around them and
 *              the colour drifting from teal toward purple. This is meant to be
 *              the focal point of the thinking state.
 */
(function (global) {
  "use strict";

  var THREE = global.THREE;

  var BEAM_MS = 2400;          /* out and back */
  var PING_MS = 1100;
  var ORB_PING_MS = 850;
  var RING_COUNT = 3;

  /* ───────────────────────────── the beam ───────────────────────────── */

  /* A cylinder rather than a line: a one-pixel line cannot be made to glow, and
   * LineBasicMaterial ignores linewidth on every desktop GL driver. */
  var BEAM_VERT = [
    "varying vec2 vUv;",
    "void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
  ].join("\n");

  var BEAM_FRAG = [
    "uniform vec3 uColor;",
    "uniform float uHead;      // 0..1, where the leading edge has reached",
    "uniform float uStrength;",
    "varying vec2 vUv;",
    "void main(){",
    "  float along = vUv.y;",
    "  // A bright head with a trailing tail, so it reads as travelling rather",
    "  // than as a bar that appears all at once.",
    "  float head = smoothstep(uHead, uHead - 0.06, along);",
    "  float tail = smoothstep(uHead - 0.55, uHead, along);",
    "  float body = head * tail;",
    "  // Fade at the tube's edges so it looks round rather than like a ribbon.",
    "  float edge = 1.0 - abs(vUv.x - 0.5) * 2.0;",
    "  edge = pow(clamp(edge, 0.0, 1.0), 0.55);",
    "  float a = body * edge * uStrength;",
    "  gl_FragColor = vec4(uColor * (0.55 + body * 1.9), a);",
    "}",
  ].join("\n");

  function makeBeam() {
    /* Open-ended unit cylinder along +Y, so scaling y stretches it from origin
     * to target without distorting the cross-section. */
    var geo = new THREE.CylinderGeometry(0.040, 0.026, 1, 10, 1, true);
    geo.translate(0, 0.5, 0);
    var mat = new THREE.ShaderMaterial({
      uniforms: {
        uColor: { value: new THREE.Color(0x5eead4) },
        uHead: { value: 0 },
        uStrength: { value: 0 },
      },
      vertexShader: BEAM_VERT,
      fragmentShader: BEAM_FRAG,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    var mesh = new THREE.Mesh(geo, mat);
    mesh.visible = false;
    mesh.frustumCulled = false;
    return mesh;
  }

  /* ───────────────────────────── the ping ───────────────────────────── */

  function ringTexture() {
    var size = 128;
    var canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    var g = canvas.getContext("2d");
    var c = size / 2;
    g.strokeStyle = "#ffffff";
    g.lineWidth = size * 0.055;
    g.beginPath();
    g.arc(c, c, c * 0.72, 0, Math.PI * 2);
    g.stroke();
    /* A second, softer pass so the ring has a glow rather than a hard hoop. */
    g.globalAlpha = 0.35;
    g.lineWidth = size * 0.16;
    g.stroke();
    var tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }

  /* ───────────────────────── the thinking rings ───────────────────────── */

  var HELIX_VERT = [
    "varying vec2 vUv;",
    "void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
  ].join("\n");

  var HELIX_FRAG = [
    "uniform vec3 uColor;",
    "uniform float uTime;",
    "uniform float uOpacity;",
    "uniform float uSpeed;",
    "uniform float uPulses;",
    "varying vec2 vUv;",
    "void main(){",
    "  // vUv.x runs once around the torus. Pulses are bright bands sliding",
    "  // along it, which is what makes a static ring look like it is carrying",
    "  // something rather than just spinning.",
    "  float phase = fract(vUv.x * uPulses - uTime * uSpeed);",
    "  float pulse = pow(1.0 - abs(phase * 2.0 - 1.0), 6.0);",
    "  float base = 0.30;",
    "  float a = (base + pulse * 1.6) * uOpacity;",
    "  gl_FragColor = vec4(uColor * (0.5 + pulse * 2.4), a);",
    "}",
  ].join("\n");

  function create(opts) {
    opts = opts || {};
    var scene = opts.scene;
    var constellation = opts.constellation;
    var accent = new THREE.Color(opts.accent || "#5eead4");
    var violet = new THREE.Color("#a78bfa");

    var group = new THREE.Group();
    scene.add(group);

    /* One beam per agent, reused — allocating a mesh per dispatch is how a
     * long-running page ends up with a thousand orphaned geometries. */
    var beams = {};
    var pings = [];
    var pingTex = ringTexture();

    /* A small pool of ping sprites, also reused. */
    for (var i = 0; i < 8; i++) {
      var mat = new THREE.SpriteMaterial({
        map: pingTex,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        opacity: 0,
      });
      var sprite = new THREE.Sprite(mat);
      sprite.visible = false;
      group.add(sprite);
      pings.push({ sprite: sprite, t: 0, life: 0, from: 0, to: 1 });
    }

    constellation.agents.forEach(function (a) {
      var beam = makeBeam();
      beam.material.uniforms.uColor.value.set(a.color);
      group.add(beam);
      beams[a.id] = { mesh: beam, t: 0, agent: a };
    });

    /* Tether: a faint straight line held between the orb and a docked agent, so
     * a docked agent still reads as belonging to the orb. */
    var tetherGeo = new THREE.BufferGeometry();
    tetherGeo.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 0, 0, 0], 3));
    var tetherMat = new THREE.LineBasicMaterial({
      color: 0x5eead4, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    var tether = new THREE.Line(tetherGeo, tetherMat);
    tether.frustumCulled = false;
    group.add(tether);

    /* The thinking rings. Radius just outside the orb's halo but inside the
     * innermost orbit, so they never collide with an agent. */
    var rings = [];
    for (var r = 0; r < RING_COUNT; r++) {
      var geo = new THREE.TorusGeometry(1.66 + r * 0.075, 0.0075 + r * 0.0016, 8, 220);
      var uniforms = {
        uColor: { value: accent.clone() },
        uTime: { value: 0 },
        uOpacity: { value: 0 },
        uSpeed: { value: 0.16 + r * 0.085 },
        uPulses: { value: 2 + r },
      };
      var rmat = new THREE.ShaderMaterial({
        uniforms: uniforms,
        vertexShader: HELIX_VERT,
        fragmentShader: HELIX_FRAG,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        side: THREE.DoubleSide,
      });
      var mesh = new THREE.Mesh(geo, rmat);
      /* Each tilted differently, so they read as a nest of orbits rather than
       * as concentric hoops. */
      mesh.rotation.x = 1.15 + r * 0.42;
      mesh.rotation.y = r * 0.71;
      mesh.visible = false;
      group.add(mesh);
      rings.push({
        mesh: mesh, uniforms: uniforms,
        spinX: (0.055 + r * 0.03) * (r % 2 ? -1 : 1),
        spinY: (0.085 - r * 0.02) * (r % 2 ? 1 : -1),
      });
    }
    var ringsMix = 0;

    /* ───────────────────────── the public triggers ───────────────────────── */

    function spawnPing(position, color, from, to, life) {
      /* Take the oldest free slot; if all are busy, steal the most advanced one
       * rather than dropping the ping silently. */
      var slot = null;
      for (var i = 0; i < pings.length; i++) {
        if (pings[i].life <= 0) { slot = pings[i]; break; }
      }
      if (!slot) {
        slot = pings.reduce(function (best, p) {
          return p.t / p.life > best.t / best.life ? p : best;
        }, pings[0]);
      }
      slot.t = 0;
      slot.life = life;
      slot.from = from;
      slot.to = to;
      slot.sprite.position.copy(position);
      slot.sprite.material.color.set(color);
      slot.sprite.visible = true;
    }

    function dispatch(agentId) {
      var b = beams[agentId];
      if (!b) return false;
      b.t = 0.0001;                        /* non-zero marks it as running */
      b.mesh.visible = true;
      /* The "sending" beat, at the orb, at the very start. */
      spawnPing(new THREE.Vector3(0, 0, 0), b.agent.color, 0.55, 1.45, ORB_PING_MS);
      return true;
    }

    function setBusy(agentId, busy) {
      var a = constellation.get(agentId);
      if (!a) return false;
      a.busy = !!busy;
      if (!busy) a.dock = null;
      return true;
    }

    /* target: a DOM element, {x,y} in screen pixels, or null to undock. */
    function dock(agentId, target, ctx) {
      var a = constellation.get(agentId);
      if (!a) return false;
      if (!target) { a.dock = null; return true; }
      var x, y;
      if (target.getBoundingClientRect) {
        var rect = target.getBoundingClientRect();
        if (!rect.width && !rect.height) { a.dock = null; return false; }
        x = rect.left + rect.width / 2;
        y = rect.top + rect.height / 2;
      } else {
        x = target.x; y = target.y;
      }
      a.dock = screenToWorld(x, y, ctx);
      return true;
    }

    /* Screen pixels to a world point on the orb's own z-plane, so a docked
     * agent lands visually on the panel rather than in front of or behind it. */
    var _v = new THREE.Vector3();
    function screenToWorld(px, py, ctx) {
      var camera = ctx.camera;
      _v.set((px / ctx.width) * 2 - 1, -(py / ctx.height) * 2 + 1, 0.5);
      _v.unproject(camera);
      _v.sub(camera.position).normalize();
      var distance = -camera.position.z / _v.z;
      return camera.position.clone().add(_v.multiplyScalar(distance));
    }

    /* ───────────────────────────── the frame ───────────────────────────── */

    var up = new THREE.Vector3(0, 1, 0);
    var dir = new THREE.Vector3();
    var quat = new THREE.Quaternion();

    function update(dt, t, ctx) {
      var dtMs = dt * 1000;

      /* Beams: out over the first half, back over the second. The flare peaks
       * in the middle and eases out, so the whole gesture has one arc. */
      Object.keys(beams).forEach(function (id) {
        var b = beams[id];
        if (b.t <= 0) return;
        b.t += dtMs;
        var k = b.t / BEAM_MS;
        if (k >= 1) {
          b.t = 0;
          b.mesh.visible = false;
          b.mesh.material.uniforms.uStrength.value = 0;
          b.agent.flare = 0;
          return;
        }

        var a = b.agent;
        var len = a.pos.length() || 0.0001;
        dir.copy(a.pos).normalize();
        quat.setFromUnitVectors(up, dir);
        b.mesh.quaternion.copy(quat);
        b.mesh.scale.set(1, len, 1);
        b.mesh.position.set(0, 0, 0);

        /* Head races out to the agent, then retreats. */
        var head = k < 0.5 ? (k / 0.5) : 1.0;
        var strength = k < 0.5
          ? Math.min(1, k / 0.18)
          : Math.max(0, 1 - (k - 0.5) / 0.5);
        b.mesh.material.uniforms.uHead.value = head;
        b.mesh.material.uniforms.uStrength.value = strength * 0.95;

        /* Flare: a smooth hump centred on the beam's arrival. */
        var hump = Math.sin(Math.min(1, k / 0.85) * Math.PI);
        a.flare = Math.max(a.flare, hump);
        if (a.flare > 0 && k > 0.5) a.flare = hump;

        /* The sonar ping at the agent, once, at the peak. */
        if (!b.pinged && k >= 0.48) {
          b.pinged = true;
          spawnPing(a.pos, a.color, 0.30, 1.55, PING_MS);
        }
        if (k < 0.4) b.pinged = false;
      });

      /* Pings: scale outward and fade. */
      for (var i = 0; i < pings.length; i++) {
        var p = pings[i];
        if (p.life <= 0) continue;
        p.t += dtMs;
        var pk = p.t / p.life;
        if (pk >= 1) {
          p.life = 0;
          p.sprite.visible = false;
          p.sprite.material.opacity = 0;
          continue;
        }
        var eased = 1 - Math.pow(1 - pk, 2.2);
        p.sprite.scale.setScalar(p.from + (p.to - p.from) * eased);
        p.sprite.material.opacity = (1 - pk) * 0.75;
      }

      /* Docking: in quickly, out slowly. Asymmetry is the whole point — a
       * snappy arrival draws the eye, a slow return does not yank it back. */
      var docked = null;
      constellation.agents.forEach(function (a) {
        var wants = a.dock ? 1 : 0;
        var rate = wants ? 0.085 : 0.028;
        a.dockMix += (wants - a.dockMix) * (1 - Math.pow(1 - rate, dt * 60));
        if (a.dockMix > 0.35 && a.dock) docked = a;
      });

      if (docked) {
        var pos = tetherGeo.attributes.position;
        pos.setXYZ(0, 0, 0, 0);
        pos.setXYZ(1, docked.pos.x, docked.pos.y, docked.pos.z);
        pos.needsUpdate = true;
        tetherMat.color.set(docked.color);
        tetherMat.opacity = 0.16 * docked.dockMix;
      } else {
        tetherMat.opacity = 0;
      }

      /* The thinking rings fade in and out with the state rather than popping,
       * and drift teal -> violet while they are up. */
      var wantRings = ctx.thinking ? 1 : 0;
      ringsMix += (wantRings - ringsMix) * (1 - Math.pow(1 - 0.035, dt * 60));
      var visible = ringsMix > 0.004;
      var hue = 0.5 + 0.5 * Math.sin(t * 0.34);
      for (var r2 = 0; r2 < rings.length; r2++) {
        var ring = rings[r2];
        ring.mesh.visible = visible;
        if (!visible) continue;
        ring.uniforms.uTime.value = t;
        ring.uniforms.uOpacity.value = ringsMix * (0.85 - r2 * 0.12);
        ring.uniforms.uColor.value.copy(accent).lerp(violet, hue * (0.45 + r2 * 0.22));
        ring.mesh.rotation.x += dt * ring.spinX * ctx.motionScale;
        ring.mesh.rotation.y += dt * ring.spinY * ctx.motionScale;
        ring.mesh.rotation.z += dt * 0.02 * ctx.motionScale;
      }
    }

    function dispose() {
      Object.keys(beams).forEach(function (id) {
        beams[id].mesh.geometry.dispose();
        beams[id].mesh.material.dispose();
      });
      pings.forEach(function (p) { p.sprite.material.dispose(); });
      pingTex.dispose();
      tetherGeo.dispose(); tetherMat.dispose();
      rings.forEach(function (r) { r.mesh.geometry.dispose(); r.mesh.material.dispose(); });
      if (group.parent) group.parent.remove(group);
    }

    return {
      group: group,
      dispatch: dispatch,
      setBusy: setBusy,
      dock: dock,
      update: update,
      dispose: dispose,
      ringsMix: function () { return ringsMix; },
      activeBeams: function () {
        return Object.keys(beams).filter(function (k) { return beams[k].t > 0; });
      },
    };
  }

  global.IrisReactions = { create: create };
})(window);
