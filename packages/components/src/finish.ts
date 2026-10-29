/**
 * How a scene's drawings are finished (ADR-064): the same scene, the same layout and colours, a
 * different surface. `classic` draws exactly as before. `rich` gives the things on the stage depth: a
 * fuller gradient with a lit top (radial on round shapes), a crisp darker outline where a shape had
 * none, and a soft lift shadow under it (the same light on diagram nodes). Light belongs to objects:
 * the shading fades with size, so a big panel or a road built from several slabs stays calm and shows
 * no seams. Only the renderer changes: bounding boxes, validation and timing are the engine's, so a
 * finish never moves anything.
 */
import { type FrameObject, lightness, type Mat } from "@animator/engine";
import { darken, fmt, lighten, withAlpha } from "./util";

export type FinishId = "classic" | "rich";

export const FINISHES: { id: FinishId; name: string; description: string }[] = [
  { id: "rich", name: "Rich", description: "Shaded shapes with depth, crisp outlines and soft shadows." },
  { id: "classic", name: "Classic", description: "Flat shapes with a light gradient, as lessons first looked." },
];

export function isFinish(v: unknown): v is FinishId {
  return v === "classic" || v === "rich";
}

/** Gradient stops of a filled shape: [offset, colour]. */
export type Stops = [number, string][];

/** Full light up to this size (the larger side, stage pixels), the classic light from FLAT_FROM. */
const FULL_UPTO = 140;
const FLAT_FROM = 300;

/** How much of the rich light a shape of this size gets: 1 for an object, 0 for a panel. */
export function lightFor(w: number, h: number): number {
  const side = Math.max(w, h);
  if (side <= FULL_UPTO) return 1;
  if (side >= FLAT_FROM) return 0;
  return (FLAT_FROM - side) / (FLAT_FROM - FULL_UPTO);
}

/** A rich fill: lit at the top (radial: top-left), a shade deeper at the bottom, `k` of the way from
 * the classic light (0) to the full one (1). Near-white fills (paper, cards) get only a breath of shade,
 * near-black ones a little more light. */
export function richStops(fill: string, radial: boolean, k = 1): Stops {
  const l = lightness(fill);
  const mix = (classic: number, rich: number) => classic + (rich - classic) * Math.max(0, Math.min(1, k));
  const up = l > 0.9 ? 0.03 : l < 0.3 ? 0.16 : 0.13;
  const down = l > 0.9 ? 0.045 : l < 0.3 ? 0.05 : 0.1;
  return radial
    ? [
        [0, lighten(fill, mix(0.1, up + 0.04))],
        [0.55, fill],
        [1, darken(fill, mix(0.05, down + 0.02))],
      ]
    : [
        [0, lighten(fill, mix(0.05, up))],
        [0.42, fill],
        [1, darken(fill, mix(0.04, down))],
      ];
}

/** The crisp outline a rich shape gets when it has none. */
export const richEdge = (fill: string): string => darken(fill, lightness(fill) > 0.9 ? 0.14 : 0.24);

/** The soft lift shadow under a rich shape. */
export const liftShadow = (shadow: string, dark: boolean): string => withAlpha(shadow, dark ? 0.32 : 0.13);

/** An object on the stage, fully drawn and opaque, not a sliver, a line or a panel (a backdrop, a
 * road, a wall): the shapes a rich finish outlines and lifts off the page. */
export function liftable(o: FrameObject): boolean {
  const { w, h } = o.bbox;
  return (
    o.layer !== "background" &&
    o.alpha >= 0.95 &&
    o.draw >= 1 &&
    o.fillReveal >= 1 &&
    Math.min(w, h) >= 24 &&
    Math.max(w, h) <= FLAT_FROM + 60
  );
}

/** `dist` pixels straight down the stage, in the object's own frame (its matrix may rotate or scale it),
 * as an SVG translate: shadows fall down whatever the object's turn. */
export function downIn(m: Mat, dist: number): string {
  const [a, b, c, d] = m;
  const det = a * d - b * c;
  if (!Number.isFinite(det) || Math.abs(det) < 1e-9) return `translate(0 ${fmt(dist)})`;
  return `translate(${fmt((-c * dist) / det)} ${fmt((a * dist) / det)})`;
}
