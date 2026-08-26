/* IRIS scene — Tier 4: the floating sub-agent constellation.
 *
 * Each sub-agent is a small glowing avatar orbiting the orb on its own gently
 * tilted path, with its name and specialty labelled just beneath it in plain
 * HTML that follows it around the screen.
 *
 * Three details do the work of making this read as three-dimensional rather
 * than as stickers floating on glass:
 *
 *   1. Every orbit gets a different radius, speed, starting angle and tilt
 *      DIRECTION, so the paths sit in different planes. Matched orbits make
 *      five agents move in lockstep and instantly look fake.
 *   2. Labels fade with depth and vanish entirely while their agent is directly
 *      behind the orb, and they stack by depth so a near name sits over a far
 *      one.
 *   3. Each avatar breathes on its own phase, so the constellation is alive
 *      even when nothing is happening.
 *
 * Avatars use a three-step fallback — explicit image, then a naming
 * convention, then one drawn on the fly — so a brand-new agent looks finished
 * with zero art, and a cold load never shows a broken-image box.
 */
(function (global) {
  "use strict";

  var THREE = global.THREE;

  /* Orbits live in the band outside the orb's halo (1.55) and clear of the
   * processing rings Tier 5 puts at ~1.7. */
  var R_MIN = 2.22;
  var R_MAX = 2.58;
  /* labelDrop spans 0.30..0.62, and `drop` below maps that to 22..41 px. */
  var MAX_LABEL_DROP_PX = 22 + (0.62 - 0.30) * 60;

  var LABEL_CSS = [
    ".iris-labels{position:fixed;inset:0;z-index:2;pointer-events:none;overflow:hidden}",
    ".iris-agent-label{position:absolute;left:0;top:0;transform:translate(-50%,0);",
    "  pointer-events:auto;text-align:center;line-height:1.25;white-space:nowrap;",
    "  opacity:0;visibility:hidden;transition:opacity .18s linear;",
    "  font-family:'Space Grotesk',Inter,system-ui,sans-serif;user-select:none}",
    ".iris-agent-label .n{display:block;font-size:11.5px;font-weight:600;letter-spacing:.11em;",
    "  text-transform:uppercase;text-shadow:0 1px 6px rgba(2,3,8,.95),0 0 2px rgba(2,3,8,1)}",
    ".iris-agent-label .s{display:block;font-size:10px;color:#9aa4bc;margin-top:2px;",
    "  max-width:132px;white-space:normal;font-family:Inter,system-ui,sans-serif;",
    "  text-shadow:0 1px 5px rgba(2,3,8,.95)}",
    ".iris-agent-label.busy .n{text-shadow:0 0 12px currentColor}",
    "@media (max-width:640px){.iris-labels{display:none}}",
  ].join("\n");

  function injectCss() {
    if (document.getElementById("iris-labels-css")) return;
    var el = document.createElement("style");
    el.id = "iris-labels-css";
    el.textContent = LABEL_CSS;
    document.head.appendChild(el);
  }

  function rgba(hex, a) {
    var c = new THREE.Color(hex);
    return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + "," +
           Math.round(c.b * 255) + "," + a + ")";
  }

  /* Drawn, not loaded: a soft halo, a disc fading from a dark core out to the
   * agent's accent, a thin rim, and its initial. This is the reason a new agent
   * appears polished the instant it is added, with no asset pipeline at all. */
  function drawAvatar(agent, size) {
    var canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    var g = canvas.getContext("2d");
    var c = size / 2;
    var color = agent.color;

    var halo = g.createRadialGradient(c, c, size * 0.16, c, c, size * 0.5);
    halo.addColorStop(0, rgba(color, 0.42));
    halo.addColorStop(0.55, rgba(color, 0.12));
    halo.addColorStop(1, rgba(color, 0));
    g.fillStyle = halo;
    g.fillRect(0, 0, size, size);

    var r = size * 0.29;
    var disc = g.createRadialGradient(c, c - r * 0.25, r * 0.1, c, c, r);
    disc.addColorStop(0, "#070c18");
    disc.addColorStop(0.52, rgba(color, 0.22));
    disc.addColorStop(1, rgba(color, 0.80));
    g.beginPath();
    g.arc(c, c, r, 0, Math.PI * 2);
    g.fillStyle = disc;
    g.fill();

    g.lineWidth = Math.max(1, size * 0.014);
    g.strokeStyle = rgba(color, 0.95);
    g.stroke();

    g.fillStyle = "#eaf2ff";
    g.font = "600 " + Math.round(size * 0.215) + "px 'Space Grotesk', Inter, system-ui, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    var glyph = agent.initial || (agent.name || "?").charAt(0).toUpperCase();
    if (glyph.length > 1) {
      g.font = "600 " + Math.round(size * 0.155) + "px 'Space Grotesk', Inter, system-ui, sans-serif";
    }
    g.fillText(glyph, c, c + size * 0.012);

    var tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }

  /* A separate soft radial sprite behind each avatar, so it reads as a little
   * light source in the scene rather than a decal pasted on the sky. */
  function glowTexture(color) {
    var size = 128;
    var canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    var g = canvas.getContext("2d");
    var c = size / 2;
    var grad = g.createRadialGradient(c, c, 0, c, c, c);
    grad.addColorStop(0, rgba(color, 0.85));
    grad.addColorStop(0.35, rgba(color, 0.30));
    grad.addColorStop(1, rgba(color, 0));
    g.fillStyle = grad;
    g.fillRect(0, 0, size, size);
    var tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }

  /* Three steps, in this order:
   *   1. an explicit image, if the agent supplies one
   *   2. a naming convention, IF a base path is configured
   *   3. one drawn on the fly
   *
   * Step 3 runs FIRST and immediately, and an image that loads later upgrades
   * over it. That ordering matters twice: a cold load is finished-looking with
   * no pop-in and no broken-image box, and probing the convention path is
   * opt-in — otherwise every agent logs a 404 on a repo that ships no art,
   * which is console noise that trains you to ignore the console.
   *
   * Set opts.avatarBase (e.g. "/static/agents/") once you have real avatars. */
  function resolveAvatar(agent, size, avatarBase, onReady) {
    onReady(drawAvatar(agent, size), "drawn");

    var candidates = [];
    if (agent.avatar) candidates.push(agent.avatar);
    if (avatarBase) candidates.push(avatarBase + agent.id + ".png");
    if (!candidates.length) return;

    var index = 0;
    function attempt() {
      if (index >= candidates.length) return;
      var url = candidates[index++];
      var img = new Image();
      img.onload = function () {
        var tex = new THREE.Texture(img);
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.needsUpdate = true;
        onReady(tex, url);
      };
      img.onerror = attempt;
      img.src = url;
    }
    attempt();
  }

  function create(opts) {
    opts = opts || {};
    injectCss();

    var list = opts.agents || [];
    var quality = opts.quality || "high";
    var size = quality === "high" ? 256 : 160;

    var group = new THREE.Group();
    var labelHost = document.getElementById("iris-labels");
    if (!labelHost) {
      labelHost = document.createElement("div");
      labelHost.id = "iris-labels";
      labelHost.className = "iris-labels";
      document.body.appendChild(labelHost);
    }

    /* Disambiguate initials up front: "Scribe" and "Sentinel" both drawn as a
     * bare "S" is a worse label than no label — and "Scout" and "Scribe" both
     * shorten to "Sc", so one extra letter is not enough either. Grow the
     * prefix until every glyph is unique. */
    function uniqueInitials(defs) {
      var names = defs.map(function (d) { return (d.name || d.id || "?"); });
      for (var len = 1; len <= 3; len++) {
        var glyphs = names.map(function (n) {
          return n.slice(0, len).charAt(0).toUpperCase() + n.slice(1, len);
        });
        var seen = {};
        var clash = glyphs.some(function (g) {
          if (seen[g]) return true;
          seen[g] = true;
          return false;
        });
        if (!clash) return glyphs;
      }
      /* Still ambiguous at three letters: number them rather than lie. */
      return names.map(function (n, i) { return String(i + 1); });
    }
    var initials = uniqueInitials(list);

    var agents = list.map(function (def, i) {
      var count = Math.max(list.length, 1);
      /* Golden-angle phases rather than an even split: an even split lines the
       * agents up into a visible ring every time the orbits happen to align. */
      var phase = (i * 2.39996) % (Math.PI * 2);
      var radius = R_MIN + ((i * 0.618) % 1) * (R_MAX - R_MIN);
      var speed = (0.070 + ((i * 0.37) % 1) * 0.055) * (i % 2 ? -1 : 1);
      /* Each path is an ELLIPSE in the screen plane, rolled to its own angle,
       * with depth added as a separate slower wobble.
       *
       * A true 3D circle was the obvious first choice and it reads badly: an
       * agent at the near or far point of its circle projects right onto the
       * orb's centre, so the constellation spends most of its time bunched over
       * the orb with its labels colliding. Keeping the sweep in the screen
       * plane means an agent's distance from the orb barely changes, while the
       * depth wobble still gives real parallax and the front/behind fade. */
      var squash = 0.46 + ((i * 0.43) % 1) * 0.34;
      var roll = (i * 2.39996 * 0.5) % Math.PI;
      var zDepth = (0.34 + ((i * 0.53) % 1) * 0.46) * (i % 2 ? -1 : 1);
      var zPhase = (i * 1.13) % (Math.PI * 2);

      var rawName = def.name || def.id || "?";
      var agent = {
        id: def.id,
        idx: i,
        name: rawName,
        initial: initials[i],
        specialty: def.specialty || "",
        color: def.color || "#5eead4",
        orbit: {
          radius: radius, speed: speed, phase: phase,
          squash: squash, roll: roll, zDepth: zDepth, zPhase: zPhase,
        },
        /* Tier 5 writes these. */
        flare: 0, busy: false, dock: null, dockMix: 0,
        pos: new THREE.Vector3(),
        breathePhase: (i * 1.7) % (Math.PI * 2),
        /* Staggered so two agents that drift close together do not print their
         * names on the same line. */
        labelDrop: 0.30 + (i % 3) * 0.16,
      };

      var glowMat = new THREE.SpriteMaterial({
        map: glowTexture(agent.color),
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        depthTest: false,
      });
      agent.glow = new THREE.Sprite(glowMat);
      agent.glow.scale.setScalar(0.86);
      group.add(agent.glow);

      var mat = new THREE.SpriteMaterial({
        transparent: true,
        depthWrite: false,
        depthTest: false,
        opacity: 0,
      });
      agent.sprite = new THREE.Sprite(mat);
      agent.sprite.scale.setScalar(0.40);
      group.add(agent.sprite);

      resolveAvatar(agent, size, opts.avatarBase, function (tex, from) {
        if (mat.map && mat.map !== tex) mat.map.dispose();
        mat.map = tex;
        mat.needsUpdate = true;
        agent.avatarFrom = from;
      });

      var label = document.createElement("div");
      label.className = "iris-agent-label";
      label.innerHTML = "<span class='n'></span><span class='s'></span>";
      label.querySelector(".n").textContent = agent.name;
      label.querySelector(".n").style.color = agent.color;
      label.querySelector(".s").textContent = agent.specialty;
      label.title = agent.name + " — " + agent.specialty;
      labelHost.appendChild(label);
      agent.label = label;
      /* Hidden until its real screen position has been computed once, so no
       * label ever flashes in the top-left corner on load. */
      agent.placed = false;

      return agent;
    });

    var byId = {};
    agents.forEach(function (a) { byId[a.id] = a; });

    var ndc = new THREE.Vector3();
    var orbNdc = new THREE.Vector3();
    var worldPos = new THREE.Vector3();
    var orbEdge = new THREE.Vector3();

    function update(dt, t, ctx) {
      var camera = ctx.camera;
      var w = ctx.width;
      var h = ctx.height;
      var motion = ctx.motionScale == null ? 1 : ctx.motionScale;
      var hidden = ctx.hideLabels;

      /* The orb's own screen footprint, so we know when an agent is passing
       * behind it — measured through the parent's transform, because the whole
       * constellation is anchored and scaled onto the app's stage row. Reading
       * these in local space put every label a long way from its avatar. */
      var parent = ctx.parent || group;
      var scale = ctx.parentScale || 1;
      orbNdc.set(0, 0, 0);
      parent.localToWorld(orbNdc);
      orbNdc.project(camera);
      var orbSx = (orbNdc.x * 0.5 + 0.5) * w;
      var orbSy = (-orbNdc.y * 0.5 + 0.5) * h;
      orbEdge.set(1, 0, 0);
      parent.localToWorld(orbEdge);
      orbEdge.project(camera);
      var orbPx = Math.abs((orbEdge.x * 0.5 + 0.5) * w - orbSx) || 1;

      /* Avatars keep a constant SCREEN size rather than shrinking with the
       * stage: an avatar is a readable UI element, not part of the orb. */
      var counter = 1 / Math.max(scale, 0.0001) * 0.34;

      for (var i = 0; i < agents.length; i++) {
        var a = agents[i];
        var o = a.orbit;

        /* Reduced motion: hold each agent at its starting position rather than
         * stopping mid-orbit, so the constellation still reads as arranged. */
        var angle = ctx.reducedMotion ? o.phase : o.phase + t * o.speed * Math.PI * 2 * motion;

        var spread = ctx.spread || { x: 1, y: 1 };
        var ex = Math.cos(angle) * o.radius;
        var ey = Math.sin(angle) * o.radius * o.squash;
        /* Roll the ellipse into its own plane, so five paths never look like
         * five copies of one path. */
        var cr = Math.cos(o.roll), sr = Math.sin(o.roll);
        var rx = ex * cr - ey * sr;
        var ry = ex * sr + ey * cr;
        /* Spread is applied AFTER the roll, and that order is the whole point.
         * Applied before, a path rolled near 90 degrees turns the wide X
         * stretch into its VERTICAL extent — so widening the constellation to
         * fit a letterbox row made one agent swing far below the row and print
         * its name across the welcome copy. Scaling the rolled path bounds the
         * vertical reach by spread.y no matter how a path is rolled. */
        a.pos.set(
          rx * spread.x,
          ry * spread.y,
          Math.sin(angle + o.zPhase) * o.zDepth
        );

        if (a.dock && a.dockMix > 0) {
          a.pos.lerp(a.dock, a.dockMix);
        }

        a.sprite.position.copy(a.pos);
        a.glow.position.copy(a.pos);

        /* Breathing, plus whatever Tier 5's flare and busy pulse add. */
        var breathe = 1 + Math.sin(t * 1.35 + a.breathePhase) * 0.075 * motion;
        var busyPulse = a.busy ? 1 + Math.sin(t * 4.2 + a.breathePhase) * 0.14 : 1;
        var flareScale = 1 + a.flare * 0.55;
        a.sprite.scale.setScalar(counter * breathe * flareScale * (a.dockMix > 0.5 ? 1.06 : 1));
        a.glow.scale.setScalar(counter * 2.15 * breathe * busyPulse * (1 + a.flare * 0.85));
        a.glow.material.opacity = 0.42 + a.flare * 0.55 + (a.busy ? 0.20 : 0);

        /* World position, so the projection survives the parent transform. */
        worldPos.copy(a.pos);
        parent.localToWorld(worldPos);
        ndc.copy(worldPos).project(camera);
        var sx = (ndc.x * 0.5 + 0.5) * w;
        var sy2 = (-ndc.y * 0.5 + 0.5) * h;
        /* 0 nearest the camera, 1 furthest. Taken from world z against the
         * orbit's own depth range: NDC z barely moves across a band this
         * shallow, so it cannot drive a visible fade. */
        var span = Math.abs(o.zDepth) || 1;
        var depth = THREE.MathUtils.clamp((span - a.pos.z) / (span * 2), 0, 1);
        var behindOrb = a.pos.z < 0 &&
          Math.hypot(sx - orbSx, sy2 - orbSy) < orbPx * 0.92;

        a.sprite.material.opacity = (a.sprite.material.map ? 1 : 0) *
          (behindOrb ? 0.22 : 1 - depth * 0.55);

        if (hidden) {
          if (a.label.style.visibility !== "hidden") a.label.style.visibility = "hidden";
          continue;
        }

        var labelOpacity = behindOrb ? 0 : 1 - depth * 0.80;
        var drop = 22 + (a.labelDrop - 0.30) * 60;
        var lx = Math.round(sx), ly = Math.round(sy2 + drop);
        a.label.style.transform = "translate(" + lx + "px," + ly +
          "px) translate(-50%,0)";
        /* Near agents' names sit over far ones'. */
        a.label.style.zIndex = String(1000 - Math.round(depth * 900));

        /* Remember where this one landed so the declutter pass below can see
         * which names are printing on top of each other this frame. */
        a.lx = lx; a.ly = ly; a.labelOpacity = labelOpacity; a.depth = depth;
        a.label.style.opacity = labelOpacity.toFixed(3);
        if (!a.placed) {
          a.placed = true;
          a.label.style.visibility = "visible";
        } else if (a.label.style.visibility === "hidden") {
          a.label.style.visibility = "visible";
        }
        if (a.busy) a.label.classList.add("busy");
        else a.label.classList.remove("busy");
      }

      declutter();
    }

    /* Five independent orbits will sometimes bring two agents close together,
     * and two names printed across each other are worse than one name. When
     * that happens the FURTHER label fades out and the nearer one stays fully
     * readable — which is the same rule the depth fade already follows, so it
     * reads as depth rather than as flicker.
     *
     * Measured against the labels' own boxes rather than a fixed radius: the
     * names are different lengths, so "Relay" and "Sentinel" collide at very
     * different distances. */
    function declutter() {
      var vis = [];
      for (var i = 0; i < agents.length; i++) {
        var a = agents[i];
        if (a.labelOpacity > 0.08 && a.label.offsetWidth) vis.push(a);
      }
      /* Nearest first, so a label only ever yields to something in front —
       * with depth QUANTIZED and ties broken by a fixed index.
       *
       * Sorting on raw depth was unstable exactly where it mattered: two
       * agents passing each other sit at near-identical depth, the order
       * flipped every frame, and the pair took turns yielding. Both names then
       * flickered at half opacity instead of one staying readable. */
      vis.sort(function (p, q) {
        return (Math.round(p.depth * 20) - Math.round(q.depth * 20)) || (p.idx - q.idx);
      });

      for (var j = 0; j < vis.length; j++) {
        var b = vis[j];
        var fade = 0;
        for (var k = 0; k < j; k++) {
          var front = vis[k];
          /* No check for whether `front` is itself faded. Reading that back
           * from the previous frame made a pair oscillate — A hid B, then B
           * saw A "hidden" and stopped yielding, so both came back solid and
           * the collision never resolved. Sorting nearest-first already gives
           * a stable total order: a label yields to anything in front of it,
           * full stop. */
          var halfW = (b.label.offsetWidth + front.label.offsetWidth) / 2;
          var dx = Math.abs(b.lx - front.lx);
          var dy = Math.abs(b.ly - front.ly);
          var maxH = Math.max(b.label.offsetHeight, front.label.offsetHeight);
          if (dx < halfW && dy < maxH) {
            /* Ease over the last third of the approach so it dissolves. */
            var closeness = 1 - Math.max(dx / Math.max(halfW, 1), dy / Math.max(maxH, 1));
            fade = Math.max(fade, Math.min(1, closeness / 0.18));
          }
        }
        if (fade > 0) {
          b.label.style.opacity = (b.labelOpacity * (1 - fade)).toFixed(3);
        }
      }
    }

    function dispose() {
      agents.forEach(function (a) {
        if (a.sprite.material.map) a.sprite.material.map.dispose();
        a.sprite.material.dispose();
        a.glow.material.map.dispose();
        a.glow.material.dispose();
        if (a.label.parentNode) a.label.parentNode.removeChild(a.label);
      });
    }

    return {
      group: group,
      agents: agents,
      get: function (id) { return byId[id] || null; },
      update: update,
      dispose: dispose,
      drawAvatar: drawAvatar,
      glowTexture: glowTexture,
      resolveAvatar: resolveAvatar,
      R_MIN: R_MIN,
      R_MAX: R_MAX,
      /* Largest value `drop` can take below, so the layout can reserve it. */
      MAX_LABEL_DROP_PX: MAX_LABEL_DROP_PX,
    };
  }

  /* The five specialists, mapped to what IRIS can actually do. Colours are cool
   * except Sentinel, which is warm because it is the group holding the risky
   * commands — the warmth means something. */
  var DEFAULT_AGENTS = [
    { id: "operator", name: "Operator", color: "#5eead4",
      specialty: "Drives the desktop — apps, windows, screen, media" },
    { id: "scout", name: "Scout", color: "#22d3ee",
      specialty: "Answers from the open web, and does the maths" },
    { id: "scribe", name: "Scribe", color: "#a78bfa",
      specialty: "Reads, writes and files — docs, decks, sheets, code" },
    { id: "relay", name: "Relay", color: "#818cf8",
      specialty: "Timers, switches, motors, sensors, the robot face" },
    { id: "sentinel", name: "Sentinel", color: "#f4a259",
      specialty: "Watches the machine, holds the risky commands" },
  ];

  global.IrisAgents = { create: create, DEFAULT_AGENTS: DEFAULT_AGENTS };
})(window);
