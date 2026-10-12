/**
 * A saved drawing (ADR-055): vector art from the object library, written into a scene by the
 * pipeline when the model refers to a saved object by name. The model never writes it (hidden:
 * not in the LLM schema, the catalog or the docs); the scene carries the art inline, so a lesson
 * plays without the library.
 *
 * The art has its own coordinate box (`w` x `h`); `width` sizes it on the canvas and the height
 * follows, so a drawing always keeps its proportions. Its parts (`part` on shapes) and named
 * points (`anchors`) are anchors of the object, for labels and arrows that point at a part.
 */
import { z } from "zod";
import { parsePath, pathBounds } from "../geom/path";
import { BOX_ANCHORS, centered, type Rect, unionAll } from "../math/rect";
import type { Vec2 } from "../math/vec";
import { parseColorRef } from "../theme/theme";
import { defineComponentSpec } from "./registry";
import { type Params, pNum } from "./spec";

/** Snake-case names for parts and named points (they become anchors: `heart.left_ventricle`). */
const Name = z.string().regex(/^[a-z][a-z0-9_]{0,47}$/);
/** A color role reference (`red`, `red.dark`), a hex color or `none` (checked by `validate`). */
const Paint = z.string().min(1).max(32);

export const FigureShape = z.strictObject({
  d: z.string().min(1).max(20000),
  fill: Paint.optional(),
  stroke: Paint.optional(),
  strokeWidth: z.number().min(0).max(200).optional(),
  opacity: z.number().min(0).max(1).optional(),
  fillRule: z.enum(["nonzero", "evenodd"]).optional(),
  part: Name.optional(),
});

export const FigureArt = z.strictObject({
  w: z.number().min(1).max(4000),
  h: z.number().min(1).max(4000),
  shapes: z.array(FigureShape).min(1).max(400),
  anchors: z
    .array(z.strictObject({ name: Name, x: z.number(), y: z.number() }))
    .max(64)
    .optional(),
});

export type FigureShapeT = z.infer<typeof FigureShape>;
export type FigureArtT = z.infer<typeof FigureArt>;

/** All path data of one drawing together (a scene is at most 256 KB). */
export const FIGURE_MAX_PATH_CHARS = 120_000;
const RESERVED = new Set<string>(BOX_ANCHORS);

const isArt = (v: unknown): v is FigureArtT =>
  typeof v === "object" &&
  v !== null &&
  typeof (v as FigureArtT).w === "number" &&
  typeof (v as FigureArtT).h === "number" &&
  Array.isArray((v as FigureArtT).shapes);

const artOf = (p: Params): FigureArtT | null => (isArt(p.art) && p.art.w > 0 && p.art.h > 0 ? p.art : null);

/** The art's anchor points in art coordinates: its named points, then each part's center (the
 * pipeline stores a point inside each part; a part without one gets its bounds' center).
 * Computed once per art object (evaluation passes the same object every frame). */
const anchorCache = new WeakMap<FigureArtT, Map<string, Vec2>>();

export function figureAnchors(art: FigureArtT): Map<string, Vec2> {
  const hit = anchorCache.get(art);
  if (hit) return hit;
  const out = new Map<string, Vec2>();
  for (const a of art.anchors ?? []) {
    if (!RESERVED.has(a.name) && Number.isFinite(a.x) && Number.isFinite(a.y) && !out.has(a.name))
      out.set(a.name, { x: a.x, y: a.y });
  }
  const boxes = new Map<string, Rect[]>();
  for (const s of art.shapes) {
    if (!s.part || RESERVED.has(s.part) || out.has(s.part)) continue;
    try {
      const b = pathBounds(parsePath(s.d));
      boxes.set(s.part, [...(boxes.get(s.part) ?? []), b]);
    } catch {
      // Invalid path data is reported by `validate`.
    }
  }
  for (const [part, rects] of boxes) {
    const b = unionAll(rects);
    if (b) out.set(part, { x: b.x + b.w / 2, y: b.y + b.h / 2 });
  }
  anchorCache.set(art, out);
  return out;
}

/** Canvas size of a figure: `width` wide, its height from the art's proportions. */
export function figureSize(p: Params): { w: number; h: number } {
  const art = artOf(p);
  const w = pNum(p, "width", 300);
  return { w, h: art ? (w * art.h) / art.w : w };
}

const paintOk = (v: string | undefined): boolean => v === undefined || v === "none" || parseColorRef(v) !== null;

export const figure = defineComponentSpec({
  type: "figure",
  category: "primitive",
  description:
    "A saved drawing from the object library, placed by the pipeline. Its parts and named points are anchors.",
  params: z.strictObject({
    art: FigureArt,
    width: z.number().min(8).max(1600).default(300),
    /** The library object it came from (`heart_exterior@1`), for corrections and records. */
    ref: z.string().max(96).optional(),
    /** A part shown standing out (the rest dims); a `transform` changes it as the narration moves on. */
    focus: z.string().max(48).default(""),
  }),
  geometry: (p) => {
    const { w, h } = figureSize(p);
    const art = artOf(p);
    if (!art) return { bbox: centered(0, 0, w, h) };
    const k = w / art.w;
    const anchors: Record<string, Vec2> = {};
    for (const [name, a] of figureAnchors(art)) anchors[name] = { x: (a.x - art.w / 2) * k, y: (a.y - art.h / 2) * k };
    return { bbox: centered(0, 0, w, h), anchors };
  },
  dynamicAnchors: (p) => {
    const art = artOf(p);
    return art ? [...figureAnchors(art).keys()] : [];
  },
  validate: (p) => {
    const issues: { code: string; message: string; param: string; suggestion?: string }[] = [];
    const art = artOf(p);
    if (!art) return [{ code: "FIGURE_INVALID", param: "art", message: "The drawing has no art." }];
    let chars = 0;
    art.shapes.forEach((s, i) => {
      chars += s.d.length;
      try {
        parsePath(s.d);
      } catch (e) {
        issues.push({ code: "FIGURE_INVALID", param: "art", message: `Shape ${i}: ${(e as Error).message}.` });
      }
      if (!paintOk(s.fill) || !paintOk(s.stroke))
        issues.push({ code: "FIGURE_INVALID", param: "art", message: `Shape ${i}: unknown color.` });
      if (s.part && RESERVED.has(s.part))
        issues.push({
          code: "FIGURE_INVALID",
          param: "art",
          message: `Shape ${i}: part name '${s.part}' is reserved.`,
        });
    });
    const focus = typeof p.focus === "string" ? p.focus : "";
    if (focus && !art.shapes.some((s) => s.part === focus))
      issues.push({ code: "FIGURE_INVALID", param: "focus", message: `The drawing has no part '${focus}'.` });
    if (chars > FIGURE_MAX_PATH_CHARS)
      issues.push({ code: "FIGURE_INVALID", param: "art", message: `The drawing is too large (${chars} characters).` });
    const seen = new Set<string>();
    for (const a of art.anchors ?? []) {
      if (RESERVED.has(a.name) || seen.has(a.name))
        issues.push({ code: "FIGURE_INVALID", param: "art", message: `Anchor '${a.name}' is reserved or repeated.` });
      seen.add(a.name);
      const out = a.x < -0.05 * art.w || a.x > 1.05 * art.w || a.y < -0.05 * art.h || a.y > 1.05 * art.h;
      if (out)
        issues.push({ code: "FIGURE_INVALID", param: "art", message: `Anchor '${a.name}' is outside the drawing.` });
    }
    return issues;
  },
  entrance: "pop",
  semanticRoles: ["shape", "picture"],
  hidden: true,
  examples: [
    {
      type: "figure",
      params: {
        width: 120,
        art: { w: 100, h: 100, shapes: [{ d: "M50 5 L95 95 L5 95 Z", fill: "green", part: "body" }] },
      },
      place: { slot: "stage.center" },
    },
  ],
});
