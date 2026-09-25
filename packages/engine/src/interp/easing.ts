/**
 * Deterministic easing functions: u in [0, 1] -> eased progress. Most return
 * exactly 0 at u=0 and exactly 1 at u=1; `back`, `elastic` and `spring` may
 * overshoot in between but still land exactly on 1.
 */
import { clamp01 } from "../math/num";

export const EASING_NAMES = [
  "linear",
  "ease_in",
  "ease_out",
  "ease_in_out",
  "ease_in_quad",
  "ease_out_quad",
  "ease_in_out_quad",
  "ease_in_expo",
  "ease_out_expo",
  "ease_in_out_sine",
  "back_in",
  "back_out",
  "back_in_out",
  "bounce_out",
  "elastic_out",
  "spring",
  "smooth",
  "step",
] as const;
export type EasingName = (typeof EASING_NAMES)[number];

/** Named easing or a custom cubic-bezier [x1, y1, x2, y2] (CSS semantics). */
export type Easing = EasingName | { cubic_bezier: number[] };

const BACK = 1.70158;

function bounceOut(u: number): number {
  const n = 7.5625;
  const d = 2.75;
  if (u < 1 / d) return n * u * u;
  if (u < 2 / d) {
    const v = u - 1.5 / d;
    return n * v * v + 0.75;
  }
  if (u < 2.5 / d) {
    const v = u - 2.25 / d;
    return n * v * v + 0.9375;
  }
  const v = u - 2.625 / d;
  return n * v * v + 0.984375;
}

/**
 * Analytic under-damped spring settling at 1, normalized so s(1) = 1 exactly.
 * zeta = 0.45, omega chosen so the motion visibly settles within the interval.
 */
function spring(u: number): number {
  const zeta = 0.45;
  const omega = 11;
  const wd = omega * Math.sqrt(1 - zeta * zeta);
  const f = (x: number) =>
    1 - Math.exp(-zeta * omega * x) * (Math.cos(wd * x) + ((zeta * omega) / wd) * Math.sin(wd * x));
  return f(u) / f(1);
}

const NAMED: Record<EasingName, (u: number) => number> = {
  linear: (u) => u,
  ease_in: (u) => u * u * u,
  ease_out: (u) => 1 - (1 - u) ** 3,
  ease_in_out: (u) => (u < 0.5 ? 4 * u * u * u : 1 - (-2 * u + 2) ** 3 / 2),
  ease_in_quad: (u) => u * u,
  ease_out_quad: (u) => 1 - (1 - u) * (1 - u),
  ease_in_out_quad: (u) => (u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2),
  ease_in_expo: (u) => (u === 0 ? 0 : 2 ** (10 * u - 10)),
  ease_out_expo: (u) => (u === 1 ? 1 : 1 - 2 ** (-10 * u)),
  ease_in_out_sine: (u) => -(Math.cos(Math.PI * u) - 1) / 2,
  back_in: (u) => (BACK + 1) * u * u * u - BACK * u * u,
  back_out: (u) => 1 + (BACK + 1) * (u - 1) ** 3 + BACK * (u - 1) ** 2,
  back_in_out: (u) => {
    const c2 = BACK * 1.525;
    return u < 0.5
      ? ((2 * u) ** 2 * ((c2 + 1) * 2 * u - c2)) / 2
      : ((2 * u - 2) ** 2 * ((c2 + 1) * (u * 2 - 2) + c2) + 2) / 2;
  },
  bounce_out: bounceOut,
  elastic_out: (u) => {
    if (u === 0 || u === 1) return u;
    return 2 ** (-10 * u) * Math.sin((u * 10 - 0.75) * ((2 * Math.PI) / 3)) + 1;
  },
  spring,
  smooth: (u) => u * u * (3 - 2 * u),
  step: (u) => (u < 1 ? 0 : 1),
};

/** CSS-style cubic bezier with P0=(0,0), P3=(1,1), solved for x by Newton + bisection. */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (u: number) => number {
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  const sampleX = (s: number) => ((ax * s + bx) * s + cx) * s;
  const sampleY = (s: number) => ((ay * s + by) * s + cy) * s;
  const sampleDX = (s: number) => (3 * ax * s + 2 * bx) * s + cx;
  return (u: number) => {
    if (u <= 0) return 0;
    if (u >= 1) return 1;
    let s = u;
    for (let i = 0; i < 8; i++) {
      const err = sampleX(s) - u;
      if (Math.abs(err) < 1e-7) return sampleY(s);
      const d = sampleDX(s);
      if (Math.abs(d) < 1e-6) break;
      s -= err / d;
    }
    let lo = 0;
    let hi = 1;
    s = u;
    for (let i = 0; i < 40; i++) {
      const x = sampleX(s);
      if (Math.abs(x - u) < 1e-7) break;
      if (x < u) lo = s;
      else hi = s;
      s = (lo + hi) / 2;
    }
    return sampleY(s);
  };
}

const bezierCache = new Map<string, (u: number) => number>();

export function easingFn(e: Easing | undefined): (u: number) => number {
  if (e === undefined) return NAMED.ease_in_out;
  if (typeof e === "string") return NAMED[e] ?? NAMED.ease_in_out;
  const key = e.cubic_bezier.join(",");
  let fn = bezierCache.get(key);
  if (!fn) {
    const [x1 = 0.42, y1 = 0, x2 = 0.58, y2 = 1] = e.cubic_bezier;
    fn = cubicBezier(x1, y1, x2, y2);
    bezierCache.set(key, fn);
  }
  return fn;
}

/** Evaluate easing with clamped input and exact endpoints. */
export function ease(e: Easing | undefined, u: number): number {
  const x = clamp01(u);
  if (x === 0) return 0;
  if (x === 1) return 1;
  return easingFn(e)(x);
}
