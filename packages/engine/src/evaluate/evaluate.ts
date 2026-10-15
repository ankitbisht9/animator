/**
 * evaluate(compiledScene, t) -> FrameState.
 *
 * A pure function of the compiled scene and t (P1): no wall clock, no previous
 * frame, no randomness beyond seeded hashes, no I/O. Objects are evaluated in
 * dependency order so anchors of earlier objects are available to later ones.
 */
import { getSpec } from "../catalog/registry";
import type { ComponentSpec } from "../catalog/spec";
import { type MorphPrep, morphPath } from "../compile/morph";
import { type Expr, evalExpr } from "../expr/expr";
import { formatNumber } from "../expr/format";
import { pinnedOrigin, relativeOrigin } from "../layout/placement";
import {
  apply,
  IDENTITY,
  invert,
  type Mat,
  multiply,
  objectMatrix,
  rotation,
  scaling,
  transformRect,
  translation,
} from "../math/matrix";
import { clamp, clamp01 } from "../math/num";
import { boxAnchors, center, type Rect, unionAll } from "../math/rect";
import type { Vec2 } from "../math/vec";
import type { Theme } from "../theme/theme";
import { getTheme } from "../theme/themes";
import { currentSegment, type SampleEnv, sampleChannel, sampleNumber } from "../timeline/channel";
import type { CompiledObject, CompiledScene, CompiledSolver, PulseSeg } from "../timeline/types";
import { CANVAS } from "../version";
import { type BindTarget, bindArrowEnds, bindPointer } from "./binding";
import { emphasisEffect, presenceEffect } from "./effects";

export interface Emphasis {
  style: string;
  color: string;
  amount: number;
  u: number;
  terms?: string[];
}

export interface FrameObject {
  id: string;
  type: string;
  layer: CompiledObject["layer"];
  z: number;
  /** Effective opacity (0..1) including entrance/exit and parent groups. */
  alpha: number;
  /** Local -> world matrix (screen for the overlay layer). */
  matrix: Mat;
  params: Record<string, unknown>;
  fill: string;
  stroke: string;
  text: string;
  strokeWidth: number;
  /** Stroke write-on progress 0..1. */
  draw: number;
  /** Text/equation reveal progress 0..1. */
  reveal: number;
  /** Fill opacity factor 0..1 (fills fade in after a draw entrance). */
  fillReveal: number;
  /** Entrance/exit progress 0..1 (e.g. readouts count up while appearing). */
  presence: number;
  /** Per-term reveal for equations written term by term. */
  terms?: Record<string, number>;
  emph?: Emphasis;
  /** Marks on named parts that are on at this time (a diagram's nodes, ADR-061), oldest first. */
  marks?: Emphasis[];
  shadow: boolean;
  dashed: boolean;
  /** World-space axis-aligned bounds. */
  bbox: Rect;
  localBBox: Rect;
  name?: string;
  description?: string;
}

export interface FrameState {
  sceneId: string;
  t: number;
  camera: { cx: number; cy: number; zoom: number };
  background: { style: string; color: string };
  /** Visible objects in paint order. */
  objects: FrameObject[];
  caption: { beatId: string; text: string; progress: number } | null;
  overlay: { style: string; color: string; amount: number } | null;
}

/** Internal per-object result (includes invisible objects, for anchors). */
export interface EvalObject {
  obj: CompiledObject;
  spec: ComponentSpec | undefined;
  visible: boolean;
  alpha: number;
  /** Visual matrix (with entrance/emphasis effects). */
  matrix: Mat;
  /** Layout matrix (no effects): used for anchors, placement and followers. */
  rawMatrix: Mat;
  params: Record<string, unknown>;
  localBBox: Rect;
  /** Visual world bounds. */
  bbox: Rect;
  /** Layout world bounds (no effects). */
  layoutBBox: Rect;
  /** World anchors from the layout matrix. */
  anchors: Record<string, Vec2>;
  localAnchors: Record<string, Vec2>;
  presence: number;
  frame: FrameObject;
}

export interface EvalContext {
  t: number;
  theme: Theme;
  objects: Map<string, EvalObject>;
  env: SampleEnv;
  resolver: (local: number | null) => (name: string) => number | undefined;
  bboxOf: (id: string) => Rect | null;
  anchorOf: (id: string, anchor: string) => Vec2 | null;
}

const LAYER_ORDER: Record<string, number> = { background: 0, main: 1, foreground: 2, overlay: 3 };

export function solverLocalTime(s: CompiledSolver, t: number): number {
  if (s.t0 === null || s.t1 === null) return 0;
  return clamp(t - s.t0, 0, s.t1 - s.t0) * s.timeScale;
}

export function solverValueAt(s: CompiledSolver, name: string, t: number): number | undefined {
  const arr = s.channels[name];
  if (!arr) return s.constants[name];
  if (arr.length === 0) return undefined;
  const f = solverLocalTime(s, t) / s.dt;
  const i = Math.min(arr.length - 1, Math.max(0, Math.floor(f)));
  const a = arr[i] as number;
  const b = arr[Math.min(arr.length - 1, i + 1)] as number;
  return a + (b - a) * clamp01(f - i);
}

/** Emphasis envelope: markers hold while active; pulses rise and fall. */
function emphasisAmount(style: string, u: number): number {
  switch (style) {
    case "box":
    case "underline":
    case "circle":
      return clamp01(Math.min(u / 0.18, (1 - u) / 0.18));
    case "flash":
      return 1 - u;
    default:
      return Math.sin(Math.PI * u);
  }
}

export function evaluateObjects(cs: CompiledScene, tIn: number): EvalContext {
  const t = clamp(tIn, 0, cs.duration);
  const theme = getTheme(cs.themeId);
  const solvers = new Map(cs.solvers.map((s) => [s.id, s]));
  const objects = new Map<string, EvalObject>();

  const resolver =
    (local: number | null) =>
    (name: string): number | undefined => {
      if (name === "t") return local ?? t;
      const dot = name.indexOf(".");
      if (dot < 0) return cs.values[name];
      const s = solvers.get(name.slice(0, dot));
      return s ? solverValueAt(s, name.slice(dot + 1), t) : undefined;
    };

  const safeExpr = (expr: Expr, local: number | null): number => {
    try {
      return evalExpr(expr, resolver(local));
    } catch {
      return Number.NaN;
    }
  };

  const env: SampleEnv = {
    solverValue: (solver, channel, sceneT) => {
      const s = solvers.get(solver);
      return s ? (solverValueAt(s, channel, sceneT) ?? 0) : 0;
    },
    exprValue: (expr, local) => safeExpr(expr, local),
    anchorOf: (id, anchor) => anchorOf(id, anchor),
  };

  // Children per group at time t (static parents + active dynamic links).
  const children = new Map<string, string[]>();
  for (const o of cs.objects) {
    const groups: string[] = [];
    if (o.parent) groups.push(o.parent);
    for (const l of o.links) if (t >= l.t0 && t < l.t1) groups.push(l.group);
    for (const g of groups) {
      const list = children.get(g);
      if (list) list.push(o.id);
      else children.set(g, [o.id]);
    }
  }

  /** Layout bounds of an evaluated object (groups: union of their children). */
  function bboxOf(id: string): Rect | null {
    const e = objects.get(id);
    if (!e) return null;
    if (e.obj.type === "group") {
      const kids = (children.get(id) ?? []).map((k) => objects.get(k)).filter((k): k is EvalObject => !!k);
      return unionAll(kids.map((k) => k.layoutBBox)) ?? { x: e.rawMatrix[4], y: e.rawMatrix[5], w: 0, h: 0 };
    }
    return e.layoutBBox;
  }

  function anchorOf(id: string, anchor: string): Vec2 | null {
    const e = objects.get(id);
    if (!e) return null;
    if (e.obj.type === "group") {
      const b = bboxOf(id);
      return b ? ((boxAnchors(b) as Record<string, Vec2>)[anchor] ?? null) : null;
    }
    return e.anchors[anchor] ?? null;
  }

  /** What an arrow's end or a pointer refers to, for binding it to the outline (ADR-060, ADR-061). */
  const bindTarget = (id: string): BindTarget | undefined => {
    const e = objects.get(id);
    const outline = e?.spec?.partOutline;
    return (
      e && {
        type: e.obj.type,
        params: e.params,
        localBBox: e.localBBox,
        rawMatrix: e.rawMatrix,
        ...(outline ? { partOutline: (part: string) => outline(e.params, e.obj.prep, part) } : {}),
      }
    );
  };

  for (const obj of cs.objects) {
    const spec = getSpec(obj.type);
    const ch = obj.ch;
    let x = sampleNumber(ch.x, t, env, 0);
    let y = sampleNumber(ch.y, t, env, 0);
    const rot = sampleNumber(ch.rotation, t, env, 0);
    const scl = sampleNumber(ch.scale, t, env, 1);
    const opacity = clamp01(sampleNumber(ch.opacity, t, env, 1));
    const presence = sampleNumber(ch.presence, t, env, 1);
    const pSeg = currentSegment(ch.presence, t);
    const pStyle = pSeg?.k === "presence" ? pSeg.style : "fade";
    const eff = presenceEffect(pStyle, presence);

    // Params: static -> animated channels -> bindings -> point references.
    const params: Record<string, unknown> = { ...obj.params };
    for (const [name, c] of Object.entries(ch)) {
      if (name.startsWith("params.")) {
        const v = sampleChannel(c, t, env);
        if (v !== undefined) params[name.slice(7)] = v;
      }
    }
    for (const [name, b] of Object.entries(obj.bound)) {
      const v = safeExpr(b.expr, null);
      params[name] = b.text ? formatNumber(v, b.format) : v;
    }
    // The group this object moves with: its static parent, or a group it joined (`group` action).
    const link = obj.links.find((l) => t >= l.t0 && t < l.t1);
    const parentId = link?.group ?? obj.parent;
    const parent = parentId ? objects.get(parentId) : undefined;
    const groupFrame = parent ? (link ? multiply(parent.rawMatrix, link.inv) : parent.rawMatrix) : null;
    // An object drawn from its endpoints draws in its group's frame: raw points are given in that
    // frame (parts of the group, by offsets from it), while what it refers to (another object's anchor
    // or box) is found on the canvas, so it is brought into the frame to land exactly there (ADR-060).
    const toFrame = spec?.worldSpace && groupFrame ? invert(groupFrame) : null;
    const refs: Record<string, string> = {};
    for (const key of spec?.pointParams ?? []) {
      const v = params[key];
      if (typeof v === "string") {
        const dot = v.indexOf(".");
        const p = anchorOf(v.slice(0, dot), v.slice(dot + 1));
        params[key] = p ?? { x: 0, y: 0 };
        if (p) refs[key] = v;
      }
    }
    if (obj.type === "arrow" && Object.keys(refs).length) {
      bindArrowEnds(
        params,
        refs,
        bindTarget,
        toFrame ? Math.sqrt(Math.abs(toFrame[0] * toFrame[3] - toFrame[1] * toFrame[2])) : 1,
      );
    }
    if (toFrame) for (const key of Object.keys(refs)) params[key] = apply(toFrame, params[key] as Vec2);
    for (const key of spec?.objectParams ?? []) {
      const ref = params[key];
      const e = typeof ref === "string" ? objects.get(ref) : undefined;
      if (typeof ref === "string" && e) {
        const box = bboxOf(ref) ?? e.layoutBBox;
        params[`__${key}`] = toFrame
          ? { box: transformRect(toFrame, box), matrix: multiply(toFrame, e.rawMatrix), params: e.params }
          : { box, matrix: e.rawMatrix, params: e.params };
      }
    }
    const morph = (obj.prep as { morph?: MorphPrep } | undefined)?.morph;
    if (morph) {
      const u = sampleNumber(ch.morph, t, env, 0);
      if (u > 0) params.__morphD = morphPath(morph, clamp01(u));
    }

    const geo = spec ? spec.geometry(params, { theme, prep: obj.prep }) : { bbox: { x: 0, y: 0, w: 0, h: 0 } };
    const localAnchors: Record<string, Vec2> = { ...boxAnchors(geo.bbox), ...(geo.anchors ?? {}) };

    // Placement that follows a moving reference overrides x/y.
    if (obj.followPlace) {
      const rs = multiply(rotation(rot), scaling(scl));
      const fp = obj.followPlace;
      if (fp.kind === "relative") {
        const ref = bboxOf(fp.relativeTo);
        if (ref) ({ x, y } = relativeOrigin(transformRect(rs, geo.bbox), ref, fp.side, fp.gap, fp.align));
      } else {
        const dot = fp.at.indexOf(".");
        const target = anchorOf(fp.at.slice(0, dot), fp.at.slice(dot + 1));
        const la = localAnchors[fp.anchor] ?? localAnchors.center;
        if (target && la) ({ x, y } = pinnedOrigin(apply(rs, la), { x: target.x + fp.dx, y: target.y + fp.dy }));
      }
    }

    // Two matrices: `raw` (layout: placement, anchors, followers) and `m` (visual:
    // adds entrance/emphasis effects). Followers attach to raw geometry so a
    // popping or sliding object does not jostle the labels and arrows around it.
    // An object drawn from its endpoints (arrow, line, vector) has its geometry in world coordinates:
    // it turns and scales in place, about its own center, not about the world origin.
    let own = objectMatrix(x, y, rot, scl);
    if (spec?.worldSpace && (rot !== 0 || scl !== 1)) {
      const c = center(geo.bbox);
      own = multiply(
        translation(x + c.x, y + c.y),
        multiply(multiply(rotation(rot), scaling(scl)), translation(-c.x, -c.y)),
      );
    }
    let raw: Mat = own;
    let m: Mat = own;
    let parentAlpha = 1;
    if (parent && groupFrame) {
      raw = multiply(groupFrame, own);
      m = link ? multiply(multiply(parent.matrix, link.inv), own) : multiply(parent.matrix, own);
      parentAlpha = parent.alpha;
    }
    // A label's leader, a bubble's tail or a callout's pointer aimed at a diagram's node stops on it.
    if (spec?.labelLike && !spec.worldSpace) {
      const from = apply(raw, center(geo.bbox));
      for (const [key, ref] of Object.entries(refs))
        params[key] = bindPointer(params[key] as Vec2, ref, from, bindTarget);
    }

    // Emphasis.
    const eSeg = currentSegment(ch.emph, t);
    let emph: Emphasis | undefined;
    if (eSeg?.k === "pulse" && t >= eSeg.t0 && t <= eSeg.t1) {
      const u = eSeg.t1 > eSeg.t0 ? (t - eSeg.t0) / (eSeg.t1 - eSeg.t0) : 1;
      const ps = eSeg as PulseSeg;
      emph = { style: ps.style, color: ps.color, amount: emphasisAmount(ps.style, u), u };
      if (ps.terms) emph.terms = ps.terms;
    }
    // Marks on named parts of a component that marks its own parts (a diagram's nodes, ADR-061): several
    // may be on at once, each in its own color.
    const marks: Emphasis[] = [];
    for (const seg of spec?.termIds ? (ch.marks?.segs ?? []) : []) {
      if (seg.k !== "pulse" || t < seg.t0 || t > seg.t1) continue;
      const u = seg.t1 > seg.t0 ? (t - seg.t0) / (seg.t1 - seg.t0) : 1;
      marks.push({
        style: seg.style,
        color: seg.color,
        amount: emphasisAmount(seg.style, u),
        u,
        terms: seg.terms ?? [],
      });
    }
    // A highlight of named parts of such a component leaves the whole object still.
    const partsOnly = !!emph?.terms && !!spec?.termIds;
    const emphEff = emph && !partsOnly ? emphasisEffect(emph.style, emph.amount, emph.u) : { scale: 1, dx: 0 };

    // Entrance/exit and emphasis transforms apply about the object's visual center.
    const pre = transformRect(m, geo.bbox);
    const s = eff.scale * emphEff.scale;
    const dx = eff.dx + emphEff.dx;
    if (s !== 1 || dx !== 0 || eff.dy !== 0) {
      const c = center(pre);
      m = multiply(multiply(translation(c.x + dx, c.y + eff.dy), multiply(scaling(s), translation(-c.x, -c.y))), m);
    }

    const alpha = clamp01(opacity * eff.alpha * parentAlpha);
    const bbox = transformRect(m, geo.bbox);
    const layoutBBox = transformRect(raw, geo.bbox);
    const anchors: Record<string, Vec2> = {};
    for (const [k, v] of Object.entries(localAnchors)) anchors[k] = apply(raw, v);

    let terms: Record<string, number> | undefined;
    if (obj.hiddenTerms.length) {
      terms = {};
      for (const term of obj.hiddenTerms) terms[term] = clamp01(sampleNumber(ch[`term.${term}`], t, env, 0));
    }

    const fill = String(sampleChannel(ch.fill, t, env) ?? theme.colors.object_a);
    const stroke = String(sampleChannel(ch.stroke, t, env) ?? theme.colors.text);
    const text = String(sampleChannel(ch.text, t, env) ?? theme.colors.text);
    const frame: FrameObject = {
      id: obj.id,
      type: obj.type,
      layer: obj.layer,
      z: obj.z,
      alpha,
      matrix: m,
      params,
      fill,
      stroke,
      text,
      strokeWidth: sampleNumber(ch.strokeWidth, t, env, theme.stroke.normal),
      draw: clamp01(sampleNumber(ch.draw, t, env, 1)) * eff.draw,
      reveal: clamp01(sampleNumber(ch.reveal, t, env, 1)) * eff.reveal,
      fillReveal: eff.fill,
      presence: clamp01(presence),
      shadow: sampleChannel(ch.shadow, t, env) === true,
      dashed: sampleChannel(ch.dashed, t, env) === true,
      bbox,
      localBBox: geo.bbox,
    };
    if (terms) frame.terms = terms;
    if (emph) frame.emph = emph;
    if (marks.length) frame.marks = marks;
    if (obj.metadata?.name) frame.name = obj.metadata.name;
    if (obj.metadata?.description) frame.description = obj.metadata.description;

    objects.set(obj.id, {
      obj,
      spec,
      visible: presence > 0.001 && alpha > 0.001,
      alpha,
      matrix: m,
      rawMatrix: raw,
      params,
      localBBox: geo.bbox,
      bbox,
      layoutBBox,
      anchors,
      localAnchors,
      presence,
      frame,
    });
  }

  // Groups: bounds are the union of their children.
  for (const [gid, kids] of children) {
    const g = objects.get(gid);
    if (!g || !kids.length) continue;
    const b = bboxOf(gid);
    if (b) {
      g.bbox = b;
      g.layoutBBox = b;
      g.frame.bbox = b;
      g.anchors = { ...boxAnchors(b) };
    }
  }

  return { t, theme, objects, env, resolver, bboxOf, anchorOf };
}

export function cameraAt(cs: CompiledScene, t: number, env: SampleEnv): { cx: number; cy: number; zoom: number } {
  return {
    cx: sampleNumber(cs.camera.ch.cx, t, env, CANVAS.width / 2),
    cy: sampleNumber(cs.camera.ch.cy, t, env, CANVAS.height / 2),
    zoom: Math.max(0.05, sampleNumber(cs.camera.ch.zoom, t, env, 1)),
  };
}

/** Camera matrix: world -> screen. */
export function cameraMatrix(cam: { cx: number; cy: number; zoom: number }): Mat {
  return multiply(
    translation(CANVAS.width / 2, CANVAS.height / 2),
    multiply(scaling(cam.zoom), translation(-cam.cx, -cam.cy)),
  );
}

export function evaluate(cs: CompiledScene, tIn: number): FrameState {
  const ctx = evaluateObjects(cs, tIn);
  const t = ctx.t;
  const visible = [...ctx.objects.values()]
    .filter((e) => e.visible && e.obj.type !== "group")
    .map((e, i) => ({ f: e.frame, i }));
  visible.sort((a, b) => (LAYER_ORDER[a.f.layer] ?? 1) - (LAYER_ORDER[b.f.layer] ?? 1) || a.f.z - b.f.z || a.i - b.i);

  let caption: FrameState["caption"] = null;
  if (cs.captions && cs.beats.length) {
    let beat = cs.beats[0];
    for (const b of cs.beats) if (b.start <= t) beat = b;
    if (beat && beat.start <= t + 1e-9) {
      const span = beat.end - beat.start;
      caption = { beatId: beat.id, text: beat.text, progress: span > 0 ? clamp01((t - beat.start) / span) : 1 };
    }
  }

  let overlay: FrameState["overlay"] = null;
  for (const tr of cs.transitions) {
    if (t < tr.t0 || t > tr.t1) continue;
    const u = tr.t1 > tr.t0 ? (t - tr.t0) / (tr.t1 - tr.t0) : 1;
    const amount =
      tr.style === "fade_in"
        ? 1 - u
        : tr.style === "flash"
          ? 1 - u
          : tr.style === "wipe" || tr.style === "wipe_in"
            ? u
            : Math.sin(Math.PI * u);
    overlay = { style: tr.style, color: tr.color, amount };
  }

  return {
    sceneId: cs.id,
    t,
    camera: cameraAt(cs, t, ctx.env),
    background: cs.background,
    objects: visible.map((v) => v.f),
    caption,
    overlay,
  };
}

export { IDENTITY };
