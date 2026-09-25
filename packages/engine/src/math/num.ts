export const clamp = (v: number, lo: number, hi: number): number => (v < lo ? lo : v > hi ? hi : v);
export const clamp01 = (v: number): number => clamp(v, 0, 1);
export const lerp = (a: number, b: number, u: number): number => a + (b - a) * u;
export const invLerp = (a: number, b: number, v: number): number => (a === b ? 0 : (v - a) / (b - a));

/** Round to `decimals` places. Used to trim float noise so outputs hash identically. */
export function roundTo(v: number, decimals: number): number {
  const f = 10 ** decimals;
  const r = Math.round(v * f) / f;
  return Object.is(r, -0) ? 0 : r;
}

export const approxEq = (a: number, b: number, eps = 1e-6): boolean => Math.abs(a - b) <= eps;

export const smoothstep = (u: number): number => {
  const x = clamp01(u);
  return x * x * (3 - 2 * x);
};

export const deg2rad = (d: number): number => (d * Math.PI) / 180;
export const rad2deg = (r: number): number => (r * 180) / Math.PI;

export const isFiniteNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
