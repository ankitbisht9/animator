/**
 * Compiled representation: plain JSON (so the worker can return it and the
 * browser can consume it). Actions become segments on per-object channels.
 */
import type { Expr } from "../expr/expr";
import type { Easing } from "../interp/easing";
import type { Rect } from "../math/rect";

export type ChannelValue = number | string | boolean;

interface SegBase {
  t0: number;
  t1: number;
  /** Id of the action that produced this segment (for error messages). */
  src: string;
}

/** Numeric tween. `arc` adds a parabolic offset (peak = arc at u = 0.5). */
export interface TweenSeg extends SegBase {
  k: "tween";
  from: number;
  to: number;
  ease: Easing;
  arc?: number;
}

/** Color tween, interpolated in OKLab. Values are hex. */
export interface ColorSeg extends SegBase {
  k: "color";
  from: string;
  to: string;
  ease: Easing;
}

/** Discrete change at t0 (t1 === t0). */
export interface StepSeg extends SegBase {
  k: "step";
  value: ChannelValue;
}

/** Explicit keyframes (numeric), absolute times. */
export interface KeysSeg extends SegBase {
  k: "keys";
  times: number[];
  values: number[];
  eases: (Easing | null)[];
}

/** Value read from a solver channel; solver-local time = (t - t0) * timeScale. */
export interface SolverSeg extends SegBase {
  k: "solver";
  solver: string;
  channel: string;
  timeScale: number;
  /** Added to the solver value (e.g. convert solver displacement to scene y). */
  offset: number;
  /** Multiplied before offset (unit conversion, sign). */
  gain: number;
}

/** Value computed from an expression; `t` in the expression is seconds since t0. */
export interface ExprSeg extends SegBase {
  k: "expr";
  src: string;
  expr: Expr;
  /** When set, the value is a formatted string (for text params). */
  format?: string;
  /** Value at t1, computed at compile time for holding afterwards. */
  end?: number;
}

/** Follow another object's anchor (+ offset) on one axis. */
export interface FollowSeg extends SegBase {
  k: "follow";
  leader: string;
  anchor: string;
  axis: "x" | "y";
  offset: number;
  /** Value at t1 (compile time). */
  end?: number;
}

/** Pre-sampled values (e.g. smoothed camera follow), uniform spacing from t0. */
export interface SampledSeg extends SegBase {
  k: "sampled";
  dt: number;
  values: number[];
}

/** Presence (enter/exit). Value 0..1 (may overshoot with back easings); style drives the effect. */
export interface PresenceSeg extends SegBase {
  k: "presence";
  from: number;
  to: number;
  ease: Easing;
  style: string;
}

/** Temporary emphasis: intensity rises and falls within [t0, t1]. */
export interface PulseSeg extends SegBase {
  k: "pulse";
  style: string;
  color: string;
  terms?: string[];
}

export type Segment =
  | TweenSeg
  | ColorSeg
  | StepSeg
  | KeysSeg
  | SolverSeg
  | ExprSeg
  | FollowSeg
  | SampledSeg
  | PresenceSeg
  | PulseSeg;

export interface Channel {
  base: ChannelValue;
  segs: Segment[];
}

/** Channels every object has, plus dynamic ones (params.*, term.*, morph). */
export interface ObjectChannels {
  x: Channel;
  y: Channel;
  rotation: Channel;
  scale: Channel;
  opacity: Channel;
  presence: Channel;
  draw: Channel;
  reveal: Channel;
  emph: Channel;
  fill: Channel;
  stroke: Channel;
  text: Channel;
  strokeWidth: Channel;
  shadow: Channel;
  dashed: Channel;
  [name: string]: Channel;
}

/** Inline binding in a param: value computed from an expression each frame. */
export interface BoundParam {
  expr: Expr;
  src: string;
  format?: string;
  /** Text param with {{...}}-free binding: formatted number. */
  text: boolean;
}

export type FollowPlace =
  | { kind: "relative"; relativeTo: string; side: string; gap: number; align: string }
  | { kind: "pin"; at: string; anchor: string; dx: number; dy: number };

/** Membership of an object in a group over time (see group/ungroup actions). */
export interface GroupLink {
  t0: number;
  t1: number;
  group: string;
  /** Inverse of the group's world matrix at t0. */
  inv: [number, number, number, number, number, number];
}

export interface CompiledObject {
  id: string;
  type: string;
  layer: "background" | "main" | "foreground" | "overlay";
  z: number;
  persist: boolean;
  decorative: boolean;
  /** Static params (canonical, defaults applied). Bound params listed in `bound`. */
  params: Record<string, unknown>;
  bound: Record<string, BoundParam>;
  /** Channel name -> channel. Always has x, y, rotation, scale, opacity, presence. */
  ch: ObjectChannels;
  /** Static parent group (from the object definition). */
  parent?: string;
  links: GroupLink[];
  /** Placement that tracks another object every frame (place.follow: true). */
  followPlace?: FollowPlace;
  /** Terms (equation parts) that are revealed by write actions (start hidden). */
  hiddenTerms: string[];
  /** Lifecycle at scene end. */
  removedAt?: number;
  metadata?: { name?: string; description?: string; concept?: string };
  /** Compile-time prepared data (typeset equation, resampled paths). */
  prep?: unknown;
  /** Object was carried over from the previous scene. */
  carried: boolean;
}

export interface CompiledSolver {
  id: string;
  type: string;
  /** Start of the run_solver action (scene seconds); null if never run. */
  t0: number | null;
  t1: number | null;
  timeScale: number;
  dt: number;
  channels: Record<string, number[]>;
  constants: Record<string, number>;
  outcome: Record<string, string | number | boolean>;
}

export interface ResolvedBeat {
  id: string;
  text: string;
  start: number;
  end: number;
}

export interface CompiledCamera {
  ch: { cx: Channel; cy: Channel; zoom: Channel };
}

export interface CompiledTransition {
  t0: number;
  t1: number;
  style: string;
  color: string;
}

export interface CompiledScene {
  engineVersion: string;
  dslVersion: string;
  id: string;
  title: string;
  duration: number;
  seed: number;
  themeId: string;
  layout: string;
  background: { style: string; color: string };
  captions: boolean;
  /** Objects in evaluation (dependency) order. */
  objects: CompiledObject[];
  camera: CompiledCamera;
  solvers: CompiledSolver[];
  values: Record<string, number>;
  cues: Record<string, number>;
  beats: ResolvedBeat[];
  actions: Record<string, { start: number; end: number; action: string; target?: string }>;
  transitions: CompiledTransition[];
  transitionIn: { type: string; duration: number };
  interactions: { id: string; at: number; question: string; choices: string[]; answer: number; explain: string }[];
  /** Stage region, for camera bounds and validators. */
  stage: Rect;
}
