/**
 * Diagram layout (ADR-061): the model states a diagram's structure (nodes, edges, lanes) and this
 * computes where everything goes, so nodes never pile up and edges always meet their nodes.
 *
 * - `lanes`: a history on parallel lanes (git branches): each node one step after its parents and
 *   after the node before it on its lane, its lane's row; edges straight along a lane (bowed when they
 *   skip nodes), S-curves between lanes.
 * - `layered`: flowcharts, processes, trees, networks: ranks and order by dagre (Sugiyama), edges
 *   routed through dagre's bend points, so they pass between nodes.
 * - `ring`: a cycle: nodes round a circle in order, edges along the ring.
 *
 * Positions are in natural units (text at its size), centred on 0,0; the component scales the whole
 * drawing down to fit its box. Pure and deterministic: the same params give the same layout.
 */
import { layout as dagreLayout, graphlib } from "@dagrejs/dagre";
import type { Rect } from "../math/rect";
import type { Vec2 } from "../math/vec";
import { layoutText, type TextLayout } from "../text/layout";

export const DIAGRAM_LAYOUTS = ["layered", "lanes", "ring"] as const;
export const DIAGRAM_DIRECTIONS = ["right", "down", "left", "up"] as const;
export const NODE_SHAPES = ["circle", "box", "pill", "diamond"] as const;
export const EDGE_HEADS = ["end", "none", "both", "start"] as const;
export const DIAGRAM_SIZES = ["small", "medium", "large"] as const;

export type DiagramLayoutKind = (typeof DIAGRAM_LAYOUTS)[number];
export type NodeShape = (typeof NODE_SHAPES)[number];
export type EdgeHead = (typeof EDGE_HEADS)[number];

/** Named colors the lanes take in turn when they name none. */
export const LANE_COLORS = ["blue", "green", "orange", "purple", "pink", "red", "brown", "gray"] as const;

/** Text sizes and node sizes (canvas px) per diagram size. */
const SIZES = {
  small: {
    label: 22,
    note: 20,
    edge: 20,
    lane: 22,
    r: 24,
    boxW: 96,
    boxH: 46,
    padX: 14,
    padY: 9,
    wrap: 170,
    // spacing: steps and lanes apart (beyond the nodes), and at least (beyond the circles' size)
    stepPad: 40,
    lanePad: 44,
    stepMin: 56,
    laneMin: 52,
    sep: 0.8,
    rank: 0.5,
  },
  medium: {
    label: 26,
    note: 22,
    edge: 22,
    lane: 24,
    r: 30,
    boxW: 120,
    boxH: 56,
    padX: 18,
    padY: 12,
    wrap: 210,
    stepPad: 64,
    lanePad: 56,
    stepMin: 90,
    laneMin: 70,
    sep: 0.9,
    rank: 0.62,
  },
  large: {
    label: 30,
    note: 24,
    edge: 24,
    lane: 28,
    r: 36,
    boxW: 140,
    boxH: 64,
    padX: 22,
    padY: 14,
    wrap: 250,
    stepPad: 64,
    lanePad: 56,
    stepMin: 90,
    laneMin: 70,
    sep: 0.9,
    rank: 0.62,
  },
} as const;
/** The smallest text a diagram may be drawn at (canvas px): 22 px at 1080p, with a little room. */
export const DIAGRAM_READABLE_PX = 18.9;
type Sizes = (typeof SIZES)[keyof typeof SIZES];

/** One cubic Bezier piece of an edge. */
export interface Cubic {
  p0: Vec2;
  c1: Vec2;
  c2: Vec2;
  p3: Vec2;
}

export interface LaidNode {
  id: string;
  label: string;
  shape: NodeShape;
  cx: number;
  cy: number;
  w: number;
  h: number;
  text: TextLayout;
  note: { text: TextLayout; cx: number; cy: number } | null;
  color: string | null;
  ghost: boolean;
  lane: string | null;
  /** Place in the build order (staged reveal), 0 first. */
  order: number;
}

export interface LaidEdge {
  /** The edge's own id (to reveal or highlight it by name), or null. */
  id: string | null;
  from: string;
  to: string;
  head: EdgeHead;
  dashed: boolean;
  color: string | null;
  /** The stroke, from outline to outline (the shaft stops under an arrowhead). */
  shaft: Cubic[];
  /** Arrowheads: tip on the outline, unit direction pointing into the node. */
  heads: { tip: Vec2; dir: Vec2 }[];
  label: { text: TextLayout; cx: number; cy: number; w: number; h: number } | null;
  /** Middle of the stroke (an anchor). */
  mid: Vec2;
}

export interface LaidLane {
  id: string;
  label: string;
  color: string;
  /** The lane's name tag. */
  tag: { text: TextLayout; cx: number; cy: number; w: number; h: number };
}

export interface DiagramLayout {
  kind: DiagramLayoutKind;
  nodes: LaidNode[];
  edges: LaidEdge[];
  lanes: LaidLane[];
  /** Bounds of everything drawn, centred on 0,0. */
  bbox: Rect;
  sizes: Sizes;
  headSize: number;
}

interface NodeIn {
  id: string;
  label: string;
  shape: NodeShape | null;
  lane: string | null;
  step: number | null;
  note: string;
  color: string | null;
  ghost: boolean;
}

interface EdgeIn {
  id: string | null;
  from: string;
  to: string;
  label: string;
  head: EdgeHead | null;
  dashed: boolean;
  color: string | null;
}

interface LaneIn {
  id: string;
  label: string;
  color: string | null;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const strOrNull = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const pick = <T extends string>(v: unknown, allowed: readonly T[]): T | null =>
  typeof v === "string" && (allowed as readonly string[]).includes(v) ? (v as T) : null;

function nodesOf(p: Record<string, unknown>): NodeIn[] {
  const out: NodeIn[] = [];
  const seen = new Set<string>();
  for (const raw of Array.isArray(p.nodes) ? p.nodes : []) {
    const n = (raw ?? {}) as Record<string, unknown>;
    const id = str(n.id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({
      id,
      label: str(n.label),
      shape: pick(n.shape, NODE_SHAPES),
      lane: strOrNull(n.lane),
      step: typeof n.step === "number" && Number.isFinite(n.step) ? Math.max(0, Math.round(n.step)) : null,
      note: str(n.note),
      color: strOrNull(n.color),
      ghost: n.ghost === true,
    });
  }
  return out;
}

function edgesOf(p: Record<string, unknown>, nodes: Set<string>): EdgeIn[] {
  const out: EdgeIn[] = [];
  for (const raw of Array.isArray(p.edges) ? p.edges : []) {
    const e = (raw ?? {}) as Record<string, unknown>;
    const from = str(e.from);
    const to = str(e.to);
    if (!nodes.has(from) || !nodes.has(to) || from === to) continue;
    out.push({
      id: strOrNull(e.id),
      from,
      to,
      label: str(e.label),
      head: pick(e.head, EDGE_HEADS),
      dashed: e.dashed === true,
      color: strOrNull(e.color),
    });
  }
  return out;
}

function lanesOf(p: Record<string, unknown>, nodes: NodeIn[]): LaneIn[] {
  const out: LaneIn[] = [];
  const seen = new Set<string>();
  for (const raw of Array.isArray(p.lanes) ? p.lanes : []) {
    const l = (raw ?? {}) as Record<string, unknown>;
    const id = str(l.id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, label: str(l.label) || id, color: strOrNull(l.color) });
  }
  // Lanes the nodes name without declaring them come after, in the order first named.
  for (const n of nodes) {
    if (n.lane && !seen.has(n.lane)) {
      seen.add(n.lane);
      out.push({ id: n.lane, label: n.lane, color: null });
    }
  }
  return out;
}

// ------------------------------------------------------------------ shapes

/** Is `p` (relative to the node's centre) inside the node's shape grown by `pad`? */
export function insideNode(n: { shape: NodeShape; w: number; h: number }, dx: number, dy: number, pad = 0): boolean {
  const hw = n.w / 2 + pad;
  const hh = n.h / 2 + pad;
  switch (n.shape) {
    case "circle":
      return (dx / hw) ** 2 + (dy / hh) ** 2 <= 1;
    case "diamond":
      return Math.abs(dx) / hw + Math.abs(dy) / hh <= 1;
    case "pill": {
      const r = n.h / 2 + pad;
      const core = Math.max(0, n.w / 2 - n.h / 2);
      if (Math.abs(dx) <= core) return Math.abs(dy) <= hh;
      return (Math.abs(dx) - core) ** 2 + dy ** 2 <= r * r;
    }
    default:
      return Math.abs(dx) <= hw && Math.abs(dy) <= hh;
  }
}

/** Where a node's outline is in a direction from its centre (relative to the centre). */
export function outlinePoint(n: { shape: NodeShape; w: number; h: number }, ux: number, uy: number): Vec2 {
  const len = Math.hypot(ux, uy) || 1;
  const x = ux / len;
  const y = uy / len;
  // March out and refine (every shape is star-shaped around its centre).
  let lo = 0;
  let hi = Math.max(n.w, n.h);
  for (let i = 0; i < 30; i++) {
    const mid = (lo + hi) / 2;
    if (insideNode(n, x * mid, y * mid)) lo = mid;
    else hi = mid;
  }
  return { x: x * lo, y: y * lo };
}

function sizeNode(n: NodeIn, shape: NodeShape, S: Sizes): { w: number; h: number; text: TextLayout } {
  const text = layoutText(n.label, {
    size: S.label,
    weight: 700,
    align: "center",
    lineHeight: 1.15,
    ...(shape === "circle" ? {} : { maxWidth: S.wrap }),
  });
  switch (shape) {
    case "circle": {
      const r = Math.max(S.r, text.width / 2 + S.padX * 0.6, text.height / 2 + S.padY);
      return { w: 2 * r, h: 2 * r, text };
    }
    case "pill": {
      const h = Math.max(S.boxH * 0.85, text.height + 2 * S.padY);
      return { w: Math.max(S.boxW, text.width + 2 * S.padX + h * 0.5), h, text };
    }
    case "diamond": {
      // A text box w0 x h0 fits a diamond with half-diagonals w0 and h0 (its corners touch the edges).
      const w0 = text.width + 2 * S.padX;
      const h0 = text.height + 2 * S.padY;
      return { w: Math.max(S.boxW * 1.3, 2 * w0 * 1.05 + 12), h: Math.max(S.boxH * 1.3, 2 * h0 * 1.05 + 12), text };
    }
    default:
      return {
        w: Math.max(S.boxW, text.width + 2 * S.padX),
        h: Math.max(S.boxH, text.height + 2 * S.padY),
        text,
      };
  }
}

// ------------------------------------------------------------------ curves

const lerp = (a: Vec2, b: Vec2, t: number): Vec2 => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });

export function cubicAt(c: Cubic, t: number): Vec2 {
  const u = 1 - t;
  return {
    x: u * u * u * c.p0.x + 3 * u * u * t * c.c1.x + 3 * u * t * t * c.c2.x + t * t * t * c.p3.x,
    y: u * u * u * c.p0.y + 3 * u * u * t * c.c1.y + 3 * u * t * t * c.c2.y + t * t * t * c.p3.y,
  };
}

function cubicTangent(c: Cubic, t: number): Vec2 {
  const u = 1 - t;
  return {
    x: 3 * u * u * (c.c1.x - c.p0.x) + 6 * u * t * (c.c2.x - c.c1.x) + 3 * t * t * (c.p3.x - c.c2.x),
    y: 3 * u * u * (c.c1.y - c.p0.y) + 6 * u * t * (c.c2.y - c.c1.y) + 3 * t * t * (c.p3.y - c.c2.y),
  };
}

/** The piece of a cubic between t0 and t1. */
function cubicPart(c: Cubic, t0: number, t1: number): Cubic {
  const split = (k: Cubic, t: number): [Cubic, Cubic] => {
    const a = lerp(k.p0, k.c1, t);
    const b = lerp(k.c1, k.c2, t);
    const d = lerp(k.c2, k.p3, t);
    const ab = lerp(a, b, t);
    const bd = lerp(b, d, t);
    const m = lerp(ab, bd, t);
    return [
      { p0: k.p0, c1: a, c2: ab, p3: m },
      { p0: m, c1: bd, c2: d, p3: k.p3 },
    ];
  };
  const right = t0 > 0 ? split(c, t0)[1] : c;
  const tt = t0 < 1 ? (t1 - t0) / (1 - t0) : 1;
  return tt < 1 ? split(right, tt)[0] : right;
}

const line = (a: Vec2, b: Vec2): Cubic => ({ p0: a, c1: lerp(a, b, 1 / 3), c2: lerp(a, b, 2 / 3), p3: b });

/** A smooth curve through points (Catmull-Rom as cubic pieces). */
function through(points: Vec2[]): Cubic[] {
  if (points.length < 2) return [];
  if (points.length === 2) return [line(points[0] as Vec2, points[1] as Vec2)];
  const out: Cubic[] = [];
  for (let i = 0; i + 1 < points.length; i++) {
    const p0 = points[Math.max(0, i - 1)] as Vec2;
    const p1 = points[i] as Vec2;
    const p2 = points[i + 1] as Vec2;
    const p3 = points[Math.min(points.length - 1, i + 2)] as Vec2;
    out.push({
      p0: p1,
      c1: { x: p1.x + (p2.x - p0.x) / 6, y: p1.y + (p2.y - p0.y) / 6 },
      c2: { x: p2.x - (p3.x - p1.x) / 6, y: p2.y - (p3.y - p1.y) / 6 },
      p3: p2,
    });
  }
  return out;
}

/** Points along a curve (for bounds and clearance tests). */
export function sampleCurve(curve: Cubic[], perPiece = 16): Vec2[] {
  const out: Vec2[] = [];
  curve.forEach((c, i) => {
    for (let k = i === 0 ? 0 : 1; k <= perPiece; k++) out.push(cubicAt(c, k / perPiece));
  });
  return out;
}

type Pos = { piece: number; t: number };

/**
 * The curve from where it leaves `a`'s shape (grown by `padA`) to where it enters `b`'s (grown by
 * `padB`), or null when the nodes touch and nothing of it is outside them.
 */
function clip(curve: Cubic[], a: LaidNode, padA: number, b: LaidNode, padB: number): Cubic[] | null {
  const n = curve.length;
  const outsideA = (p: Vec2) => !insideNode(a, p.x - a.cx, p.y - a.cy, padA);
  const outsideB = (p: Vec2) => !insideNode(b, p.x - b.cx, p.y - b.cy, padB);
  const at = (q: Pos) => cubicAt(curve[q.piece] as Cubic, q.t);
  const steps = 48;
  // First point outside a, walking forward.
  let start: Pos | null = null;
  outer: for (let i = 0; i < n; i++) {
    let prev = 0;
    for (let k = 0; k <= steps; k++) {
      const t = k / steps;
      if (outsideA(at({ piece: i, t }))) {
        let lo = prev;
        let hi = t;
        for (let r = 0; r < 24; r++) {
          const mid = (lo + hi) / 2;
          if (outsideA(at({ piece: i, t: mid }))) hi = mid;
          else lo = mid;
        }
        start = { piece: i, t: hi };
        break outer;
      }
      prev = t;
    }
  }
  // Last point outside b, walking back.
  let end: Pos | null = null;
  outer2: for (let i = n - 1; i >= 0; i--) {
    let prev = 1;
    for (let k = steps; k >= 0; k--) {
      const t = k / steps;
      if (outsideB(at({ piece: i, t }))) {
        let lo = t;
        let hi = prev;
        for (let r = 0; r < 24; r++) {
          const mid = (lo + hi) / 2;
          if (outsideB(at({ piece: i, t: mid }))) lo = mid;
          else hi = mid;
        }
        end = { piece: i, t: lo };
        break outer2;
      }
      prev = t;
    }
  }
  if (!start || !end) return null;
  if (start.piece > end.piece || (start.piece === end.piece && start.t >= end.t)) return null;
  const out: Cubic[] = [];
  for (let i = start.piece; i <= end.piece; i++) {
    const c = curve[i] as Cubic;
    const t0 = i === start.piece ? start.t : 0;
    const t1 = i === end.piece ? end.t : 1;
    if (t1 - t0 > 1e-6) out.push(cubicPart(c, t0, t1));
  }
  return out.length ? out : null;
}

function bounds(points: Vec2[]): Rect | null {
  if (!points.length) return null;
  let x0 = Number.POSITIVE_INFINITY;
  let y0 = Number.POSITIVE_INFINITY;
  let x1 = Number.NEGATIVE_INFINITY;
  let y1 = Number.NEGATIVE_INFINITY;
  for (const p of points) {
    x0 = Math.min(x0, p.x);
    y0 = Math.min(y0, p.y);
    x1 = Math.max(x1, p.x);
    y1 = Math.max(y1, p.y);
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

// ------------------------------------------------------------------ layouts

interface Placed {
  cx: number;
  cy: number;
}

interface Built {
  pos: Map<string, Placed>;
  /** Each edge's centre line, node centre to node centre (index as in `edges`). */
  routes: Vec2[][] | Cubic[][];
  /** Edge label centres (when the layout chose them). */
  labelAt: (Vec2 | null)[];
  order: Map<string, number>;
  lanes: LaidLane[];
}

function lanesLayout(
  nodes: NodeIn[],
  sized: Map<string, { w: number; h: number; noteH: number }>,
  edges: EdgeIn[],
  lanes: LaneIn[],
  dir: string,
  S: Sizes,
): Built {
  const laneIds = lanes.length ? lanes.map((l) => l.id) : ["_"];
  const laneOf = (n: NodeIn) => (n.lane && laneIds.includes(n.lane) ? n.lane : (laneIds[0] as string));
  const laneIndex = new Map(laneIds.map((id, i) => [id, i]));
  const parents = new Map<string, string[]>();
  for (const e of edges) parents.set(e.to, [...(parents.get(e.to) ?? []), e.from]);
  // Steps: after its parents, after the node before it on its lane, at least its own `step`.
  const step = new Map<string, number>(nodes.map((n) => [n.id, n.step ?? 0]));
  for (let pass = 0; pass <= nodes.length + 1; pass++) {
    let changed = false;
    const lastOnLane = new Map<string, number>();
    for (const n of nodes) {
      let s = n.step ?? 0;
      for (const p of parents.get(n.id) ?? []) s = Math.max(s, (step.get(p) ?? 0) + 1);
      const prev = lastOnLane.get(laneOf(n));
      if (prev !== undefined) s = Math.max(s, prev + 1);
      s = Math.min(s, 400);
      if (s !== step.get(n.id)) {
        step.set(n.id, s);
        changed = true;
      }
      lastOnLane.set(laneOf(n), s);
    }
    if (!changed) break;
  }
  const maxW = Math.max(...[...sized.values()].map((s) => s.w));
  const maxH = Math.max(...[...sized.values()].map((s) => s.h + s.noteH));
  const across = dir === "right" || dir === "left";
  const stepGap = across
    ? Math.max(maxW + S.stepPad, S.r * 2 + S.stepMin)
    : Math.max(maxH + S.lanePad, S.r * 2 + S.stepMin - 10);
  const laneGap = across
    ? Math.max(maxH + S.lanePad, S.r * 2 + S.laneMin)
    : Math.max(maxW + S.lanePad + 4, S.r * 2 + S.stepMin);
  const sign = dir === "left" || dir === "up" ? -1 : 1;
  const pos = new Map<string, Placed>();
  for (const n of nodes) {
    const s = (step.get(n.id) ?? 0) * stepGap * sign;
    const l = (laneIndex.get(laneOf(n)) ?? 0) * laneGap;
    pos.set(n.id, across ? { cx: s, cy: l } : { cx: l, cy: s });
  }
  // Edges: along a lane straight (bowed when nodes of the lane lie between their ends). Across lanes
  // as git graphs draw them: from a fork the edge turns into the child's lane at once and runs along
  // it; into a merge it runs along the parent's lane and turns at the last step; whichever lane is
  // clear of other nodes between the two (an S-curve halfway when neither is).
  const at = (lane: string, s: number): Vec2 => {
    const along = s * stepGap * sign;
    const row = (laneIndex.get(lane) ?? 0) * laneGap;
    return across ? { x: along, y: row } : { x: row, y: along };
  };
  const turn = (p: Vec2, q: Vec2): Cubic => {
    const half = across ? (q.x - p.x) / 2 : (q.y - p.y) / 2;
    return across
      ? { p0: p, c1: { x: p.x + half, y: p.y }, c2: { x: q.x - half, y: q.y }, p3: q }
      : { p0: p, c1: { x: p.x, y: p.y + half }, c2: { x: q.x, y: q.y - half }, p3: q };
  };
  const busy = (lane: string, s0: number, s1: number, except: string[]) =>
    nodes.some((n) => {
      if (laneOf(n) !== lane || except.includes(n.id)) return false;
      const s = step.get(n.id) ?? 0;
      return s > Math.min(s0, s1) && s < Math.max(s0, s1);
    });
  const routes: Cubic[][] = edges.map((e) => {
    const a = pos.get(e.from) as Placed;
    const b = pos.get(e.to) as Placed;
    const pa = { x: a.cx, y: a.cy };
    const pb = { x: b.cx, y: b.cy };
    const la = laneOf(nodes.find((n) => n.id === e.from) as NodeIn);
    const lb = laneOf(nodes.find((n) => n.id === e.to) as NodeIn);
    const sa = step.get(e.from) ?? 0;
    const sb = step.get(e.to) ?? 0;
    const ends = [e.from, e.to];
    // The empty corridor half a lane beside a lane (towards `toward`: -1 the lane before, 1 after).
    const corridor = (lane: string, s0: number, s1: number, toward: number): Cubic[] => {
      const off = toward * 0.5 * laneGap;
      const shift = (p: Vec2): Vec2 => (across ? { x: p.x, y: p.y + off } : { x: p.x + off, y: p.y });
      const dir = s1 > s0 ? 1 : -1;
      const c0 = shift(at(lane, s0 + dir));
      const c1 = shift(at(lane, s1 - dir));
      const out: Cubic[] = [turn(pa, c0)];
      if (Math.abs(s1 - s0) > 2) out.push(line(c0, c1));
      out.push(turn(c1, pb));
      return out;
    };
    if (la === lb) {
      if (!busy(la, sa, sb, ends)) return [line(pa, pb)];
      // Nodes of the lane between its ends: round them, beside the lane.
      return corridor(la, sa, sb, -1);
    }
    if (sb - sa >= 2 && !busy(lb, sa, sb, ends)) {
      const corner = at(lb, sa + 1);
      return [turn(pa, corner), line(corner, pb)];
    }
    if (sb - sa >= 2 && !busy(la, sa, sb, ends)) {
      const corner = at(la, sb - 1);
      return [line(pa, corner), turn(corner, pb)];
    }
    if (sb - sa >= 2) {
      // Both lanes busy: along the empty corridor beside the parent's lane, towards the child's.
      return corridor(la, sa, sb, (laneIndex.get(lb) ?? 0) > (laneIndex.get(la) ?? 0) ? 1 : -1);
    }
    return [turn(pa, pb)];
  });
  // Build order: step by step, lane by lane.
  const ranked = [...nodes].sort(
    (x, y) =>
      (step.get(x.id) ?? 0) - (step.get(y.id) ?? 0) ||
      (laneIndex.get(laneOf(x)) ?? 0) - (laneIndex.get(laneOf(y)) ?? 0),
  );
  const order = new Map(ranked.map((n, i) => [n.id, i]));
  // Lane tags: one column before the lanes, each on its lane's row (above the lanes when they run down).
  const laneInfo: LaneIn[] = lanes.length ? lanes : [];
  const tags = laneInfo.map((l, i) => {
    const text = layoutText(l.label, { size: S.lane, weight: 700, align: "center", lineHeight: 1.15 });
    return { lane: l, i, text, w: text.width + S.lane * 1.1, h: text.height + S.lane * 0.56 };
  });
  const firstAlong = Math.min(
    ...nodes.map((n) => (across ? (pos.get(n.id) as Placed).cx : (pos.get(n.id) as Placed).cy) * sign),
  );
  const lanesOut: LaidLane[] = tags.map((g, k) => {
    const color = g.lane.color ?? (LANE_COLORS[k % LANE_COLORS.length] as string);
    const along = (firstAlong - (across ? maxW / 2 : maxH / 2) - 24 - (across ? g.w : g.h) / 2) * sign;
    const row = g.i * laneGap;
    return {
      id: g.lane.id,
      label: g.lane.label,
      color,
      tag: { text: g.text, cx: across ? along : row, cy: across ? row : along, w: g.w, h: g.h },
    };
  });
  return { pos, routes, labelAt: edges.map(() => null), order, lanes: lanesOut };
}

function layeredLayout(
  nodes: NodeIn[],
  sized: Map<string, { w: number; h: number; noteH: number }>,
  edges: EdgeIn[],
  edgeLabels: ({ w: number; h: number } | null)[],
  dir: string,
  S: Sizes,
): Built {
  const g = new graphlib.Graph({ multigraph: true });
  const rankdir = dir === "down" ? "TB" : dir === "up" ? "BT" : dir === "left" ? "RL" : "LR";
  g.setGraph({
    rankdir,
    nodesep: Math.round(S.boxH * S.sep),
    ranksep: Math.round(S.boxW * S.rank),
    edgesep: 18,
    marginx: 0,
    marginy: 0,
    ranker: "network-simplex",
  });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) {
    const s = sized.get(n.id) as { w: number; h: number; noteH: number };
    g.setNode(n.id, { width: s.w, height: s.h + s.noteH });
  }
  edges.forEach((e, i) => {
    const lab = edgeLabels[i];
    g.setEdge(
      e.from,
      e.to,
      lab ? { minlen: 1, weight: 1, width: lab.w, height: lab.h, labelpos: "c" } : { minlen: 1, weight: 1 },
      `e${i}`,
    );
  });
  dagreLayout(g);
  const pos = new Map<string, Placed>();
  for (const n of nodes) {
    const d = g.node(n.id) as { x: number; y: number };
    const s = sized.get(n.id) as { noteH: number };
    // dagre centres the node with its note; the shape sits at the top of that box.
    pos.set(n.id, { cx: d.x, cy: d.y - s.noteH / 2 });
  }
  // Edges leave a node and enter the next along the flow (as in diagram tools), then turn through
  // dagre's bend points in the gaps between ranks: a steep edge never cuts through a node beside its
  // own in the same rank.
  const across = rankdir === "LR" || rankdir === "RL";
  const axis: Vec2 =
    rankdir === "LR"
      ? { x: 1, y: 0 }
      : rankdir === "RL"
        ? { x: -1, y: 0 }
        : rankdir === "TB"
          ? { x: 0, y: 1 }
          : { x: 0, y: -1 };
  const lead = Math.round(S.boxW * S.rank * 0.35);
  const ahead = (e: EdgeIn) => {
    const a = pos.get(e.from) as Placed;
    const b = pos.get(e.to) as Placed;
    return (b.cx - a.cx) * axis.x + (b.cy - a.cy) * axis.y >= 0 ? 1 : -1;
  };
  // Ports: edges leaving (or entering) one side of a node are spread along that side, in the order
  // of where they go, so they never share their first stretch.
  const cross = (p: Placed) => (across ? p.cy : p.cx);
  const ports = new Map<string, number>();
  const spread = (
    key: (e: EdgeIn) => string,
    other: (e: EdgeIn) => string,
    own: (e: EdgeIn) => string,
    tag: string,
  ) => {
    const sides = new Map<string, number[]>();
    edges.forEach((e, i) => sides.set(key(e), [...(sides.get(key(e)) ?? []), i]));
    for (const list of sides.values()) {
      if (list.length < 2) continue;
      const first = edges[list[0] as number] as EdgeIn;
      const size = sized.get(own(first)) as { w: number; h: number };
      const room = (across ? size.h : size.w) * 0.55;
      const step = Math.min(16, room / (list.length - 1));
      const sorted = [...list].sort(
        (x, y) =>
          cross(pos.get(other(edges[x] as EdgeIn)) as Placed) - cross(pos.get(other(edges[y] as EdgeIn)) as Placed) ||
          x - y,
      );
      sorted.forEach((i, k) => ports.set(`${tag}${i}`, (k - (sorted.length - 1) / 2) * step));
    }
  };
  spread(
    (e) => `${e.from}|${ahead(e)}`,
    (e) => e.to,
    (e) => e.from,
    "out",
  );
  spread(
    (e) => `${e.to}|${-ahead(e)}`,
    (e) => e.from,
    (e) => e.to,
    "in",
  );
  const perp: Vec2 = { x: axis.y, y: axis.x };
  const routes: Vec2[][] = [];
  const labelAt: (Vec2 | null)[] = [];
  edges.forEach((e, i) => {
    const d = g.edge({ v: e.from, w: e.to, name: `e${i}` }) as { points?: Vec2[]; x?: number; y?: number } | undefined;
    const a = pos.get(e.from) as Placed;
    const b = pos.get(e.to) as Placed;
    const sa = sized.get(e.from) as { w: number; h: number };
    const sb = sized.get(e.to) as { w: number; h: number };
    const dir = ahead(e);
    const reach = (sz: { w: number; h: number }) => (across ? sz.w : sz.h) / 2 + lead;
    const po = ports.get(`out${i}`) ?? 0;
    const pi = ports.get(`in${i}`) ?? 0;
    const from = { x: a.cx + perp.x * po, y: a.cy + perp.y * po };
    const to = { x: b.cx + perp.x * pi, y: b.cy + perp.y * pi };
    const exit = { x: from.x + axis.x * dir * reach(sa), y: from.y + axis.y * dir * reach(sa) };
    const enter = { x: to.x - axis.x * dir * reach(sb), y: to.y - axis.y * dir * reach(sb) };
    const inner = (d?.points ?? []).slice(1, -1);
    routes.push([from, exit, ...inner, enter, to]);
    labelAt.push(edgeLabels[i] && typeof d?.x === "number" && typeof d?.y === "number" ? { x: d.x, y: d.y } : null);
  });
  // Build order: by rank (dagre's), then along the rank.
  const ranked = [...nodes].sort((x, y) => {
    const dx = g.node(x.id) as { x: number; y: number; rank?: number };
    const dy = g.node(y.id) as { x: number; y: number; rank?: number };
    return (dx.rank ?? 0) - (dy.rank ?? 0) || (across ? dx.y - dy.y : dx.x - dy.x);
  });
  return { pos, routes, labelAt, order: new Map(ranked.map((n, i) => [n.id, i])), lanes: [] };
}

function ringLayout(
  nodes: NodeIn[],
  sized: Map<string, { w: number; h: number; noteH: number }>,
  edges: EdgeIn[],
  dir: string,
  S: Sizes,
): Built {
  const n = nodes.length;
  const span = Math.max(...[...sized.values()].map((s) => Math.max(s.w, s.h + s.noteH)));
  const R = Math.max(S.r * 3, (n * (span + 48)) / (2 * Math.PI));
  const sign = dir === "left" || dir === "up" ? -1 : 1;
  const angle = new Map(nodes.map((nd, i) => [nd.id, -Math.PI / 2 + (sign * 2 * Math.PI * i) / n]));
  const pos = new Map<string, Placed>();
  for (const nd of nodes) {
    const a = angle.get(nd.id) as number;
    pos.set(nd.id, { cx: R * Math.cos(a), cy: R * Math.sin(a) });
  }
  const index = new Map(nodes.map((nd, i) => [nd.id, i]));
  const routes: Cubic[][] = edges.map((e) => {
    const i = index.get(e.from) as number;
    const j = index.get(e.to) as number;
    const a = pos.get(e.from) as Placed;
    const b = pos.get(e.to) as Placed;
    const neighbours = n > 2 && ((i + 1) % n === j || (j + 1) % n === i);
    if (!neighbours) {
      // Across the ring: bowed in towards its centre, clear of the nodes on the ring between them.
      const pa = { x: a.cx, y: a.cy };
      const pb = { x: b.cx, y: b.cy };
      return [{ p0: pa, c1: lerp(pa, { x: 0, y: 0 }, 0.55), c2: lerp(pb, { x: 0, y: 0 }, 0.55), p3: pb }];
    }
    // Along the ring: the short arc between the two angles, as one cubic.
    const a0 = angle.get(e.from) as number;
    let a1 = angle.get(e.to) as number;
    while (a1 - a0 > Math.PI) a1 -= 2 * Math.PI;
    while (a0 - a1 > Math.PI) a1 += 2 * Math.PI;
    const k = (4 / 3) * Math.tan((a1 - a0) / 4) * R;
    const p0 = { x: R * Math.cos(a0), y: R * Math.sin(a0) };
    const p3 = { x: R * Math.cos(a1), y: R * Math.sin(a1) };
    return [
      {
        p0,
        c1: { x: p0.x - k * Math.sin(a0), y: p0.y + k * Math.cos(a0) },
        c2: { x: p3.x + k * Math.sin(a1), y: p3.y - k * Math.cos(a1) },
        p3,
      },
    ];
  });
  return {
    pos,
    routes,
    labelAt: edges.map(() => null),
    order: new Map(nodes.map((nd, i) => [nd.id, i])),
    lanes: [],
  };
}

// ------------------------------------------------------------------ the layout

/** Everything about a diagram that decides its layout (not its box: the fit is applied after). */
export function diagramKey(p: Record<string, unknown>): string {
  return JSON.stringify([p.layout, p.direction, p.size, p.nodes, p.edges, p.lanes]);
}

const cache = new Map<string, DiagramLayout>();

/** The diagram's layout (cached: the same structure gives the same object). */
export function diagramLayout(p: Record<string, unknown>): DiagramLayout {
  const key = diagramKey(p);
  const hit = cache.get(key);
  if (hit) return hit;
  const out = computeLayout(p);
  if (cache.size >= 64) cache.clear();
  cache.set(key, out);
  return out;
}

function computeLayout(p: Record<string, unknown>): DiagramLayout {
  const kind = pick(p.layout, DIAGRAM_LAYOUTS) ?? "layered";
  const dir = pick(p.direction, DIAGRAM_DIRECTIONS) ?? "right";
  const S = SIZES[pick(p.size, DIAGRAM_SIZES) ?? "medium"];
  const headSize = Math.round(S.label * 0.62);
  const nodesIn = nodesOf(p);
  const ids = new Set(nodesIn.map((n) => n.id));
  const edgesIn = edgesOf(p, ids);
  const lanesIn = kind === "lanes" ? lanesOf(p, nodesIn) : [];
  const defaultShape: NodeShape = kind === "layered" ? "box" : kind === "ring" ? "pill" : "circle";
  const shapes = new Map(nodesIn.map((n) => [n.id, n.shape ?? defaultShape]));
  const sized = new Map<string, { w: number; h: number; noteH: number; text: TextLayout; note: TextLayout | null }>();
  for (const n of nodesIn) {
    const s = sizeNode(n, shapes.get(n.id) as NodeShape, S);
    const note = n.note
      ? layoutText(n.note, { size: S.note, weight: 400, align: "center", lineHeight: 1.15, maxWidth: S.wrap * 1.1 })
      : null;
    sized.set(n.id, { w: s.w, h: s.h, noteH: note ? note.height + 8 : 0, text: s.text, note });
  }
  // Circles of one diagram are one size: the largest any label needs.
  const circleW = Math.max(
    0,
    ...nodesIn.filter((n) => shapes.get(n.id) === "circle").map((n) => (sized.get(n.id) as { w: number }).w),
  );
  for (const n of nodesIn) {
    const s = sized.get(n.id) as { w: number; h: number };
    if (shapes.get(n.id) === "circle") {
      s.w = circleW;
      s.h = circleW;
    }
  }
  const edgeLabels = edgesIn.map((e) => {
    if (!e.label) return null;
    const text = layoutText(e.label, { size: S.edge, weight: 600, align: "center", lineHeight: 1.15 });
    return { text, w: text.width + S.edge * 0.9, h: text.height + S.edge * 0.45 };
  });
  if (!nodesIn.length) {
    return { kind, nodes: [], edges: [], lanes: [], bbox: { x: 0, y: 0, w: 0, h: 0 }, sizes: S, headSize };
  }
  const built =
    kind === "lanes"
      ? lanesLayout(nodesIn, sized, edgesIn, lanesIn, dir, S)
      : kind === "ring"
        ? ringLayout(nodesIn, sized, edgesIn, dir, S)
        : layeredLayout(nodesIn, sized, edgesIn, edgeLabels, dir, S);
  const laneColor = new Map(built.lanes.map((l) => [l.id, l.color]));
  const nodes: LaidNode[] = nodesIn.map((n) => {
    const s = sized.get(n.id) as { w: number; h: number; text: TextLayout; note: TextLayout | null };
    const at = built.pos.get(n.id) as Placed;
    return {
      id: n.id,
      label: n.label,
      shape: shapes.get(n.id) as NodeShape,
      cx: at.cx,
      cy: at.cy,
      w: s.w,
      h: s.h,
      text: s.text,
      note: s.note ? { text: s.note, cx: at.cx, cy: at.cy + s.h / 2 + 8 + s.note.height / 2 } : null,
      color: n.color ?? (kind === "lanes" && n.lane ? (laneColor.get(n.lane) ?? null) : null),
      ghost: n.ghost,
      lane: kind === "lanes" ? (n.lane ?? built.lanes[0]?.id ?? null) : null,
      order: built.order.get(n.id) ?? 0,
    };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges: LaidEdge[] = edgesIn.map((e, i) => {
    const a = byId.get(e.from) as LaidNode;
    const b = byId.get(e.to) as LaidNode;
    const route = built.routes[i] as Vec2[] | Cubic[];
    const curve: Cubic[] = route.length && "p0" in (route[0] as object) ? (route as Cubic[]) : through(route as Vec2[]);
    const head = e.head ?? (kind === "lanes" ? "none" : "end");
    const endHead = head === "end" || head === "both";
    const startHead = head === "start" || head === "both";
    const gap = 4;
    // The tips touch the outlines (plus a small gap); the stroke stops under each arrowhead.
    const tipped = clip(curve, a, gap, b, gap) ?? [];
    const shaft =
      clip(curve, a, gap + (startHead ? headSize * 0.6 : 0), b, gap + (endHead ? headSize * 0.6 : 0)) ?? tipped;
    const heads: { tip: Vec2; dir: Vec2 }[] = [];
    const unit = (v: Vec2) => {
      const l = Math.hypot(v.x, v.y) || 1;
      return { x: v.x / l, y: v.y / l };
    };
    if (tipped.length && endHead) {
      const last = tipped[tipped.length - 1] as Cubic;
      heads.push({ tip: last.p3, dir: unit(cubicTangent(last, 1)) });
    }
    if (tipped.length && startHead) {
      const first = tipped[0] as Cubic;
      const t = cubicTangent(first, 0);
      heads.push({ tip: first.p0, dir: unit({ x: -t.x, y: -t.y }) });
    }
    const pts = sampleCurve(tipped.length ? tipped : curve, 12);
    const mid = pts[Math.floor(pts.length / 2)] ?? { x: (a.cx + b.cx) / 2, y: (a.cy + b.cy) / 2 };
    const lab = edgeLabels[i];
    let label: LaidEdge["label"] = null;
    if (lab) {
      const at = built.labelAt[i];
      if (at) label = { text: lab.text, cx: at.x, cy: at.y, w: lab.w, h: lab.h };
      else {
        // Beside the middle of the stroke, off to its left (above a stroke going right).
        const k = Math.floor(pts.length / 2);
        const t = unit({
          x: (pts[Math.min(pts.length - 1, k + 1)] as Vec2).x - (pts[Math.max(0, k - 1)] as Vec2).x,
          y: (pts[Math.min(pts.length - 1, k + 1)] as Vec2).y - (pts[Math.max(0, k - 1)] as Vec2).y,
        });
        const off = (Math.abs(t.y) * lab.w) / 2 + (Math.abs(t.x) * lab.h) / 2 + 8;
        label = { text: lab.text, cx: mid.x + t.y * off, cy: mid.y - t.x * off, w: lab.w, h: lab.h };
      }
    }
    return {
      id: e.id,
      from: e.from,
      to: e.to,
      head,
      dashed: e.dashed,
      color: e.color,
      shaft,
      heads,
      label,
      mid,
    };
  });
  // Bounds of everything, then centre it all on 0,0.
  const pts: Vec2[] = [];
  const box = (cx: number, cy: number, w: number, h: number) =>
    pts.push({ x: cx - w / 2, y: cy - h / 2 }, { x: cx + w / 2, y: cy + h / 2 });
  for (const n of nodes) {
    box(n.cx, n.cy, n.w, n.h);
    if (n.note) box(n.note.cx, n.note.cy, n.note.text.width, n.note.text.height);
  }
  for (const e of edges) {
    pts.push(...sampleCurve(e.shaft, 8));
    for (const h of e.heads) pts.push(h.tip);
    if (e.label) box(e.label.cx, e.label.cy, e.label.w, e.label.h);
  }
  for (const l of built.lanes) box(l.tag.cx, l.tag.cy, l.tag.w, l.tag.h);
  const b = bounds(pts) ?? { x: 0, y: 0, w: 0, h: 0 };
  const ox = b.x + b.w / 2;
  const oy = b.y + b.h / 2;
  const mv = (p: Vec2): Vec2 => ({ x: p.x - ox, y: p.y - oy });
  const mc = (c: Cubic): Cubic => ({ p0: mv(c.p0), c1: mv(c.c1), c2: mv(c.c2), p3: mv(c.p3) });
  return {
    kind,
    nodes: nodes.map((n) => ({
      ...n,
      cx: n.cx - ox,
      cy: n.cy - oy,
      note: n.note ? { ...n.note, cx: n.note.cx - ox, cy: n.note.cy - oy } : null,
    })),
    edges: edges.map((e) => ({
      ...e,
      shaft: e.shaft.map(mc),
      heads: e.heads.map((h) => ({ tip: mv(h.tip), dir: h.dir })),
      label: e.label ? { ...e.label, cx: e.label.cx - ox, cy: e.label.cy - oy } : null,
      mid: mv(e.mid),
    })),
    lanes: built.lanes.map((l) => ({ ...l, tag: { ...l.tag, cx: l.tag.cx - ox, cy: l.tag.cy - oy } })),
    bbox: { x: b.x - ox, y: b.y - oy, w: b.w, h: b.h },
    sizes: S,
    headSize,
  };
}

/** The smallest text the diagram draws (canvas px, at its natural size). */
export function smallestText(l: DiagramLayout): number {
  const S = l.sizes;
  const sizes: number[] = [S.label];
  if (l.nodes.some((n) => n.note)) sizes.push(S.note);
  if (l.edges.some((e) => e.label)) sizes.push(S.edge);
  if (l.lanes.length) sizes.push(S.lane);
  return Math.min(...sizes);
}

/** The scale that fits the layout in a `width` x `height` box: never above 1 (text keeps its size),
 * never below what keeps its text readable (it then takes more room than it was given). */
export function diagramFit(l: DiagramLayout, width: number, height: number): number {
  if (l.bbox.w <= 0 || l.bbox.h <= 0) return 1;
  const fit = Math.min(1, width / l.bbox.w, height / l.bbox.h);
  return Math.max(fit, Math.min(1, DIAGRAM_READABLE_PX / smallestText(l)));
}

/** The room it needs to stay readable when that is more than `width` x `height`, else null. */
export function diagramNeeds(l: DiagramLayout, width: number, height: number): { w: number; h: number } | null {
  if (l.bbox.w <= 0 || l.bbox.h <= 0) return null;
  const k = diagramFit(l, width, height);
  const w = l.bbox.w * k;
  const h = l.bbox.h * k;
  return w > width + 0.5 || h > height + 0.5 ? { w: Math.ceil(w), h: Math.ceil(h) } : null;
}

/** How far each part is shown at draw/reveal progress `u` (0..1): nodes in build order, each edge
 * after both its nodes, lane tags first. */
export function stagedReveal(l: DiagramLayout, u: number): { node: (id: string) => number; lane: number } {
  if (u >= 1) return { node: () => 1, lane: 1 };
  const total = Math.max(1, l.nodes.length);
  const order = new Map(l.nodes.map((n) => [n.id, n.order]));
  const lane = Math.max(0, Math.min(1, u * (total + 1) * 2));
  return {
    node: (id: string) => Math.max(0, Math.min(1, u * (total + 1) - (order.get(id) ?? 0))),
    lane,
  };
}
