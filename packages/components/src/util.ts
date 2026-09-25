import { adjustLightness, type FrameObject, type Mat, mixHex, roundTo, withAlpha } from "@animator/engine";
import type { SVGProps } from "react";

export const fmt = (v: number, d = 2): number => roundTo(v, d);

export const matrixAttr = (m: Mat): string => `matrix(${m.map((v) => fmt(v, 4)).join(" ")})`;

/** Stable, collision-free element id derived from an object id (never random). */
export const uid = (objectId: string, part: string): string => `a-${objectId}-${part}`;

export const lighten = (hex: string, amt: number): string => adjustLightness(hex, amt);
export const darken = (hex: string, amt: number): string => adjustLightness(hex, -amt);
export { mixHex, withAlpha };

/** Stroke write-on attributes (pathLength-normalized dash) for draw progress < 1. */
export function drawAttrs(draw: number): SVGProps<SVGPathElement> {
  if (draw >= 1) return {};
  return { pathLength: 1, strokeDasharray: "1 1", strokeDashoffset: fmt(1 - Math.max(0, draw), 4) };
}

/**
 * Stroke/fill treatment shared by shapes. During a draw-on entrance a shape
 * without a stroke gets a temporary one that fades as the fill arrives.
 */
export function shapePaint(o: FrameObject) {
  const sw = o.strokeWidth;
  const drawing = o.draw < 1 || o.fillReveal < 1;
  const strokeWidth = sw > 0 ? sw : drawing ? 3 : 0;
  const strokeOpacity = sw > 0 ? 1 : 1 - o.fillReveal;
  return {
    strokeWidth,
    strokeOpacity: fmt(strokeOpacity, 3),
    fillOpacity: fmt(o.fillReveal, 3),
    dash: drawAttrs(o.draw),
  };
}

export const n = (v: unknown, d: number): number => (typeof v === "number" && Number.isFinite(v) ? v : d);
export const s = (v: unknown, d: string): string => (typeof v === "string" ? v : d);
