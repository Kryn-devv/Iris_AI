/* IRIS scene — the renderer, the camera, the one animation loop.
 *
 * Ships behind the SAME global name and the SAME three-call API the existing
 * app.js already uses:
 *
 *     new window.IrisHologram(canvas, { accent, quality, reducedMotion })
 *     .setState(name)      .setLevel(0..1)
 *
 * so nothing else in the app has to change. The canvas-2D hologram that used to
 * own that name is captured first and kept as a real fallback: this project's
 * contract is that it boots anywhere, and a machine with no WebGL must still
 * get a working face rather than a blank rectangle.
 *
 * One loop drives everything. Nothing snaps — every visual property eases
 * toward a target, so a state change reads as a living thing settling into a
 * new mood.
 */
(function (global) {
  "use strict";

  var THREE = global.THREE;

  /* Whatever owned this name before us — the canvas-2D hologram. */
  var Fallback = global.IrisHologram || null;

  function webglAvailable() {
    try {
      var probe = document.createElement("canvas");
      return !!(global.WebGLRenderingContext &&
                (probe.getContext("webgl2") || probe.getContext("webgl")));
    } catch (err) {
      return false;
    }
  }

  /* The full-screen canvas lives behind the app chrome but in FRONT of the
   * aurora and grid, so the existing CSS grain and vignette still grade over
   * the 3D — which is what stops it looking pasted on. */
  function ensureCanvas(id) {
    var el = document.getElementById(id);
    if (el) return el;
    el = document.createElement("canvas");
    el.id = id;
    var before = document.querySelector(".bg-noise");
    if (before && before.parentNode) before.parentNode.insertBefore(el, before);
    else document.body.appendChild(el);
    return el;
  }

  /* World-space radius that must stay visible: the orb is radius 1, the
   * outermost orbit sits near 2.4, and the labels hang below that. */


  function IrisScene(legacyCanvas, opts) {
    opts = opts || {};

    this.accent = opts.accent || "#5eead4";
    this.quality = opts.quality || "high";
    this.reducedMotion = !!opts.reducedMotion;
    this.motionScale = this.reducedMotion ? 0.25 : 1.0;

    /* The stage canvas the old hologram drew into is retired, but the node
     * stays: app.js holds a reference to it and constructs against it. */
    if (legacyCanvas && legacyCanvas.style) legacyCanvas.style.display = "none";
    document.body.classList.add("iris-scene-active");

    /* Two canvases, two renderers, background first in DOM order. The
     * background is a full-screen fragment shader and by far the most
     * expensive thing here, but it also drifts slowly enough that half
     * resolution and half framerate are invisible — which is only possible if
     * it is not sharing a renderer with the orb. */
    this.bgCanvas = ensureCanvas("scene-bg");
    this.canvas = ensureCanvas("scene");

    this.bgRenderer = new THREE.WebGLRenderer({
      canvas: this.bgCanvas,
      alpha: false,
      antialias: false,
      powerPreference: "high-performance",
    });
    this.bgRenderer.setClearColor(0x020308, 1);
    this.bgRenderer.toneMapping = THREE.NoToneMapping;

    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      alpha: true,
      antialias: this.quality === "high",
      powerPreference: "high-performance",
    });
    this.renderer.setClearColor(0x000000, 0);
    /* No tone mapping: every material here is additive and hand-tuned, and ACES
     * would desaturate the accent teal the whole app is built around. */
    this.renderer.toneMapping = THREE.NoToneMapping;
    this.maxDpr = this.quality === "high" ? 2 : 1.5;
    /* Performance mode: the background at 60% resolution, every other frame. */
    this.perfMode = false;
    this.bgDprScale = 1.0;
    this._bgTick = 0;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200);
    this.camera.position.set(0, 0, 3.35);

    /* The orb and the constellation share one parent, so both can be anchored
     * to the app's own hologram row and scaled to it. Centring on the viewport
     * instead put the orb straight through the welcome copy and pushed the
     * lowest agent's label behind the input dock. */
    this.focus = new THREE.Group();
    this.scene.add(this.focus);

    this.orb = global.IrisOrb.create({
      accent: this.accent,
      quality: this.quality,
    });
    this.focus.add(this.orb.group);

    this.sky = global.IrisSky.create({
      accent: this.accent,
      mobile: this.quality !== "high",
    });

    this.states = global.IrisStates.create({ accent: this.accent });
    this.audio = global.IrisAudio.create();

    /* The constellation is hidden entirely on small screens: five orbiting
     * labels on a phone is clutter, not information. */
    this.compact = global.matchMedia
      ? global.matchMedia("(max-width: 640px)").matches
      : false;
    this.constellation = global.IrisAgents.create({
      agents: opts.agents || global.IrisAgents.DEFAULT_AGENTS,
      quality: this.quality,
    });
    this.focus.add(this.constellation.group);
    this.constellation.group.visible = !this.compact;

    this.reactions = global.IrisReactions.create({
      scene: this.focus,
      constellation: this.constellation,
      accent: this.accent,
    });

    /* Where the orb sits in 0..1 screen space, so the sky's glow pools behind
     * wherever the orb actually is rather than at the viewport centre. */
    this._orbUv = new THREE.Vector2(0.5, 0.5);
    this.state = "idle";
    this.level = 0;
    this.clock = new THREE.Clock();
    this.elapsed = 0;
    this.running = false;
    this.destroyed = false;

    this._onResize = this._resize.bind(this);
    this._onVisibility = this._visibility.bind(this);
    global.addEventListener("resize", this._onResize);
    document.addEventListener("visibilitychange", this._onVisibility);

    this._resize();
    this._watchStage();
    this._start();
  }

  /* World-space radius that must stay visible: the orb is radius 1, the
   * outermost orbit sits near 2.4, and the labels hang below that. */
  IrisScene.SAFE_REACH = 3.00;

  IrisScene.prototype._resize = function () {
    var w = global.innerWidth || 1;
    var h = global.innerHeight || 1;
    this._w = w;
    this._h = h;
    var dpr = Math.min(global.devicePixelRatio || 1, this.maxDpr);
    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(w, h, false);
    this.bgRenderer.setPixelRatio(dpr * this.bgDprScale);
    this.bgRenderer.setSize(w, h, false);
    this.sky.resize(w, h);
    this.camera.aspect = w / h;
    /* Frame by the constellation's REACH rather than by the orb, so the empty
     * space the sub-agents orbit through is always on screen — on a phone in
     * portrait as much as on a wide monitor. Framing to the orb instead left it
     * filling the viewport with nowhere for anything to orbit. */
    var vFov = (this.camera.fov * Math.PI) / 180;
    var tan = Math.tan(vFov / 2);
    var reach = IrisScene.SAFE_REACH;
    this.camera.position.z = Math.max(reach / tan, reach / (tan * this.camera.aspect)) * 1.04;
    this.camera.updateProjectionMatrix();
    this._layout();
  };

  /* Anchor the orb over the app's hologram row and scale it to that row, so the
   * scene occupies the whole viewport for its background but the orb itself
   * still belongs to the layout it was dropped into. */
  IrisScene.prototype._layout = function () {
    var w = this._w || 1;
    var h = this._h || 1;
    var cx = w / 2;
    var cy = h * 0.42;
    var boxPx = Math.min(w, h) * 0.46;
    var halfPx = boxPx / 2;

    /* Depends only on the camera and the viewport, so it is available before
     * anything that needs to convert a pixel budget into world units. */
    var vFov = (this.camera.fov * Math.PI) / 180;
    var visibleH = 2 * Math.tan(vFov / 2) * this.camera.position.z;
    var worldPerPx = visibleH / h;

    var spreadX = 1.0;

    var stage = document.getElementById("stage") || document.querySelector(".stage");
    if (stage) {
      var rect = stage.getBoundingClientRect();
      /* Guard against a collapsed or not-yet-laid-out row. */
      if (rect.height > 60 && rect.width > 60) {
        cx = rect.left + rect.width / 2;
        cy = rect.top + rect.height / 2;
        boxPx = Math.min(rect.height, rect.width);
        halfPx = rect.height / 2;
        /* The hologram row is much wider than it is tall, so orbits sized to
         * its height leave the sides empty. Stretching X fills the row. */
        spreadX = Math.max(1.0, Math.min(1.70, 0.85 + rect.width / rect.height * 0.16));
      }
    }

    /* The orb is 2 world units across; aim it at a little over half the row. */
    var focusScale = Math.max(0.18, (boxPx * 0.50 * worldPerPx) / 2);

    /* Vertical spread is SOLVED from the space available, not guessed from the
     * row's aspect ratio.
     *
     * A ratio-based guess was wrong in the way that matters: a label hangs
     * BELOW its avatar by a fixed number of pixels and then occupies a few
     * lines of its own, and none of that scales with the row, so the lowest
     * agent printed its name across the welcome copy underneath. Here the
     * label's real height is measured and subtracted, then the remaining
     * pixels are converted into the largest spread that fits:
     *
     *   agentReachPx = R_MAX * spreadY * focusScale / worldPerPx  <=  usablePx
     */
    var labelPx = this._labelReservePx();

    /* The reserve is only needed at the BOTTOM — a label hangs below its
     * avatar, never above it. So instead of shrinking the orbit from both ends,
     * the constellation is biased upward into the empty space over the orb and
     * keeps its height. Subtracting the reserve from both halves was the naive
     * version and it flattened all five paths onto one line, which trades an
     * overlap with the welcome copy for the labels overlapping each other. */
    var bandHalfPx = Math.max(24, (halfPx * 2 - labelPx) / 2);
    var reach = (global.IrisAgents && global.IrisAgents.R_MAX) || 2.58;
    var spreadY = (bandHalfPx * worldPerPx) / (reach * focusScale);
    spreadY = Math.max(0.16, Math.min(1.05, spreadY));

    this._spread = { x: spreadX, y: spreadY };

    /* Lift the constellation by half the reserve so the band it sweeps is
     * centred on the space the labels can actually use. The orb is a sibling
     * and stays exactly where the layout put it. */
    if (this.constellation && this.constellation.group) {
      this.constellation.group.position.y =
        (labelPx / 2) * worldPerPx / Math.max(focusScale, 0.0001);
    }

    /* World position of that screen point on the z = 0 plane. */
    this.focus.position.set(
      (cx - w / 2) * worldPerPx,
      -(cy - h / 2) * worldPerPx,
      0
    );
    this.focus.scale.setScalar(focusScale);

    /* Tell the sky where the orb now is, so its glow pools in the right place. */
    this._orbUv.set(cx / w, 1 - cy / h);
  };

  /* Pixels below an avatar that its label needs: the drop plus the tallest
   * rendered label. Measured rather than assumed, because the label's height
   * depends on the specialty text, the font and the user's zoom — and it is
   * measured once per layout, not per frame. */
  IrisScene.prototype._labelReservePx = function () {
    var maxDrop = (global.IrisAgents && global.IrisAgents.MAX_LABEL_DROP_PX) || 54;
    var tallest = 0;
    var labels = document.querySelectorAll(".iris-agent-label");
    for (var i = 0; i < labels.length; i++) {
      tallest = Math.max(tallest, labels[i].offsetHeight || 0);
    }
    /* Before the labels exist, assume a name plus two lines of specialty. */
    return maxDrop + (tallest || 56);
  };

  /* The stage row changes height when the welcome screen collapses after the
   * first message, so watch it rather than only reacting to window resizes. */
  IrisScene.prototype._watchStage = function () {
    var stage = document.getElementById("stage") || document.querySelector(".stage");
    if (!stage || !global.ResizeObserver) return;
    var self = this;
    this._stageObserver = new global.ResizeObserver(function () { self._layout(); });
    this._stageObserver.observe(stage);
  };

  IrisScene.prototype._visibility = function () {
    if (document.hidden) this._stop();
    else if (!this._pausedByApp) this._start();
  };

  IrisScene.prototype._start = function () {
    if (this.running || this.destroyed || this._pausedByApp) return;
    this.running = true;
    /* Reset the clock so a tab that was hidden for an hour does not resume with
     * a one-hour delta and fling everything across the screen. */
    this.clock.getDelta();
    var self = this;
    var loop = function () {
      if (!self.running) return;
      self._raf = global.requestAnimationFrame(loop);
      self._frame();
    };
    this._raf = global.requestAnimationFrame(loop);
  };

  IrisScene.prototype._stop = function () {
    this.running = false;
    if (this._raf) global.cancelAnimationFrame(this._raf);
    this._raf = null;
  };

  IrisScene.prototype._frame = function () {
    /* Clamped: a stall on a busy machine must not arrive as a huge delta. */
    var dt = Math.min(this.clock.getDelta(), 0.05);
    this.elapsed += dt;

    /* Mood first: the state writes the orb's targets, which the orb then eases
     * toward. Nothing in here ever assigns a visible property directly. */
    this._glow = this.states.apply(this.orb, dt, this.elapsed);

    /* Then the amplitude the orb reacts to, from whichever source is live. */
    var a = this.audio.read(this.elapsed, performance.now(),
                            this.states.currentName(), this.level);
    this.audioSource = a.source;
    this.orb.pushAudio(a.level, a.bass);

    this.orb.update(dt, this.elapsed, this.motionScale);

    var ctx = {
      camera: this.camera,
      width: this._w,
      height: this._h,
      motionScale: this.motionScale,
      reducedMotion: this.reducedMotion,
      hideLabels: this.compact || !this.constellation.group.visible,
      thinking: this.states.isThinking(),
      parent: this.focus,
      parentScale: this.focus.scale.x,
      spread: this._spread,
    };
    /* Reactions first: it writes each agent's flare and dock mix, which the
     * constellation then applies when it places them. Running these the other
     * way round costs a frame of lag on every dispatch. */
    this.reactions.update(dt, this.elapsed, ctx);
    this.constellation.update(dt, this.elapsed, ctx);

    /* The background is told the orb's LIVE colour and how hot it is, so the
     * glow pooled behind the orb is genuinely the orb's own light rather than a
     * separate decoration that happens to be nearby. */
    this._bgTick++;
    var everyOther = this.perfMode && (this._bgTick & 1);
    if (!everyOther) {
      this.sky.update(this.perfMode ? dt * 2 : dt, this.elapsed, {
        motionScale: this.motionScale,
        orbColor: this.orb.colorNow(),
        orbGlow: this._orbGlow(),
        orbUv: this._orbUv,
      });
      this.bgRenderer.render(this.sky.scene, this.sky.camera);
    }

    this.renderer.render(this.scene, this.camera);
  };

  /* How brightly the orb should be lighting the space behind it. Tier 3
   * replaces the constant with something state-driven. */
  IrisScene.prototype._orbGlow = function () {
    return this._glow == null ? 0.20 : this._glow;
  };



  /* ---- agent events ---- */

  /* A beam races out to the agent and back while it flares and pings. */
  IrisScene.prototype.dispatchAgent = function (agentId) {
    return this.reactions.dispatch(agentId);
  };

  /* Steady pulsing halo while it works. If a matching panel exists in the DOM,
   * it also leaves orbit and settles near it. */
  IrisScene.prototype.setAgentBusy = function (agentId, busy, panel) {
    var ok = this.reactions.setBusy(agentId, busy);
    if (!ok) return false;
    var ctx = { camera: this.camera, width: this._w, height: this._h };
    if (busy) {
      var target = panel || this._findPanel(agentId);
      if (target) this.reactions.dock(agentId, target, ctx);
    } else {
      this.reactions.dock(agentId, null, ctx);
    }
    return true;
  };

  /* Convention over configuration: an element carrying data-agent="scout" or
   * id="panel-scout" is that agent's panel. No panel is a normal case — the
   * agent simply pulses in orbit instead. */
  IrisScene.prototype._findPanel = function (agentId) {
    return document.querySelector('[data-agent="' + agentId + '"]') ||
           document.getElementById("panel-" + agentId) ||
           null;
  };

  /* Add an agent to the constellation at runtime, avatar and label generated on
   * the fly, so a capability that appears while the page is open joins the
   * scene without a reload. */
  IrisScene.prototype.addAgent = function (def) {
    if (!def || !def.id || this.constellation.get(def.id)) return false;
    var defs = this.constellation.agents.map(function (a) {
      return { id: a.id, name: a.name, specialty: a.specialty, color: a.color };
    });
    defs.push(def);
    var wasVisible = this.constellation.group.visible;
    this.focus.remove(this.constellation.group);
    this.reactions.dispose();
    this.constellation.dispose();
    this.constellation = global.IrisAgents.create({
      agents: defs, quality: this.quality, avatarBase: this.avatarBase,
    });
    this.constellation.group.visible = wasVisible;
    this.focus.add(this.constellation.group);
    this.reactions = global.IrisReactions.create({
      scene: this.focus, constellation: this.constellation, accent: this.accent,
    });
    return true;
  };

  IrisScene.prototype.agentIds = function () {
    return this.constellation.agents.map(function (a) { return a.id; });
  };

  /* ---- the three-call public API app.js already speaks ---- */

  IrisScene.prototype.setState = function (name) {
    var resolved = this.states.set(name);
    this.state = resolved;
    /* The microphone is asked for the first time listening actually begins —
     * never on page load, so nothing prompts for a mic it has not been told to
     * use. Failure is fine: the synthetic path covers it. */
    if (resolved === "listening" && !this.audio.available() && !this.audio.denied()) {
      this.audio.enable();
    }
    return resolved;
  };

  /* app.js's amplitude hint. Not applied directly any more: it feeds the audio
   * layer as a HINT, which the synthetic generator shapes and the real
   * microphone overrides when it has something better. */
  IrisScene.prototype.setLevel = function (value) {
    this.level = Math.max(0, Math.min(1, value || 0));
  };

  IrisScene.prototype.destroy = function () {
    this.destroyed = true;
    this._stop();
    global.removeEventListener("resize", this._onResize);
    document.removeEventListener("visibilitychange", this._onVisibility);
    if (this._overlayObserver) this._overlayObserver.disconnect();
    if (this._stageObserver) this._stageObserver.disconnect();
    this.audio.disable();
    this.reactions.dispose();
    this.constellation.dispose();
    this.orb.dispose();
    this.sky.dispose();
    this.bgRenderer.dispose();
    if (this.bgRenderer.forceContextLoss) this.bgRenderer.forceContextLoss();
    /* Explicit: leaking a WebGL context is how iOS ends up refusing to create
     * the next one. */
    this.renderer.dispose();
    if (this.renderer.forceContextLoss) this.renderer.forceContextLoss();
    document.body.classList.remove("iris-scene-active");
  };

  /* Why the scene is or is not running, in a form the UI can show the user.
   *
   * This used to be a console.info and nothing else, which is the same silent
   * fallback this project treats as a defect elsewhere: the old flat hologram
   * appears, everything "works", and there is no way to tell a stale install
   * from switched-off WebGL without opening devtools. */
  var status = { active: false, reason: "", detail: "" };

  if (!THREE) {
    status.reason = "three-missing";
    status.detail = "three.js did not load, so the 3D scene cannot start. "
                  + "/static/vendor/three.min.js is missing or was blocked — "
                  + "check that your copy of IRIS is up to date.";
  } else if (!webglAvailable()) {
    status.reason = "no-webgl";
    status.detail = "This browser has WebGL switched off, so the 3D scene "
                  + "cannot start. In Chrome: Settings \u2192 System \u2192 "
                  + "\u201cUse graphics acceleration when available\u201d.";
  } else {
    status.active = true;
    if (global.IrisWiring) global.IrisWiring.install(IrisScene);
    global.IrisHologram = IrisScene;
    global.IrisScene = IrisScene;
  }

  if (!status.active) {
    if (Fallback) {
      console.warn("[iris] " + status.detail + " Falling back to the flat hologram.");
    } else {
      console.warn("[iris] " + status.detail + " No fallback hologram present.");
      status.detail += " There is no fallback either, so the stage is empty.";
    }
  }

  global.IrisSceneStatus = status;
  global.IrisHologramFallback = Fallback;
})(window);
