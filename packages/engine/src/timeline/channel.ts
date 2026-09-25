/**
 * Sampling a channel at time t. Segments on one channel never overlap (the
 * compiler rejects conflicts), so the value is: base before the first segment,
 * the active segment's value inside it, and the segment's end value after it.
 */
import { mixHex } from "../color/color";
import type { Expr } from "../expr/expr";
import { ease } from "../interp/easing";
import { clamp } from "../math/num";
import type { Vec2 } from "../math/vec";
import type { Channel, ChannelValue, KeysSeg, Segment } from "./types";

export interface SampleEnv {
  /** Solver channel value at the scene time (the solver maps it to local time). */
  solverValue(solver: string, channel: string, sceneT: number): number;
  /** Evaluate an expression; `local` is seconds since the segment start. */
  exprValue(expr: Expr, local: number, sceneT: number): number;
  /** An already-evaluated object's anchor in world space for the current frame. */
  anchorOf(objectId: string, anchor: string): Vec2 | null;
}

/** Index of the last segment with t0 <= t, or -1. */
export function segmentIndexAt(segs: readonly Segment[], t: number): number {
  let lo = 0;
  let hi = segs.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if ((segs[mid] as Segment).t0 <= t) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}

function keysValue(seg: KeysSeg, t: number): number {
  const { times, values, eases } = seg;
  if (t <= (times[0] as number)) return values[0] as number;
  const last = times.length - 1;
  if (t >= (times[last] as number)) return values[last] as number;
  let i = 0;
  while (i < last && (times[i + 1] as number) < t) i++;
  const t0 = times[i] as number;
  const t1 = times[i + 1] as number;
  const u = t1 === t0 ? 1 : (t - t0) / (t1 - t0);
  const e = ease(eases[i + 1] ?? "ease_in_out", u);
  return (values[i] as number) + ((values[i + 1] as number) - (values[i] as number)) * e;
}

/** Value of a segment at time t (t >= seg.t0; values past t1 hold the end value). */
export function segmentValue(seg: Segment, t: number, env: SampleEnv): ChannelValue {
  const active = t <= seg.t1;
  const span = seg.t1 - seg.t0;
  const u = span <= 0 ? 1 : clamp((t - seg.t0) / span, 0, 1);
  switch (seg.k) {
    case "tween": {
      if (!active) return seg.to;
      const v = seg.from + (seg.to - seg.from) * ease(seg.ease, u);
      return seg.arc ? v + seg.arc * 4 * u * (1 - u) : v;
    }
    case "presence":
      return active ? seg.from + (seg.to - seg.from) * ease(seg.ease, u) : seg.to;
    case "color":
      return active ? mixHex(seg.from, seg.to, clamp(ease(seg.ease, u), 0, 1)) : seg.to;
    case "step":
      return seg.value;
    case "keys":
      return keysValue(seg, t);
    case "solver":
      return env.solverValue(seg.solver, seg.channel, Math.min(t, seg.t1)) * seg.gain + seg.offset;
    case "expr":
      if (!active && seg.end !== undefined) return seg.end;
      return env.exprValue(seg.expr, Math.min(t, seg.t1) - seg.t0, Math.min(t, seg.t1));
    case "follow": {
      if (!active && seg.end !== undefined) return seg.end;
      const p = env.anchorOf(seg.leader, seg.anchor);
      return p ? (seg.axis === "x" ? p.x : p.y) + seg.offset : 0;
    }
    case "sampled": {
      if (!active) return seg.values[seg.values.length - 1] ?? 0;
      const f = (t - seg.t0) / seg.dt;
      const i = Math.min(seg.values.length - 1, Math.floor(f));
      const a = seg.values[i] ?? 0;
      const b = seg.values[Math.min(seg.values.length - 1, i + 1)] ?? a;
      return a + (b - a) * (f - i);
    }
    case "pulse":
      return active ? Math.sin(Math.PI * u) : 0;
  }
}

export function sampleChannel(ch: Channel | undefined, t: number, env: SampleEnv): ChannelValue | undefined {
  if (!ch) return undefined;
  const i = segmentIndexAt(ch.segs, t);
  if (i < 0) return ch.base;
  return segmentValue(ch.segs[i] as Segment, t, env);
}

export function sampleNumber(ch: Channel | undefined, t: number, env: SampleEnv, fallback: number): number {
  const v = sampleChannel(ch, t, env);
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

/** The segment governing time t (active, or the most recent one before t). */
export function currentSegment(ch: Channel | undefined, t: number): Segment | undefined {
  if (!ch) return undefined;
  const i = segmentIndexAt(ch.segs, t);
  return i < 0 ? undefined : ch.segs[i];
}
