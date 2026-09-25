import { deg2rad } from "./num";
import { fromPoints, type Rect } from "./rect";
import type { Vec2 } from "./vec";

/** 2D affine matrix in SVG order: x' = a·x + c·y + e, y' = b·x + d·y + f. */
export type Mat = readonly [number, number, number, number, number, number];

export const IDENTITY: Mat = [1, 0, 0, 1, 0, 0];

/** Returns m1 · m2 (apply m2 first, then m1). */
export function multiply(m1: Mat, m2: Mat): Mat {
  const [a1, b1, c1, d1, e1, f1] = m1;
  const [a2, b2, c2, d2, e2, f2] = m2;
  return [
    a1 * a2 + c1 * b2,
    b1 * a2 + d1 * b2,
    a1 * c2 + c1 * d2,
    b1 * c2 + d1 * d2,
    a1 * e2 + c1 * f2 + e1,
    b1 * e2 + d1 * f2 + f1,
  ];
}

export const translation = (x: number, y: number): Mat => [1, 0, 0, 1, x, y];
export const scaling = (sx: number, sy = sx): Mat => [sx, 0, 0, sy, 0, 0];

export function rotation(deg: number): Mat {
  const r = deg2rad(deg);
  const c = Math.cos(r);
  const s = Math.sin(r);
  return [c, s, -s, c, 0, 0];
}

/** Object transform: translate to (x, y), then rotate, then scale (about the local origin). */
export function objectMatrix(x: number, y: number, rotationDeg: number, scale: number): Mat {
  let m = translation(x, y);
  if (rotationDeg !== 0) m = multiply(m, rotation(rotationDeg));
  if (scale !== 1) m = multiply(m, scaling(scale));
  return m;
}

export function apply(m: Mat, p: Vec2): Vec2 {
  return { x: m[0] * p.x + m[2] * p.y + m[4], y: m[1] * p.x + m[3] * p.y + m[5] };
}

/** Axis-aligned bounds of a transformed rectangle. */
export function transformRect(m: Mat, r: Rect): Rect {
  return fromPoints([
    apply(m, { x: r.x, y: r.y }),
    apply(m, { x: r.x + r.w, y: r.y }),
    apply(m, { x: r.x, y: r.y + r.h }),
    apply(m, { x: r.x + r.w, y: r.y + r.h }),
  ]);
}

export function invert(m: Mat): Mat {
  const [a, b, c, d, e, f] = m;
  const det = a * d - b * c;
  if (det === 0) return IDENTITY;
  return [d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det];
}
