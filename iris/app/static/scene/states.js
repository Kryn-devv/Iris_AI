/* IRIS scene — Tier 3a: the conversational moods.
 *
 * Each state is a target "mood" — a full set of orb parameters. Nothing is
 * applied directly; everything is handed to the orb as a TARGET and eased
 * toward, so switching state reads as the orb settling into a new temperament
 * rather than a cut. A change lands visibly in about a second.
 *
 * The palette is deliberately cool everywhere except one place. LISTENING is
 * warm amber, and it is the only warm state, so "I am recording you" is
 * unmistakable in the corner of your eye without reading a label.
 */
(function (global) {
  "use strict";

  var ACCENT = "#5eead4";     /* --accent, the app's teal */
  var CYAN = "#67e8f9";
  var VIOLET = "#a78bfa";
  var AMBER = "#fbbf24";      /* the one warm state */
  var RED = "#f87171";        /* --danger */

  /* Every field here is eased. `glow` is not an orb property — it is how hard
   * the orb should be lighting the sky behind it, so the whole scene responds
   * together rather than the orb changing alone. */
  var MOODS = {
    idle: {
      churn: 0.20, amp: 0.068, brightness: 0.52, opacity: 0.36,
      haloStrength: 0.50, rimPower: 2.2, spin: 0.70, scale: 1.00,
      core: ACCENT, rim: ACCENT, halo: ACCENT, glow: 0.15, coreMul: 0.26,
    },
    listening: {
      churn: 0.44, amp: 0.100, brightness: 1.12, opacity: 0.58,
      haloStrength: 1.32, rimPower: 1.65, spin: 1.05, scale: 1.05,
      core: AMBER, rim: AMBER, halo: AMBER, glow: 0.66, coreMul: 0.34,
    },
    thinking: {
      churn: 1.15, amp: 0.132, brightness: 0.60, opacity: 0.48,
      haloStrength: 0.80, rimPower: 2.0, spin: 1.95, scale: 0.97,
      core: ACCENT, rim: CYAN, halo: VIOLET, glow: 0.46, coreMul: 0.28,
      cycle: true,     /* body colour drifts between two hues while it thinks */
    },
    speaking: {
      churn: 0.70, amp: 0.148, brightness: 1.32, opacity: 0.64,
      haloStrength: 1.35, rimPower: 1.70, spin: 1.35, scale: 1.02,
      core: ACCENT, rim: ACCENT, halo: ACCENT, glow: 0.72, coreMul: 0.38,
      pulse: true,     /* a subtle rhythmic size beat on top of the voice */
    },
    error: {
      churn: 0.04, amp: 0.026, brightness: 1.10, opacity: 0.74,
      haloStrength: 0.70, rimPower: 3.2, spin: 0.08, scale: 0.95,
      core: RED, rim: RED, halo: RED, glow: 0.38, coreMul: 0.42,
    },
  };

  /* app.js and the old hologram speak "thinking"; the spec calls it
   * "processing". Accept both rather than silently dropping one. */
  var ALIASES = {
    processing: "thinking",
    working: "thinking",
    busy: "thinking",
    ready: "idle",
    failed: "error",
  };

  function resolve(name) {
    var key = String(name || "idle").toLowerCase();
    key = ALIASES[key] || key;
    return MOODS[key] ? key : "idle";
  }

  function create(opts) {
    opts = opts || {};
    var THREE = global.THREE;
    var accent = opts.accent || ACCENT;

    /* An explicit accent override replaces teal everywhere it appears, but
     * never touches amber or red — those two carry meaning, not identity. */
    var moods = {};
    Object.keys(MOODS).forEach(function (key) {
      var m = Object.assign({}, MOODS[key]);
      ["core", "rim", "halo"].forEach(function (slot) {
        if (m[slot] === ACCENT) m[slot] = accent;
      });
      moods[key] = m;
    });

    var current = "idle";
    var cycleT = 0;
    var tealC = new THREE.Color(accent);
    var violetC = new THREE.Color(VIOLET);
    var mixC = new THREE.Color();
    var coreC = new THREE.Color();

    function set(name) {
      current = resolve(name);
      return current;
    }

    /* Called every frame. Writes targets onto the orb and returns the sky glow
     * so the background can follow the same mood. */
    function apply(orb, dt, t) {
      var m = moods[current];
      var targets = {
        churn: m.churn, amp: m.amp, brightness: m.brightness,
        opacity: m.opacity, haloStrength: m.haloStrength,
        rimPower: m.rimPower, spin: m.spin, scale: m.scale,
      };

      if (m.cycle) {
        /* Thinking: the body colour drifts between two hues rather than sitting
         * on one, which is what makes it read as working rather than waiting. */
        cycleT += dt;
        var k = 0.5 + 0.5 * Math.sin(cycleT * 0.62);
        mixC.copy(tealC).lerp(violetC, k);
        coreC.copy(mixC).multiplyScalar(m.coreMul);
        targets.rim = "#" + mixC.getHexString();
        targets.core = "#" + coreC.getHexString();
        targets.halo = "#" + violetC.clone().lerp(tealC, 1 - k).getHexString();
      } else {
        targets.rim = m.rim;
        targets.halo = m.halo;
        coreC.set(m.core).multiplyScalar(m.coreMul);
        targets.core = "#" + coreC.getHexString();
      }

      if (m.pulse) {
        /* Speaking: a slow beat under the voice, so it stays alive through a
         * pause between words instead of going flat. */
        targets.scale = m.scale + Math.sin(t * 5.1) * 0.022;
      }

      orb.setTargets(targets);

      var level = orb.levelNow();
      return m.glow + level * 0.30;
    }

    return {
      set: set,
      apply: apply,
      currentName: function () { return current; },
      isSpeaking: function () { return current === "speaking"; },
      isListening: function () { return current === "listening"; },
      isThinking: function () { return current === "thinking"; },
    };
  }

  global.IrisStates = { create: create, resolve: resolve, MOODS: MOODS, ALIASES: ALIASES };
})(window);
