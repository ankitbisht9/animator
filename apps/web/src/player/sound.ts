/**
 * Sound for lessons (ADR-065): soft effects on what happens on screen (a pop as a thing appears, a
 * whoosh as it moves, a chime on a highlight, a swish between scenes, a two-note call for a quick
 * check) and a quiet music bed that ducks under the voice. Everything is synthesized in the browser
 * with the Web Audio API: no audio files, no licences, nothing to download, no cost.
 *
 * Cues come from the compiled scenes (each action's start, scene starts, checkpoints) and fire as the
 * player's clock passes them while playing: never on a seek, never twice in a burst.
 */
import type { Lesson } from "../lesson/lesson";

export type SoundKind = "pop" | "out" | "whoosh" | "tick" | "chime" | "morph" | "swish" | "quiz";

export interface SoundCue {
  t: number; // global lesson time (seconds)
  kind: SoundKind;
}

/** effects and music; effects only; silence. */
export type SoundMode = "all" | "effects" | "off";
export const SOUND_MODES: SoundMode[] = ["all", "effects", "off"];

const ACTION_SOUND: Record<string, SoundKind | undefined> = {
  create: "pop",
  show: "pop",
  label: "pop",
  remove: "out",
  hide: "out",
  cut: "out",
  move: "whoosh",
  rotate: "whoosh",
  scale: "whoosh",
  camera_move: "whoosh",
  transform: "morph",
  morph: "morph",
  highlight: "chime",
  write: "tick",
  draw: "tick",
  transition: "swish",
};

/** The quietest moment between two sounds, and between two of the same kind. */
const MIN_GAP_S = 0.16;
const SAME_GAP_S = 0.4;
/** Motions shorter than this make no whoosh. */
const LEAST_MOTION_S = 0.35;

/** Every sound of a lesson, in time order, thinned so a burst of actions makes one sound. */
export function soundCues(lesson: Lesson): SoundCue[] {
  const raw: SoundCue[] = [];
  for (const s of lesson.scenes) {
    const c = s.compiled;
    if (!c) continue;
    if (s.index > 0) raw.push({ t: s.start + 0.02, kind: "swish" });
    for (const a of Object.values(c.actions)) {
      const kind = ACTION_SOUND[a.action];
      if (!kind || a.start < 0 || a.start >= c.duration) continue;
      if (kind === "whoosh" && a.end - a.start < LEAST_MOTION_S) continue;
      // The title writes itself as every scene opens: the scene's swish is enough.
      if (kind === "tick" && a.start < 0.6) continue;
      raw.push({ t: s.start + a.start, kind });
    }
    for (const q of c.interactions) raw.push({ t: s.start + Math.max(0, q.at - 0.25), kind: "quiz" });
  }
  raw.sort((x, y) => x.t - y.t);
  const out: SoundCue[] = [];
  const lastOf = new Map<SoundKind, number>();
  let last = Number.NEGATIVE_INFINITY;
  for (const cue of raw) {
    const prev = lastOf.get(cue.kind) ?? Number.NEGATIVE_INFINITY;
    const priority = cue.kind === "swish" || cue.kind === "quiz";
    if (!priority && (cue.t - last < MIN_GAP_S || cue.t - prev < SAME_GAP_S)) continue;
    out.push(cue);
    last = cue.t;
    lastOf.set(cue.kind, cue.t);
  }
  return out;
}

/** The cues passed when the clock moved from `from` to `to` (none for a jump: a seek is not playback). */
export function cuesBetween(cues: SoundCue[], from: number, to: number): SoundCue[] {
  if (!(to > from) || to - from > 0.6) return [];
  let lo = 0;
  let hi = cues.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((cues[mid]?.t ?? 0) <= from) lo = mid + 1;
    else hi = mid;
  }
  const out: SoundCue[] = [];
  for (let i = lo; i < cues.length && (cues[i]?.t ?? Number.POSITIVE_INFINITY) <= to; i++) {
    const cue = cues[i];
    if (cue) out.push(cue);
  }
  return out;
}

// ---------------------------------------------------------------------- synthesis

const EFFECTS_GAIN = 0.5;
const MUSIC_GAIN = 0.06;
const MUSIC_DUCKED = 0.022;
// A slow, warm progression (Cmaj7, Am7, Fmaj7, G6), each chord about 8 seconds.
const CHORDS: number[][] = [
  [48, 52, 55, 59],
  [45, 48, 52, 55],
  [41, 45, 48, 52],
  [43, 47, 50, 52],
];
const CHORD_S = 8;

const hz = (midi: number) => 440 * 2 ** ((midi - 69) / 12);

type Ctx = AudioContext;

function envelope(g: GainNode, at: number, peak: number, attack: number, decay: number) {
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), at + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, at + attack + decay);
}

function tone(
  ctx: Ctx,
  out: AudioNode,
  at: number,
  type: OscillatorType,
  f0: number,
  f1: number,
  peak: number,
  dur: number,
) {
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(f0, at);
  o.frequency.exponentialRampToValueAtTime(Math.max(20, f1), at + dur);
  envelope(g, at, peak, 0.006, dur);
  o.connect(g).connect(out);
  o.start(at);
  o.stop(at + dur + 0.05);
}

function noise(ctx: Ctx, out: AudioNode, at: number, peak: number, dur: number, f0: number, f1: number, q: number) {
  const len = Math.ceil(ctx.sampleRate * (dur + 0.05));
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const data = buf.getChannelData(0);
  let seed = 22_222;
  for (let i = 0; i < len; i++) {
    seed = (seed * 16_807) % 2_147_483_647;
    data[i] = (seed / 2_147_483_647) * 2 - 1;
  }
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const bp = ctx.createBiquadFilter();
  bp.type = "bandpass";
  bp.Q.value = q;
  bp.frequency.setValueAtTime(f0, at);
  bp.frequency.exponentialRampToValueAtTime(f1, at + dur);
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(peak, at + dur * 0.4);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  src.connect(bp).connect(g).connect(out);
  src.start(at);
  src.stop(at + dur + 0.05);
}

/** Plays one sound `kind` at `at` (seconds on the context's clock) into `out`. */
export function playSound(ctx: Ctx, out: AudioNode, kind: SoundKind, at: number): void {
  switch (kind) {
    case "pop":
      tone(ctx, out, at, "sine", 720, 380, 0.32, 0.11);
      tone(ctx, out, at, "triangle", 1440, 900, 0.06, 0.06);
      break;
    case "out":
      tone(ctx, out, at, "sine", 420, 220, 0.2, 0.12);
      break;
    case "whoosh":
      noise(ctx, out, at, 0.16, 0.32, 500, 2200, 0.9);
      break;
    case "tick":
      tone(ctx, out, at, "triangle", 2100, 1700, 0.05, 0.035);
      break;
    case "chime":
      tone(ctx, out, at, "sine", 1046.5, 1046.5, 0.12, 0.7);
      tone(ctx, out, at + 0.01, "sine", 1568, 1568, 0.05, 0.55);
      break;
    case "morph":
      tone(ctx, out, at, "sine", 330, 660, 0.12, 0.28);
      noise(ctx, out, at, 0.06, 0.28, 900, 2600, 1.2);
      break;
    case "swish":
      noise(ctx, out, at, 0.14, 0.42, 2600, 520, 0.7);
      break;
    case "quiz":
      tone(ctx, out, at, "sine", 659.3, 659.3, 0.16, 0.22);
      tone(ctx, out, at + 0.14, "sine", 987.8, 987.8, 0.16, 0.4);
      break;
  }
}

/** The sound of the player: effects on cues, and the music bed. Created on first use (after the viewer
 * pressed play, so the browser lets it start). */
export class SoundBoard {
  private ctx: AudioContext | null = null;
  private effects: GainNode | null = null;
  private music: GainNode | null = null;
  private chordTimer: number | null = null;
  private chord = 0;
  private voices: { osc: OscillatorNode; gain: GainNode }[] = [];
  private playingMusic = false;

  private context(): AudioContext | null {
    if (this.ctx) return this.ctx;
    const Ctor =
      window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    try {
      this.ctx = new Ctor();
    } catch {
      return null;
    }
    this.effects = this.ctx.createGain();
    this.effects.gain.value = EFFECTS_GAIN;
    this.effects.connect(this.ctx.destination);
    const lp = this.ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 1500;
    this.music = this.ctx.createGain();
    this.music.gain.value = 0;
    this.music.connect(lp).connect(this.ctx.destination);
    return this.ctx;
  }

  /** Plays the cues (they were just passed): a burst plays only its first. */
  play(cues: SoundCue[]): void {
    if (!cues.length) return;
    const ctx = this.context();
    if (!ctx || !this.effects) return;
    if (ctx.state === "suspended") void ctx.resume().catch(() => undefined);
    const first = cues[0];
    if (first) playSound(ctx, this.effects, first.kind, ctx.currentTime + 0.005);
  }

  /** The music bed on or off; `ducked` while the voice speaks. */
  setMusic(on: boolean, ducked: boolean): void {
    const ctx = on ? this.context() : this.ctx;
    if (!ctx || !this.music) return;
    const now = ctx.currentTime;
    const target = on ? (ducked ? MUSIC_DUCKED : MUSIC_GAIN) : 0;
    this.music.gain.cancelScheduledValues(now);
    this.music.gain.setTargetAtTime(target, now, on ? 0.35 : 0.2);
    if (on && !this.playingMusic) {
      if (ctx.state === "suspended") void ctx.resume().catch(() => undefined);
      this.playingMusic = true;
      this.nextChord();
    } else if (!on && this.playingMusic) {
      this.playingMusic = false;
      if (this.chordTimer !== null) window.clearTimeout(this.chordTimer);
      this.chordTimer = null;
      const voices = this.voices;
      this.voices = [];
      for (const v of voices) {
        v.gain.gain.setTargetAtTime(0, now, 0.3);
        v.osc.stop(now + 1.5);
      }
    }
  }

  private nextChord(): void {
    const ctx = this.ctx;
    if (!ctx || !this.music || !this.playingMusic) return;
    const now = ctx.currentTime;
    for (const v of this.voices) {
      v.gain.gain.setTargetAtTime(0, now, 0.9);
      v.osc.stop(now + 4);
    }
    this.voices = [];
    const notes = CHORDS[this.chord % CHORDS.length] ?? [];
    this.chord += 1;
    notes.forEach((midi, i) => {
      for (const [type, mult, level, detune] of [
        ["sine", 1, 0.22, -3],
        ["triangle", 2, 0.06, 4],
      ] as const) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = type;
        osc.frequency.value = hz(midi) * mult;
        osc.detune.value = detune + i;
        gain.gain.setValueAtTime(0, now);
        gain.gain.setTargetAtTime(level / notes.length, now, 1.1);
        osc.connect(gain).connect(this.music as GainNode);
        osc.start(now);
        this.voices.push({ osc, gain });
      }
    });
    this.chordTimer = window.setTimeout(() => this.nextChord(), CHORD_S * 1000);
  }

  dispose(): void {
    this.setMusic(false, false);
    const ctx = this.ctx;
    this.ctx = null;
    if (ctx) void ctx.close().catch(() => undefined);
  }
}
