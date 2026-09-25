/**
 * Color math: parsing, OKLab interpolation (perceptually even blends), lightness
 * adjustments and WCAG contrast. Colors are sRGB components in [0, 1].
 */
import { clamp01, roundTo } from "../math/num";

export interface RGBA {
  r: number;
  g: number;
  b: number;
  a: number;
}

interface Lab {
  L: number;
  a: number;
  b: number;
}

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;

export const isHexColor = (s: string): boolean => HEX.test(s);

export function parseHex(s: string): RGBA | null {
  const m = HEX.exec(s.trim());
  if (!m?.[1]) return null;
  let h = m[1];
  if (h.length <= 4) h = [...h].map((c) => c + c).join("");
  const n = (i: number) => Number.parseInt(h.slice(i, i + 2), 16) / 255;
  return { r: n(0), g: n(2), b: n(4), a: h.length === 8 ? n(6) : 1 };
}

/** Parse a hex color; throws on malformed input (callers validate first). */
export function parseColor(s: string): RGBA {
  const c = parseHex(s);
  if (!c) throw new Error(`invalid color: ${s}`);
  return c;
}

const to2 = (v: number) =>
  Math.round(clamp01(v) * 255)
    .toString(16)
    .padStart(2, "0");

export function toHex(c: RGBA): string {
  const base = `#${to2(c.r)}${to2(c.g)}${to2(c.b)}`;
  return c.a >= 0.999 ? base : `${base}${to2(c.a)}`;
}

const srgbToLinear = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const linearToSrgb = (v: number) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);

function toOklab(c: RGBA): Lab {
  const r = srgbToLinear(c.r);
  const g = srgbToLinear(c.g);
  const b = srgbToLinear(c.b);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return {
    L: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  };
}

function fromOklab(lab: Lab, alpha: number): RGBA {
  const l = (lab.L + 0.3963377774 * lab.a + 0.2158037573 * lab.b) ** 3;
  const m = (lab.L - 0.1055613458 * lab.a - 0.0638541728 * lab.b) ** 3;
  const s = (lab.L - 0.0894841775 * lab.a - 1.291485548 * lab.b) ** 3;
  return {
    r: clamp01(linearToSrgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s)),
    g: clamp01(linearToSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s)),
    b: clamp01(linearToSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)),
    a: alpha,
  };
}

/** Interpolate two colors in OKLab (alpha linearly). u in [0, 1]. */
export function mixOklab(from: RGBA, to: RGBA, u: number): RGBA {
  if (u <= 0) return from;
  if (u >= 1) return to;
  const a = toOklab(from);
  const b = toOklab(to);
  const lab = { L: a.L + (b.L - a.L) * u, a: a.a + (b.a - a.a) * u, b: a.b + (b.b - a.b) * u };
  return fromOklab(lab, from.a + (to.a - from.a) * u);
}

export const mixHex = (from: string, to: string, u: number): string =>
  toHex(mixOklab(parseColor(from), parseColor(to), u));

/** Shift perceptual lightness by `delta` (OKLab L, roughly -1..1). */
export function adjustLightness(hex: string, delta: number): string {
  const c = parseColor(hex);
  const lab = toOklab(c);
  return toHex(fromOklab({ ...lab, L: clamp01(lab.L + delta) }, c.a));
}

export const withAlpha = (hex: string, alpha: number): string => toHex({ ...parseColor(hex), a: clamp01(alpha) });

/** Perceptual lightness (OKLab L) in [0, 1]. */
export const lightness = (hex: string): number => toOklab(parseColor(hex)).L;

/** WCAG 2.x relative luminance. */
export function luminance(c: RGBA): number {
  return 0.2126 * srgbToLinear(c.r) + 0.7152 * srgbToLinear(c.g) + 0.0722 * srgbToLinear(c.b);
}

/** Composite `fg` (with alpha) over an opaque `bg`. */
export function over(fg: RGBA, bg: RGBA): RGBA {
  const a = fg.a;
  return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a), a: 1 };
}

/** WCAG contrast ratio between two colors (fg composited over bg), 1..21. */
export function contrastRatio(fgHex: string, bgHex: string): number {
  const bg = parseColor(bgHex);
  const fg = over(parseColor(fgHex), { ...bg, a: 1 });
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  const hi = Math.max(l1, l2);
  const lo = Math.min(l1, l2);
  return roundTo((hi + 0.05) / (lo + 0.05), 3);
}
