import type { Vec2 } from "./vec";

/** Axis-aligned rectangle. `x`,`y` is the top-left corner (y grows downward). */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const rect = (x: number, y: number, w: number, h: number): Rect => ({ x, y, w, h });
export const centered = (cx: number, cy: number, w: number, h: number): Rect => ({
  x: cx - w / 2,
  y: cy - h / 2,
  w,
  h,
});
export const center = (r: Rect): Vec2 => ({ x: r.x + r.w / 2, y: r.y + r.h / 2 });
export const area = (r: Rect): number => Math.max(0, r.w) * Math.max(0, r.h);
export const translate = (r: Rect, dx: number, dy: number): Rect => ({ ...r, x: r.x + dx, y: r.y + dy });
export const expand = (r: Rect, pad: number): Rect => ({
  x: r.x - pad,
  y: r.y - pad,
  w: r.w + 2 * pad,
  h: r.h + 2 * pad,
});

export function union(a: Rect, b: Rect): Rect {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y };
}

export function unionAll(rects: Rect[]): Rect | null {
  let acc: Rect | null = null;
  for (const r of rects) acc = acc ? union(acc, r) : r;
  return acc;
}

export function intersection(a: Rect, b: Rect): Rect | null {
  const x = Math.max(a.x, b.x);
  const y = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.w, b.x + b.w);
  const y2 = Math.min(a.y + a.h, b.y + b.h);
  return x2 > x && y2 > y ? { x, y, w: x2 - x, h: y2 - y } : null;
}

export const overlapArea = (a: Rect, b: Rect): number => {
  const i = intersection(a, b);
  return i ? area(i) : 0;
};

/** True when `inner` lies inside `outer`, allowing `tol` pixels of overhang. */
export const contains = (outer: Rect, inner: Rect, tol = 0): boolean =>
  inner.x >= outer.x - tol &&
  inner.y >= outer.y - tol &&
  inner.x + inner.w <= outer.x + outer.w + tol &&
  inner.y + inner.h <= outer.y + outer.h + tol;

export function fromPoints(points: Vec2[]): Rect {
  let x0 = Number.POSITIVE_INFINITY;
  let y0 = Number.POSITIVE_INFINITY;
  let x1 = Number.NEGATIVE_INFINITY;
  let y1 = Number.NEGATIVE_INFINITY;
  for (const p of points) {
    if (p.x < x0) x0 = p.x;
    if (p.y < y0) y0 = p.y;
    if (p.x > x1) x1 = p.x;
    if (p.y > y1) y1 = p.y;
  }
  if (!Number.isFinite(x0)) return { x: 0, y: 0, w: 0, h: 0 };
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

export const BOX_ANCHORS = [
  "center",
  "top",
  "bottom",
  "left",
  "right",
  "top_left",
  "top_right",
  "bottom_left",
  "bottom_right",
] as const;
export type BoxAnchor = (typeof BOX_ANCHORS)[number];

/** The nine standard anchors every component exposes, derived from its bounding box. */
export function boxAnchors(r: Rect): Record<BoxAnchor, Vec2> {
  const cx = r.x + r.w / 2;
  const cy = r.y + r.h / 2;
  const x2 = r.x + r.w;
  const y2 = r.y + r.h;
  return {
    center: { x: cx, y: cy },
    top: { x: cx, y: r.y },
    bottom: { x: cx, y: y2 },
    left: { x: r.x, y: cy },
    right: { x: x2, y: cy },
    top_left: { x: r.x, y: r.y },
    top_right: { x: x2, y: r.y },
    bottom_left: { x: r.x, y: y2 },
    bottom_right: { x: x2, y: y2 },
  };
}
