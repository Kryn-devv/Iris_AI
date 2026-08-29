/**
 * Ambient soundscape, fully synthesized with WebAudio — no audio files.
 *
 * A soft evolving pad (detuned oscillators → lowpass with slow LFO) plus a
 * sparse "data blip" texture. Four moods follow the journey's chapters:
 * chaos → neural → expansive → resolution. Strictly opt-in: nothing is
 * created until the user presses the sound toggle (a user gesture), fades
 * in/out gently, and suspends when off or when the tab is hidden.
 *
 * SSR-safe: no window/AudioContext access at module level.
 */

type Mood = "chaos" | "neural" | "expansive" | "resolution";

interface MoodSpec {
  /** Pad chord frequencies (Hz). */
  chord: [number, number, number, number];
  /** Lowpass cutoff target. */
  cutoff: number;
  /** Average seconds between blips. */
  blipEvery: number;
  /** Pentatonic-ish pool the blips pick from. */
  blipScale: number[];
  padGain: number;
}

const MOODS: Record<Mood, MoodSpec> = {
  chaos: {
    chord: [55, 110, 116.54, 164.81], // A, A, A#(rub), E — uneasy
    cutoff: 420,
    blipEvery: 0.7,
    blipScale: [440, 493.88, 554.37, 659.25, 739.99],
    padGain: 0.5,
  },
  neural: {
    chord: [65.41, 130.81, 155.56, 196], // Cm-ish — focused
    cutoff: 760,
    blipEvery: 0.32,
    blipScale: [523.25, 622.25, 783.99, 932.33, 1046.5],
    padGain: 0.55,
  },
  expansive: {
    chord: [58.27, 116.54, 174.61, 233.08], // Bb open fifths — wide
    cutoff: 980,
    blipEvery: 1.1,
    blipScale: [466.16, 587.33, 698.46, 880],
    padGain: 0.62,
  },
  resolution: {
    chord: [65.41, 130.81, 196, 261.63], // C major — arrival
    cutoff: 1200,
    blipEvery: 1.6,
    blipScale: [523.25, 659.25, 783.99, 1046.5],
    padGain: 0.66,
  },
};

/** Chapter index (copy.ts order) → mood. */
const CHAPTER_MOODS: Mood[] = [
  "expansive", // hero
  "chaos", // chaos
  "chaos", // capture (still busy, converging)
  "neural", // analysis
  "neural", // priorities
  "neural", // voting
  "expansive", // roadmap
  "expansive", // surveys (calm)
  "expansive", // changelog
  "resolution", // finale
];

const MASTER_LEVEL = 0.22;

class AmbientAudio {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private padGain: GainNode | null = null;
  private filter: BiquadFilterNode | null = null;
  private oscs: OscillatorNode[] = [];
  private oscGains: GainNode[] = [];
  private lfo: OscillatorNode | null = null;
  private noiseGain: GainNode | null = null;
  private blipTimer: ReturnType<typeof setTimeout> | null = null;
  private mood: Mood = "expansive";
  enabled = false;
  private listeners = new Set<(on: boolean) => void>();

  onChange(fn: (on: boolean) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit() {
    this.listeners.forEach((fn) => fn(this.enabled));
  }

  /** Must be called from a user gesture. */
  async enable(chapterIndex: number) {
    if (typeof window === "undefined") return;
    if (!this.ctx) this.build();
    const ctx = this.ctx;
    if (!ctx || !this.master) return;
    this.enabled = true;
    this.emit();
    try {
      await ctx.resume();
    } catch {
      /* context refused — stay silent */
    }
    const t = ctx.currentTime;
    this.master.gain.cancelScheduledValues(t);
    this.master.gain.setValueAtTime(this.master.gain.value, t);
    this.master.gain.linearRampToValueAtTime(MASTER_LEVEL, t + 1.8);
    this.setChapter(chapterIndex, true);
    this.scheduleBlip();
  }

  async disable() {
    this.enabled = false;
    this.emit();
    if (this.blipTimer) clearTimeout(this.blipTimer);
    this.blipTimer = null;
    const ctx = this.ctx;
    if (!ctx || !this.master) return;
    const t = ctx.currentTime;
    this.master.gain.cancelScheduledValues(t);
    this.master.gain.setValueAtTime(this.master.gain.value, t);
    this.master.gain.linearRampToValueAtTime(0.0001, t + 0.9);
    setTimeout(() => {
      if (!this.enabled) this.ctx?.suspend().catch(() => undefined);
    }, 1000);
  }

  toggle(chapterIndex: number) {
    if (this.enabled) void this.disable();
    else void this.enable(chapterIndex);
  }

  /** Pause without flipping user intent (tab hidden). */
  setSuspended(hidden: boolean) {
    if (!this.ctx || !this.enabled) return;
    if (hidden) {
      if (this.blipTimer) clearTimeout(this.blipTimer);
      this.blipTimer = null;
      this.ctx.suspend().catch(() => undefined);
    } else {
      this.ctx.resume().catch(() => undefined);
      this.scheduleBlip();
    }
  }

  setChapter(chapterIndex: number, force = false) {
    const mood = CHAPTER_MOODS[Math.min(chapterIndex, CHAPTER_MOODS.length - 1)] ?? "expansive";
    if (!force && mood === this.mood) return;
    this.mood = mood;
    const ctx = this.ctx;
    if (!ctx || !this.filter || !this.padGain) return;
    const spec = MOODS[mood];
    const t = ctx.currentTime;
    this.filter.frequency.cancelScheduledValues(t);
    this.filter.frequency.setTargetAtTime(spec.cutoff, t, 2.5);
    this.padGain.gain.setTargetAtTime(spec.padGain, t, 2.5);
    this.oscs.forEach((osc, i) => {
      const target = spec.chord[i % spec.chord.length];
      osc.frequency.cancelScheduledValues(t);
      osc.frequency.setTargetAtTime(target, t, 3.5);
    });
  }

  private build() {
    const Ctor: typeof AudioContext | undefined =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const ctx = new Ctor();
    this.ctx = ctx;

    this.master = ctx.createGain();
    this.master.gain.value = 0.0001;
    this.master.connect(ctx.destination);

    this.filter = ctx.createBiquadFilter();
    this.filter.type = "lowpass";
    this.filter.frequency.value = 700;
    this.filter.Q.value = 0.6;

    this.padGain = ctx.createGain();
    this.padGain.gain.value = 0.6;
    this.padGain.connect(this.filter);
    this.filter.connect(this.master);

    // Slow LFO breathes the filter open and closed.
    this.lfo = ctx.createOscillator();
    this.lfo.frequency.value = 0.06;
    const lfoGain = ctx.createGain();
    lfoGain.gain.value = 180;
    this.lfo.connect(lfoGain);
    lfoGain.connect(this.filter.frequency);
    this.lfo.start();

    // Pad: four detuned oscillators.
    const spec = MOODS[this.mood];
    const waves: OscillatorType[] = ["sine", "triangle", "sine", "triangle"];
    for (let i = 0; i < 4; i++) {
      const osc = ctx.createOscillator();
      osc.type = waves[i];
      osc.frequency.value = spec.chord[i];
      osc.detune.value = i % 2 === 0 ? -4 : 5;
      const g = ctx.createGain();
      g.gain.value = i === 0 ? 0.5 : 0.28;
      osc.connect(g);
      g.connect(this.padGain);
      osc.start();
      this.oscs.push(osc);
      this.oscGains.push(g);
    }

    // Space-wind: filtered noise, barely audible.
    const noiseLen = ctx.sampleRate * 2;
    const buffer = ctx.createBuffer(1, noiseLen, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < noiseLen; i++) data[i] = (Math.random() * 2 - 1) * 0.4;
    const noise = ctx.createBufferSource();
    noise.buffer = buffer;
    noise.loop = true;
    const noiseFilter = ctx.createBiquadFilter();
    noiseFilter.type = "bandpass";
    noiseFilter.frequency.value = 320;
    noiseFilter.Q.value = 0.4;
    this.noiseGain = ctx.createGain();
    this.noiseGain.gain.value = 0.05;
    noise.connect(noiseFilter);
    noiseFilter.connect(this.noiseGain);
    this.noiseGain.connect(this.master);
    noise.start();
  }

  private scheduleBlip() {
    if (!this.enabled || !this.ctx) return;
    if (this.blipTimer) clearTimeout(this.blipTimer);
    const spec = MOODS[this.mood];
    const wait = spec.blipEvery * (0.5 + Math.random());
    this.blipTimer = setTimeout(() => {
      this.playBlip();
      this.scheduleBlip();
    }, wait * 1000);
  }

  private playBlip() {
    const ctx = this.ctx;
    if (!ctx || !this.master || ctx.state !== "running") return;
    const spec = MOODS[this.mood];
    const freq = spec.blipScale[Math.floor(Math.random() * spec.blipScale.length)];
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, t);
    osc.frequency.exponentialRampToValueAtTime(freq * 1.01, t + 0.3);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.09, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.55);
    // Gentle stereo drift.
    const pan = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
    osc.connect(g);
    if (pan) {
      pan.pan.value = Math.random() * 1.6 - 0.8;
      g.connect(pan);
      pan.connect(this.master);
    } else {
      g.connect(this.master);
    }
    osc.start(t);
    osc.stop(t + 0.7);
  }
}

export const ambientAudio = new AmbientAudio();
