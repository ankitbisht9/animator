/**
 * Arrows that join objects meet their outlines (ADR-060), as arrows bound to shapes do in diagram
 * tools: the arrow is aimed at the object, and drawn up to its edge.
 *
 * - An end given as an object's centre never runs into the object: when its gap leaves the head
 *   inside, the gap grows to stop it on the outline (a gap the model already widened to clear the
 *   object is kept). The `to` end is bound whenever the arrow comes from outside the object; the
 *   `from` end only when the arrow joins two objects (an arrow from a body's centre to a free point
 *   is a force acting at that centre).
 * - An end at a box corner of a round object (`top_left` of a circle) lands on the round outline,
 *   where the box corner would leave it hanging beside the object.
 *
 * Only objects whose outline is known exactly are bound: circles, ellipses, balls, badged icons,
 * polygons and box-shaped components. Larger objects than a node (a region, a planet) are left
 * alone: an arrow may point into them.
 */
import { polygonPoints } from "../catalog/primitives";
import { apply, invert, type Mat } from "../math/matrix";
import type { Rect } from "../math/rect";
import { dist, type Vec2 } from "../math/vec";

type Outline = { kind: "ellipse"; cx: number; cy: number; rx: number; ry: number } | { kind: "polygon"; pts: Vec2[] };

/** What binding needs of the object an end refers to. */
export interface BindTarget {
  type: string;
  params: Record<string, unknown>;
  localBBox: Rect;
  rawMatrix: Mat;
  /** The outline of a named part (a diagram's node `c2`), local frame; null when it has none. */
  partOutline?: (part: string) => Outline | null;
}

const ROUND = new Set(["circle", "ellipse", "ball"]);
/** Components drawn as (rounded) boxes filling their bounds. */
const BOXES = new Set([
  "rectangle",
  "card",
  "badge",
  "note",
  "label",
  "text",
  "equation",
  "readout",
  "title_card",
  "block",
  "image",
]);
/** Box corners, as directions from the centre. */
const CORNERS: Record<string, readonly [number, number]> = {
  top_left: [-1, -1],
  top_right: [1, -1],
  bottom_left: [-1, 1],
  bottom_right: [1, 1],
};
/** Half the smaller side (px) above which an object is a region an arrow may point into. */
export const NODE_HALF_MAX = 140;
/** The shortest arrow (px) binding may leave between two objects. */
const MIN_ARROW = 12;
/** Where a bound gap is kept (evaluated params only). */
export const BOUND_GAP = { startGap: "__startGap", endGap: "__endGap" } as const;

const scaleOf = (m: Mat): number => Math.sqrt(Math.abs(m[0] * m[3] - m[1] * m[2]));

/** Half the smaller side of an outline's bounds. */
function halfSideOf(o: Outline): number {
  if (o.kind === "ellipse") return Math.min(o.rx, o.ry);
  const xs = o.pts.map((p) => p.x);
  const ys = o.pts.map((p) => p.y);
  return Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / 2;
}

function outlineOf(e: BindTarget): Outline | null {
  const b = e.localBBox;
  if (!(b.w > 0 && b.h > 0)) return null;
  if (ROUND.has(e.type) || (e.type === "icon" && e.params.badge !== false)) {
    return { kind: "ellipse", cx: b.x + b.w / 2, cy: b.y + b.h / 2, rx: b.w / 2, ry: b.h / 2 };
  }
  if (e.type === "polygon") {
    const pts = polygonPoints(e.params);
    return pts.length >= 3 ? { kind: "polygon", pts } : null;
  }
  if (BOXES.has(e.type)) {
    return {
      kind: "polygon",
      pts: [
        { x: b.x, y: b.y },
        { x: b.x + b.w, y: b.y },
        { x: b.x + b.w, y: b.y + b.h },
        { x: b.x, y: b.y + b.h },
      ],
    };
  }
  return null;
}

/** On or inside the outline (local coordinates; `tol` px of slack). */
function inside(o: Outline, p: Vec2, tol = 0.5): boolean {
  if (o.kind === "ellipse") {
    const dx = (p.x - o.cx) / (o.rx + tol);
    const dy = (p.y - o.cy) / (o.ry + tol);
    return dx * dx + dy * dy <= 1;
  }
  let hit = false;
  const n = o.pts.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const a = o.pts[i] as Vec2;
    const b = o.pts[j] as Vec2;
    if (a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) hit = !hit;
    // On an edge (within tol).
    const ex = b.x - a.x;
    const ey = b.y - a.y;
    const l2 = ex * ex + ey * ey;
    if (l2 > 0) {
      const u = Math.max(0, Math.min(1, ((p.x - a.x) * ex + (p.y - a.y) * ey) / l2));
      if (Math.hypot(a.x + u * ex - p.x, a.y + u * ey - p.y) <= tol) return true;
    }
  }
  return hit;
}

/** Where the segment a -> b first crosses the outline (0..1 along it), or null. */
function firstCrossing(o: Outline, a: Vec2, b: Vec2): number | null {
  if (o.kind === "ellipse") {
    const ax = (a.x - o.cx) / o.rx;
    const ay = (a.y - o.cy) / o.ry;
    const dx = (b.x - a.x) / o.rx;
    const dy = (b.y - a.y) / o.ry;
    const A = dx * dx + dy * dy;
    if (A === 0) return null;
    const B = 2 * (ax * dx + ay * dy);
    const C = ax * ax + ay * ay - 1;
    const disc = B * B - 4 * A * C;
    if (disc < 0) return null;
    const s = (-B - Math.sqrt(disc)) / (2 * A);
    return s >= 0 && s <= 1 ? s : null;
  }
  let best: number | null = null;
  const n = o.pts.length;
  const rx = b.x - a.x;
  const ry = b.y - a.y;
  for (let i = 0; i < n; i++) {
    const p = o.pts[i] as Vec2;
    const q = o.pts[(i + 1) % n] as Vec2;
    const sx = q.x - p.x;
    const sy = q.y - p.y;
    const den = rx * sy - ry * sx;
    if (Math.abs(den) < 1e-12) continue;
    const s = ((p.x - a.x) * sy - (p.y - a.y) * sx) / den;
    const u = ((p.x - a.x) * ry - (p.y - a.y) * rx) / den;
    if (s >= 0 && s <= 1 && u >= 0 && u <= 1 && (best === null || s < best)) best = s;
  }
  return best;
}

/** The point on a round outline in the direction of a box corner (world coordinates), or null. */
function roundCorner(e: BindTarget, anchor: string): Vec2 | null {
  const dir = CORNERS[anchor];
  const o = outlineOf(e);
  if (!dir || !o || o.kind !== "ellipse") return null;
  const k = Math.SQRT1_2;
  return apply(e.rawMatrix, { x: o.cx + dir[0] * o.rx * k, y: o.cy + dir[1] * o.ry * k });
}

/**
 * How far from an object's centre (world px) the line from `from` to it crosses its outline, or null
 * when the arrow should not be bound there (no exact outline, a region, `from` inside the object).
 */
function clearance(e: BindTarget, center: Vec2, from: Vec2, part?: Outline): number | null {
  const o = part ?? outlineOf(e);
  if (!o) return null;
  const k = scaleOf(e.rawMatrix);
  const half = part ? halfSideOf(part) : Math.min(e.localBBox.w, e.localBBox.h) / 2;
  if (k === 0 || half * k > NODE_HALF_MAX) return null;
  const inv = invert(e.rawMatrix);
  const a = apply(inv, from);
  const c = apply(inv, center);
  if (inside(o, a)) return null;
  const s = firstCrossing(o, a, c);
  if (s === null) return null;
  const hit = apply(e.rawMatrix, { x: a.x + s * (c.x - a.x), y: a.y + s * (c.y - a.y) });
  return dist(hit, center);
}

/**
 * Bind an arrow's ends to the objects they refer to. `params.from` / `params.to` hold the resolved
 * points (world coordinates); `refs` the anchor references they came from; `unit` converts world px
 * to the arrow's own units (its group's frame may be scaled). Changes `params` in place.
 */
export function bindArrowEnds(
  params: Record<string, unknown>,
  refs: Record<string, string>,
  target: (id: string) => BindTarget | undefined,
  unit = 1,
): void {
  const split = (ref: string) => {
    const dot = ref.indexOf(".");
    return { id: ref.slice(0, dot), anchor: ref.slice(dot + 1) };
  };
  // Corners of round objects first: they move the ends the centres are aimed from.
  for (const key of ["from", "to"] as const) {
    const ref = refs[key];
    if (!ref) continue;
    const { id, anchor } = split(ref);
    const e = target(id);
    const p = e ? roundCorner(e, anchor) : null;
    if (p) params[key] = p;
  }
  const from = params.from as Vec2 | undefined;
  const to = params.to as Vec2 | undefined;
  if (!from || !to || typeof from.x !== "number" || typeof to.x !== "number") return;
  const gapOf = (key: string) => (typeof params[key] === "number" ? (params[key] as number) : 0);
  // The gap (in the arrow's units) that stops an end on the outline of the object it is aimed at.
  const needed = (key: "from" | "to", other: Vec2): number => {
    const ref = refs[key];
    if (!ref) return 0;
    const { id, anchor } = split(ref);
    const e = target(id);
    if (!e) return 0;
    // An object's centre, or the centre of a named part with an outline (a diagram's node, ADR-061).
    const part = anchor === "center" ? undefined : (e.partOutline?.(anchor) ?? undefined);
    if (anchor !== "center" && !part) return 0;
    const d = clearance(e, params[key] as Vec2, other, part);
    return d === null ? 0 : d * unit;
  };
  const endGap = Math.max(gapOf("endGap"), needed("to", from));
  const startGap = Math.max(gapOf("startGap"), refs.to !== undefined ? needed("from", to) : 0);
  // Objects drawn into each other leave no room for an arrow between them: it stays as written.
  if (startGap + endGap > dist(from, to) * unit - MIN_ARROW) return;
  // Kept beside the declared gaps (`arrowLayout` prefers them), so what the scene declares, and
  // carries into the next scene, stays as written.
  if (endGap !== gapOf("endGap")) params[BOUND_GAP.endGap] = endGap;
  if (startGap !== gapOf("startGap")) params[BOUND_GAP.startGap] = startGap;
}

/**
 * A pointer (a label's leader, a bubble's tail, a callout's pointer) aimed at a named part with an
 * outline (a diagram's node, ADR-061) stops on that outline, on the side it comes from, instead of
 * running over the part's text. Pointers at anything else stay as written.
 */
export function bindPointer(
  point: Vec2,
  ref: string,
  from: Vec2,
  target: (id: string) => BindTarget | undefined,
): Vec2 {
  const dot = ref.indexOf(".");
  const e = target(ref.slice(0, dot));
  const part = e?.partOutline?.(ref.slice(dot + 1));
  if (!e || !part) return point;
  const d = clearance(e, point, from, part);
  const len = dist(from, point);
  if (d === null || len <= d) return point;
  return { x: point.x + ((from.x - point.x) * d) / len, y: point.y + ((from.y - point.y) * d) / len };
}
