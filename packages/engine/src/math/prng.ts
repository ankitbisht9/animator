/**
 * Seeded, stateless randomness. `Math.random()` is never used by the engine (P1):
 * every random value is a pure function of (seed, keys...).
 */

/** 32-bit integer finalizer (murmur3 fmix32). */
function fmix32(h: number): number {
  let x = h >>> 0;
  x ^= x >>> 16;
  x = Math.imul(x, 0x85ebca6b);
  x ^= x >>> 13;
  x = Math.imul(x, 0xc2b2ae35);
  x ^= x >>> 16;
  return x >>> 0;
}

/** Hash any number of integers into one 32-bit unsigned integer. */
export function hash32(...keys: number[]): number {
  let h = 0x9e3779b9;
  for (const k of keys) {
    h = fmix32(h ^ Math.imul((k | 0) + 0x632be59b, 0x9e3779b1));
  }
  return h;
}

/** FNV-1a hash of a string, for deriving seeds from ids. */
export function hashString(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Uniform in [0, 1), a pure function of its arguments. */
export const rand01 = (seed: number, ...keys: number[]): number => hash32(seed, ...keys) / 4294967296;

/** Uniform in [lo, hi). */
export const randRange = (lo: number, hi: number, seed: number, ...keys: number[]): number =>
  lo + (hi - lo) * rand01(seed, ...keys);

/** Sequential generator for solvers that step in a fixed order (still deterministic). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
