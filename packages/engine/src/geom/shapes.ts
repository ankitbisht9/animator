/**
 * Outlines of the basic shapes as path data (local coordinates), and arc-length
 * resampling of an outline into a fixed number of points (used by `morph`).
 */
import type { Params } from "../catalog/spec";
import type { Vec2 } from "../math/vec";
import { parsePath, type Seg, subpaths } from "./path";

const n = (p: Params, k: string, d: number) => (typeof p[k] === "number" ? (p[k] as number) : d);

export function roundedRectPath(w: number, h: number, r0: number): string {
  const r = Math.max(0, Math.min(r0, w / 2, h / 2));
  const x = -w / 2;
  const y = -h / 2;
  if (r === 0) return `M${x} ${y}H${x + w}V${y + h}H${x}Z`;
  return (
    `M${x + r} ${y}H${x + w - r}A${r} ${r} 0 0 1 ${x + w} ${y + r}V${y + h - r}` +
    `A${r} ${r} 0 0 1 ${x + w - r} ${y + h}H${x + r}A${r} ${r} 0 0 1 ${x} ${y + h - r}V${y + r}A${r} ${r} 0 0 1 ${x + r} ${y}Z`
  );
}

export function ellipsePath(rx: number, ry: number): string {
  return `M${rx} 0A${rx} ${ry} 0 1 1 ${-rx} 0A${rx} ${ry} 0 1 1 ${rx} 0Z`;
}

export function polygonPath(points: Vec2[]): string {
  if (points.length === 0) return "M0 0Z";
  return `${points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join("")}Z`;
}

/** Outline of a shape component in its local frame, or null if it has none. */
export function shapeOutline(type: string, p: Params, polygonPoints: (p: Params) => Vec2[]): string | null {
  switch (type) {
    case "circle":
      return ellipsePath(n(p, "radius", 50), n(p, "radius", 50));
    case "ellipse":
      return ellipsePath(n(p, "rx", 80), n(p, "ry", 50));
    case "rectangle":
      return roundedRectPath(n(p, "width", 200), n(p, "height", 120), n(p, "cornerRadius", 0));
    case "polygon":
      return polygonPath(polygonPoints(p));
    case "path":
      return typeof p.d === "string" ? p.d : null;
    default:
      return null;
  }
}

function flatten(segs: Seg[]): Vec2[] {
  const pts: Vec2[] = [];
  let cur: Vec2 = { x: 0, y: 0 };
  let start: Vec2 = { x: 0, y: 0 };
  for (const s of segs) {
    if (s.c === "M") {
      cur = s.p;
      start = s.p;
      pts.push(cur);
    } else if (s.c === "L") {
      cur = s.p;
      pts.push(cur);
    } else if (s.c === "C") {
      for (let k = 1; k <= 24; k++) {
        const t = k / 24;
        const u = 1 - t;
        pts.push({
          x: u * u * u * cur.x + 3 * u * u * t * s.c1.x + 3 * u * t * t * s.c2.x + t * t * t * s.p.x,
          y: u * u * u * cur.y + 3 * u * u * t * s.c1.y + 3 * u * t * t * s.c2.y + t * t * t * s.p.y,
        });
      }
      cur = s.p;
    } else {
      cur = start;
      pts.push(start);
    }
  }
  return pts;
}

/** `count` points evenly spaced by arc length along the longest subpath (treated as closed). */
export function resampleOutline(d: string, count: number): Vec2[] {
  const subs = subpaths(parsePath(d)).map(flatten);
  let poly = subs[0] ?? [];
  let best = -1;
  for (const s of subs) {
    let len = 0;
    for (let i = 1; i < s.length; i++)
      len += Math.hypot((s[i] as Vec2).x - (s[i - 1] as Vec2).x, (s[i] as Vec2).y - (s[i - 1] as Vec2).y);
    if (len > best) {
      best = len;
      poly = s;
    }
  }
  if (poly.length < 2) return Array.from({ length: count }, () => poly[0] ?? { x: 0, y: 0 });
  const closed = [...poly, poly[0] as Vec2];
  const cum = [0];
  for (let i = 1; i < closed.length; i++) {
    cum.push(
      (cum[i - 1] as number) +
        Math.hypot(
          (closed[i] as Vec2).x - (closed[i - 1] as Vec2).x,
          (closed[i] as Vec2).y - (closed[i - 1] as Vec2).y,
        ),
    );
  }
  const total = cum[cum.length - 1] as number;
  const out: Vec2[] = [];
  let j = 1;
  for (let k = 0; k < count; k++) {
    const target = (total * k) / count;
    while (j < cum.length - 1 && (cum[j] as number) < target) j++;
    const a = closed[j - 1] as Vec2;
    const b = closed[j] as Vec2;
    const seg = (cum[j] as number) - (cum[j - 1] as number);
    const u = seg > 0 ? (target - (cum[j - 1] as number)) / seg : 0;
    out.push({ x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u });
  }
  return out;
}

/** Rotate/reverse `to` so its points line up with `from` (minimum total squared distance). */
export function alignOutlines(from: Vec2[], to: Vec2[]): Vec2[] {
  const nPts = from.length;
  let best = to;
  let bestCost = Number.POSITIVE_INFINITY;
  for (const cand of [to, [...to].reverse()]) {
    for (let shift = 0; shift < nPts; shift++) {
      let cost = 0;
      for (let i = 0; i < nPts && cost < bestCost; i++) {
        const a = from[i] as Vec2;
        const b = cand[(i + shift) % nPts] as Vec2;
        cost += (a.x - b.x) ** 2 + (a.y - b.y) ** 2;
      }
      if (cost < bestCost) {
        bestCost = cost;
        best = Array.from({ length: nPts }, (_, i) => cand[(i + shift) % nPts] as Vec2);
      }
    }
  }
  return best;
}
