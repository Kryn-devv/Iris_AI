/* IRIS scene — Tier 3b: what the orb physically reacts to.
 *
 * Two sources, auto-selected:
 *
 *   REAL      the microphone, through an AnalyserNode. Gives genuine
 *             reactivity while IRIS is listening to you. Requested lazily —
 *             the first time listening actually starts — so the page never
 *             prompts for a microphone it has not been asked to use.
 *
 *   SYNTHETIC layered sines shaped to the cadence of speech. Used whenever no
 *             real signal is flowing, which includes the entire time IRIS is
 *             TALKING: browser speechSynthesis routes straight to the OS output
 *             and cannot be tapped by an analyser at all.
 *
 * Both produce the same two numbers — an overall loudness and a bass level —
 * and the orb smooths them asymmetrically (fast attack, slow decay), so it
 * leaps on a loud syllable and eases back rather than twitching frame to frame.
 *
 * ── THE iOS PITFALL, for when real playback audio is added ──────────────────
 * iOS Safari will not reliably let you feed audio into an AnalyserNode while
 * also playing it aloud. The pattern that works: play the audible copy through
 * one path, and feed a SILENT DUPLICATE of the same audio into the analyser
 * purely to read its levels. Create the AudioContext from inside a real user
 * tap, and give the microphone its own separate AudioContext. Ignore any of
 * that on iOS and the visualiser reads zeroes while the audio plays fine —
 * which looks like a broken orb, not an audio problem.
 */
(function (global) {
  "use strict";

  /* A real signal is only trusted while it is actually arriving. If the mic
   * goes silent for this long we fall back, so a muted input does not freeze
   * the orb. */
  var LIVE_TIMEOUT_MS = 900;
  var SILENCE_FLOOR = 0.012;

  function create(opts) {
    opts = opts || {};

    var ctx = null;
    var analyser = null;
    var source = null;
    var bins = null;
    var stream = null;
    var lastSignalAt = 0;
    var requested = false;
    var denied = false;
    var real = { level: 0, bass: 0 };

    /* ---- the real path ---- */

    function enable() {
      if (requested || denied) return Promise.resolve(false);
      requested = true;
      if (!global.navigator || !navigator.mediaDevices ||
          !navigator.mediaDevices.getUserMedia || !(global.AudioContext || global.webkitAudioContext)) {
        denied = true;
        return Promise.resolve(false);
      }
      return navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      }).then(function (s) {
        stream = s;
        var Ctx = global.AudioContext || global.webkitAudioContext;
        /* The microphone gets its own context — sharing one with playback is
         * the specific thing iOS refuses to do reliably. */
        ctx = new Ctx();
        analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.55;
        bins = new Uint8Array(analyser.frequencyBinCount);
        source = ctx.createMediaStreamSource(stream);
        source.connect(analyser);
        /* Deliberately NOT connected to the destination: analysing the mic must
         * never put it through the speakers. */
        return true;
      }).catch(function (err) {
        denied = true;
        console.info("[iris] microphone unavailable, using synthetic motion:", err && err.name);
        return false;
      });
    }

    function disable() {
      if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
      if (source) { try { source.disconnect(); } catch (e) {} }
      if (ctx && ctx.close) { try { ctx.close(); } catch (e) {} }
      ctx = null; analyser = null; source = null; bins = null;
      requested = false;
      real.level = 0; real.bass = 0;
    }

    function readReal(now) {
      if (!analyser) return false;
      analyser.getByteFrequencyData(bins);
      var n = bins.length;
      /* Bass is the bottom eighth of the spectrum — where a voice's body is. */
      var bassEnd = Math.max(1, Math.floor(n / 8));
      var total = 0, bass = 0;
      for (var i = 0; i < n; i++) {
        total += bins[i];
        if (i < bassEnd) bass += bins[i];
      }
      real.level = Math.min(1, (total / n / 255) * 2.6);
      real.bass = Math.min(1, (bass / bassEnd / 255) * 2.2);
      if (real.level > SILENCE_FLOOR) lastSignalAt = now;
      return now - lastSignalAt < LIVE_TIMEOUT_MS;
    }

    /* ---- the synthetic path ---- */

    /* Three incommensurate rates so it never settles into an obvious loop, and
     * a slow envelope on top so phrases have shape — a flat buzz at constant
     * amplitude reads as a machine, not a voice. */
    function synth(t, state, hint) {
      if (state === "speaking") {
        var env = 0.62 + 0.38 * Math.sin(t * 0.9 + Math.sin(t * 0.31) * 1.6);
        var syl = 0.55 * Math.sin(t * 11.3) + 0.30 * Math.sin(t * 18.7) + 0.15 * Math.sin(t * 6.1);
        var lvl = Math.max(0, env * (0.46 + 0.42 * syl));
        return { level: Math.min(1, lvl * (0.55 + hint * 0.75)), bass: Math.min(1, lvl * 0.72) };
      }
      if (state === "listening") {
        /* A gentle waver: attentive, not performing. Rises with whatever hint
         * the app gives us (interim transcript length, in IRIS's case). */
        var w = 0.5 + 0.5 * Math.sin(t * 2.3) * Math.sin(t * 0.7);
        return { level: Math.min(1, 0.10 + w * 0.14 + hint * 0.55), bass: 0.06 + hint * 0.30 };
      }
      if (state === "thinking") {
        var c = 0.5 + 0.5 * Math.sin(t * 3.7) * Math.cos(t * 1.9);
        return { level: 0.08 + c * 0.16, bass: 0.05 + c * 0.10 };
      }
      if (state === "error") return { level: 0.02, bass: 0.01 };
      var breathe = 0.5 + 0.5 * Math.sin(t * 0.8);
      return { level: 0.03 + breathe * 0.045, bass: 0.02 + breathe * 0.03 };
    }

    /* ---- what the scene calls ---- */

    var usingReal = false;

    function read(t, nowMs, state, hint) {
      hint = hint || 0;
      /* Only the listening state has anything real to analyse: playback cannot
       * be tapped, so speaking is always synthetic by necessity. */
      var wantReal = state === "listening";
      var live = wantReal && readReal(nowMs);
      usingReal = live;
      if (live) {
        return { level: real.level, bass: real.bass, source: "mic" };
      }
      var s = synth(t, state, hint);
      s.source = "synthetic";
      return s;
    }

    return {
      enable: enable,
      disable: disable,
      read: read,
      usingReal: function () { return usingReal; },
      available: function () { return !!analyser; },
      denied: function () { return denied; },
    };
  }

  global.IrisAudio = { create: create };
})(window);
