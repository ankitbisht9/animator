/**
 * Strict SVG path-data parser. Every path the engine accepts (user `path`
 * components, glyph outlines, equation glyphs) goes through here and is
 * normalized to absolute M / L / C / Z commands, which makes transforming,
 * measuring, resampling and morphing uniform. Anything malformed is rejected.
 */
import type { Mat } from "../math/matrix";
import { roundTo } from "../math/num";
import type { Rect } from "../math/rect";
import type { Vec2 } from "../math/vec";

export type Seg = { c: "M"; p: Vec2 } | { c: "L"; p: Vec2 } | { c: "C"; c1: Vec2; c2: Vec2; p: Vec2 } | { c: "Z" };

export class PathError extends Error {}

export const PATH_MAX_LENGTH = 20000;
export const PATH_MAX_SEGMENTS = 4000;

const ARG_COUNT: Record<string, number> = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0 };
const NUM = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/;

function tokenize(d: string): (string | number)[] {
  const out: (string | number)[] = [];
  let i = 0;
  while (i < d.length) {
    const ch = d[i] as string;
    if (ch === " " || ch === "," || ch === "\n" || ch === "\t" || ch === "\r") {
      i++;
      continue;
    }
    if (/[MLHVCSQTAZmlhvcsqtaz]/.test(ch)) {
      out.push(ch);
      i++;
      continue;
    }
    const m = NUM.exec(d.slice(i, i + 64));
    if (!m) throw new PathError(`unexpected '${ch}' at ${i}`);
    out.push(Number(m[0]));
    i += m[0].length;
  }
  return out;
}

/** Convert an SVG elliptical arc to cubic segments (W3C SVG implementation notes F.6). */
function arcToCubics(
  p0: Vec2,
  rx: number,
  ry: number,
  phiDeg: number,
  largeArc: boolean,
  sweep: boolean,
  p: Vec2,
): Seg[] {
  if (rx === 0 || ry === 0) return [{ c: "L", p }];
  const phi = (phiDeg * Math.PI) / 180;
  const cos = Math.cos(phi);
  const sin = Math.sin(phi);
  const dx = (p0.x - p.x) / 2;
  const dy = (p0.y - p.y) / 2;
  const x1 = cos * dx + sin * dy;
  const y1 = -sin * dx + cos * dy;
  let arx = Math.abs(rx);
  let ary = Math.abs(ry);
  const lambda = (x1 * x1) / (arx * arx) + (y1 * y1) / (ary * ary);
  if (lambda > 1) {
    arx *= Math.sqrt(lambda);
    ary *= Math.sqrt(lambda);
  }
  const num = arx * arx * ary * ary - arx * arx * y1 * y1 - ary * ary * x1 * x1;
  const den = arx * arx * y1 * y1 + ary * ary * x1 * x1;
  let coef = Math.sqrt(Math.max(0, num / den));
  if (largeArc === sweep) coef = -coef;
  const cx1 = (coef * arx * y1) / ary;
  const cy1 = (-coef * ary * x1) / arx;
  const cx = cos * cx1 - sin * cy1 + (p0.x + p.x) / 2;
  const cy = sin * cx1 + cos * cy1 + (p0.y + p.y) / 2;
  const ang = (ux: number, uy: number, vx: number, vy: number) => {
    const a = Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
    return a;
  };
  const theta1 = ang(1, 0, (x1 - cx1) / arx, (y1 - cy1) / ary);
  let dtheta = ang((x1 - cx1) / arx, (y1 - cy1) / ary, (-x1 - cx1) / arx, (-y1 - cy1) / ary);
  if (!sweep && dtheta > 0) dtheta -= 2 * Math.PI;
  if (sweep && dtheta < 0) dtheta += 2 * Math.PI;
  const n = Math.max(1, Math.ceil(Math.abs(dtheta) / (Math.PI / 2)));
  const delta = dtheta / n;
  const t = (4 / 3) * Math.tan(delta / 4);
  const segs: Seg[] = [];
  let a = theta1;
  const pt = (angle: number): Vec2 => {
    const ex = arx * Math.cos(angle);
    const ey = ary * Math.sin(angle);
    return { x: cos * ex - sin * ey + cx, y: sin * ex + cos * ey + cy };
  };
  const dpt = (angle: number): Vec2 => {
    const ex = -arx * Math.sin(angle);
    const ey = ary * Math.cos(angle);
    return { x: cos * ex - sin * ey, y: sin * ex + cos * ey };
  };
  for (let i = 0; i < n; i++) {
    const a2 = a + delta;
    const s = pt(a);
    const e = i === n - 1 ? p : pt(a2);
    const d1 = dpt(a);
    const d2 = dpt(a2);
    segs.push({
      c: "C",
      c1: { x: s.x + t * d1.x, y: s.y + t * d1.y },
      c2: { x: e.x - t * d2.x, y: e.y - t * d2.y },
      p: e,
    });
    a = a2;
  }
  return segs;
}

/** Parse path data into absolute M/L/C/Z segments. Throws PathError on anything malformed. */
export function parsePath(d: string): Seg[] {
  if (d.length > PATH_MAX_LENGTH) throw new PathError(`path data longer than ${PATH_MAX_LENGTH} chars`);
  const toks = tokenize(d);
  const segs: Seg[] = [];
  let i = 0;
  let cur: Vec2 = { x: 0, y: 0 };
  let start: Vec2 = { x: 0, y: 0 };
  let lastCtrl: Vec2 | null = null; // for S
  let lastQ: Vec2 | null = null; // for T
  let cmd = "";
  const num = (): number => {
    const v = toks[i++];
    if (typeof v !== "number") throw new PathError("expected a number");
    if (!Number.isFinite(v)) throw new PathError("non-finite coordinate");
    return v;
  };
  while (i < toks.length) {
    const tk = toks[i];
    if (typeof tk === "string") {
      cmd = tk;
      i++;
    } else if (!cmd) {
      throw new PathError("path must start with a command");
    } else if (cmd === "Z" || cmd === "z") {
      throw new PathError("numbers after Z");
    }
    const upper = cmd.toUpperCase();
    const rel = cmd !== upper;
    if (segs.length === 0 && upper !== "M") throw new PathError("path must start with M");
    const argc = ARG_COUNT[upper] ?? 0;
    if (argc > 0 && typeof toks[i] !== "number") throw new PathError(`missing arguments for ${cmd}`);
    const ox = rel ? cur.x : 0;
    const oy = rel ? cur.y : 0;
    let q: Vec2 | null = null;
    let cc: Vec2 | null = null;
    switch (upper) {
      case "M": {
        const p = { x: num() + ox, y: num() + oy };
        segs.push({ c: "M", p });
        cur = p;
        start = p;
        // Subsequent pairs are implicit linetos.
        cmd = rel ? "l" : "L";
        break;
      }
      case "L": {
        const p = { x: num() + ox, y: num() + oy };
        segs.push({ c: "L", p });
        cur = p;
        break;
      }
      case "H": {
        const p = { x: num() + ox, y: cur.y };
        segs.push({ c: "L", p });
        cur = p;
        break;
      }
      case "V": {
        const p = { x: cur.x, y: num() + oy };
        segs.push({ c: "L", p });
        cur = p;
        break;
      }
      case "C": {
        const c1 = { x: num() + ox, y: num() + oy };
        const c2 = { x: num() + ox, y: num() + oy };
        const p = { x: num() + ox, y: num() + oy };
        segs.push({ c: "C", c1, c2, p });
        cc = c2;
        cur = p;
        break;
      }
      case "S": {
        const c1 = lastCtrl ? { x: 2 * cur.x - lastCtrl.x, y: 2 * cur.y - lastCtrl.y } : cur;
        const c2 = { x: num() + ox, y: num() + oy };
        const p = { x: num() + ox, y: num() + oy };
        segs.push({ c: "C", c1, c2, p });
        cc = c2;
        cur = p;
        break;
      }
      case "Q":
      case "T": {
        const qc: Vec2 =
          upper === "Q"
            ? { x: num() + ox, y: num() + oy }
            : lastQ
              ? { x: 2 * cur.x - lastQ.x, y: 2 * cur.y - lastQ.y }
              : cur;
        const p = { x: num() + ox, y: num() + oy };
        // Degree elevation: quadratic -> cubic.
        segs.push({
          c: "C",
          c1: { x: cur.x + (2 / 3) * (qc.x - cur.x), y: cur.y + (2 / 3) * (qc.y - cur.y) },
          c2: { x: p.x + (2 / 3) * (qc.x - p.x), y: p.y + (2 / 3) * (qc.y - p.y) },
          p,
        });
        q = qc;
        cur = p;
        break;
      }
      case "A": {
        const rx = num();
        const ry = num();
        const rot = num();
        const large = num();
        const sweep = num();
        if ((large !== 0 && large !== 1) || (sweep !== 0 && sweep !== 1))
          throw new PathError("arc flags must be 0 or 1");
        const p = { x: num() + ox, y: num() + oy };
        segs.push(...arcToCubics(cur, rx, ry, rot, large === 1, sweep === 1, p));
        cur = p;
        break;
      }
      case "Z":
        segs.push({ c: "Z" });
        cur = start;
        break;
      default:
        throw new PathError(`unsupported command ${cmd}`);
    }
    lastCtrl = cc;
    lastQ = q;
    if (segs.length > PATH_MAX_SEGMENTS) throw new PathError(`more than ${PATH_MAX_SEGMENTS} segments`);
  }
  if (segs.length === 0) throw new PathError("empty path");
  return segs;
}

const fmt = (v: number) => String(roundTo(v, 2));

export function serializePath(segs: Seg[]): string {
  let out = "";
  for (const s of segs) {
    switch (s.c) {
      case "M":
      case "L":
        out += `${s.c}${fmt(s.p.x)} ${fmt(s.p.y)}`;
        break;
      case "C":
        out += `C${fmt(s.c1.x)} ${fmt(s.c1.y)} ${fmt(s.c2.x)} ${fmt(s.c2.y)} ${fmt(s.p.x)} ${fmt(s.p.y)}`;
        break;
      case "Z":
        out += "Z";
        break;
    }
  }
  return out;
}

export function transformPath(segs: Seg[], m: Mat): Seg[] {
  const tp = (p: Vec2): Vec2 => ({ x: m[0] * p.x + m[2] * p.y + m[4], y: m[1] * p.x + m[3] * p.y + m[5] });
  return segs.map((s) => {
    switch (s.c) {
      case "M":
      case "L":
        return { c: s.c, p: tp(s.p) };
      case "C":
        return { c: "C", c1: tp(s.c1), c2: tp(s.c2), p: tp(s.p) };
      default:
        return s;
    }
  });
}

/** Parameter values in (0,1) where a cubic's derivative (one axis) is zero. */
function cubicExtrema(p0: number, p1: number, p2: number, p3: number): number[] {
  const a = -p0 + 3 * p1 - 3 * p2 + p3;
  const b = 2 * (p0 - 2 * p1 + p2);
  const c = p1 - p0;
  const out: number[] = [];
  if (Math.abs(a) < 1e-12) {
    if (Math.abs(b) > 1e-12) out.push(-c / b);
  } else {
    const disc = b * b - 4 * a * c;
    if (disc >= 0) {
      const sq = Math.sqrt(disc);
      out.push((-b + sq) / (2 * a), (-b - sq) / (2 * a));
    }
  }
  return out.filter((t) => t > 0 && t < 1);
}

const cubicAt = (p0: number, p1: number, p2: number, p3: number, t: number) => {
  const u = 1 - t;
  return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
};

/** Exact bounding box (cubic extrema, not control-point hull). */
export function pathBounds(segs: Seg[]): Rect {
  let x0 = Number.POSITIVE_INFINITY;
  let y0 = Number.POSITIVE_INFINITY;
  let x1 = Number.NEGATIVE_INFINITY;
  let y1 = Number.NEGATIVE_INFINITY;
  const addP = (x: number, y: number) => {
    if (x < x0) x0 = x;
    if (x > x1) x1 = x;
    if (y < y0) y0 = y;
    if (y > y1) y1 = y;
  };
  let cur: Vec2 = { x: 0, y: 0 };
  let start: Vec2 = { x: 0, y: 0 };
  for (const s of segs) {
    if (s.c === "M") {
      cur = s.p;
      start = s.p;
      addP(s.p.x, s.p.y);
    } else if (s.c === "L") {
      addP(s.p.x, s.p.y);
      cur = s.p;
    } else if (s.c === "C") {
      addP(s.p.x, s.p.y);
      for (const t of cubicExtrema(cur.x, s.c1.x, s.c2.x, s.p.x)) addP(cubicAt(cur.x, s.c1.x, s.c2.x, s.p.x, t), cur.y);
      for (const t of cubicExtrema(cur.y, s.c1.y, s.c2.y, s.p.y)) addP(cur.x, cubicAt(cur.y, s.c1.y, s.c2.y, s.p.y, t));
      cur = s.p;
    } else {
      cur = start;
    }
  }
  // The x/y extrema above were added with a placeholder on the other axis; that
  // axis is already covered by endpoints, so bounds remain exact per axis.
  if (!Number.isFinite(x0)) return { x: 0, y: 0, w: 0, h: 0 };
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/** Split into subpaths (each starting with M). */
export function subpaths(segs: Seg[]): Seg[][] {
  const out: Seg[][] = [];
  for (const s of segs) {
    if (s.c === "M" || out.length === 0) out.push([]);
    out[out.length - 1]?.push(s);
  }
  return out;
}

/** Approximate total length by flattening cubics. */
export function pathLength(segs: Seg[]): number {
  let total = 0;
  let cur: Vec2 = { x: 0, y: 0 };
  let start: Vec2 = { x: 0, y: 0 };
  for (const s of segs) {
    if (s.c === "M") {
      cur = s.p;
      start = s.p;
    } else if (s.c === "L") {
      total += Math.hypot(s.p.x - cur.x, s.p.y - cur.y);
      cur = s.p;
    } else if (s.c === "C") {
      let prev = cur;
      for (let k = 1; k <= 16; k++) {
        const t = k / 16;
        const p = {
          x: cubicAt(cur.x, s.c1.x, s.c2.x, s.p.x, t),
          y: cubicAt(cur.y, s.c1.y, s.c2.y, s.p.y, t),
        };
        total += Math.hypot(p.x - prev.x, p.y - prev.y);
        prev = p;
      }
      cur = s.p;
    } else {
      total += Math.hypot(start.x - cur.x, start.y - cur.y);
      cur = start;
    }
  }
  return total;
}
