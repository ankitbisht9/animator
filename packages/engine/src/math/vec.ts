import { deg2rad, lerp as lerpN } from "./num";

export interface Vec2 {
  x: number;
  y: number;
}

export const vec = (x: number, y: number): Vec2 => ({ x, y });
export const add = (a: Vec2, b: Vec2): Vec2 => ({ x: a.x + b.x, y: a.y + b.y });
export const sub = (a: Vec2, b: Vec2): Vec2 => ({ x: a.x - b.x, y: a.y - b.y });
export const mul = (a: Vec2, s: number): Vec2 => ({ x: a.x * s, y: a.y * s });
export const len = (a: Vec2): number => Math.hypot(a.x, a.y);
export const dist = (a: Vec2, b: Vec2): number => Math.hypot(a.x - b.x, a.y - b.y);
export const lerpVec = (a: Vec2, b: Vec2, u: number): Vec2 => ({ x: lerpN(a.x, b.x, u), y: lerpN(a.y, b.y, u) });

export function normalize(a: Vec2): Vec2 {
  const l = len(a);
  return l === 0 ? { x: 0, y: 0 } : { x: a.x / l, y: a.y / l };
}

/** Rotate by degrees (screen coordinates: positive = clockwise because y grows downward). */
export function rotate(a: Vec2, deg: number): Vec2 {
  const r = deg2rad(deg);
  const c = Math.cos(r);
  const s = Math.sin(r);
  return { x: a.x * c - a.y * s, y: a.x * s + a.y * c };
}

/** Unit vector for a compass-like direction name or an angle in degrees (0 = right, 90 = down). */
export function directionVector(dir: string | number): Vec2 {
  if (typeof dir === "number") return rotate({ x: 1, y: 0 }, dir);
  switch (dir) {
    case "up":
      return { x: 0, y: -1 };
    case "down":
      return { x: 0, y: 1 };
    case "left":
      return { x: -1, y: 0 };
    default:
      return { x: 1, y: 0 };
  }
}
