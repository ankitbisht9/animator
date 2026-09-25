/** Tier 0 primitives. */
import { z } from "zod";
import { Point, TextOrBind, Vec } from "../dsl/primitives";
import { parsePath, pathBounds } from "../geom/path";
import { centered, fromPoints, type Rect } from "../math/rect";
import type { Vec2 } from "../math/vec";
import { resolveTextSize, TextSize, textLayoutOf, Weight } from "./common";
import { defineComponentSpec } from "./registry";
import { type Params, pNum, pStr, pVec } from "./spec";

export const circle = defineComponentSpec({
  type: "circle",
  category: "primitive",
  description: "A filled circle. Origin is its center.",
  params: z.strictObject({ radius: z.number().min(1).max(800).default(50) }),
  geometry: (p) => {
    const r = pNum(p, "radius", 50);
    return { bbox: centered(0, 0, 2 * r, 2 * r) };
  },
  entrance: "pop",
  draw: true,
  defaultStyle: { fill: "object_a", stroke: "object_a.dark", strokeWidth: 0 },
  semanticRoles: ["shape"],
  examples: [{ type: "circle", params: { radius: 40 }, place: { slot: "stage.center" }, style: { fill: "object_b" } }],
});

export const ellipse = defineComponentSpec({
  type: "ellipse",
  category: "primitive",
  description: "A filled ellipse. Origin is its center.",
  params: z.strictObject({
    rx: z.number().min(1).max(800).default(80),
    ry: z.number().min(1).max(800).default(50),
  }),
  geometry: (p) => ({ bbox: centered(0, 0, 2 * pNum(p, "rx", 80), 2 * pNum(p, "ry", 50)) }),
  entrance: "pop",
  draw: true,
  defaultStyle: { fill: "object_c", stroke: "object_c.dark", strokeWidth: 0 },
  semanticRoles: ["shape"],
  examples: [{ type: "ellipse", params: { rx: 90, ry: 40 }, place: { slot: "stage.center" } }],
});

export const rectangle = defineComponentSpec({
  type: "rectangle",
  category: "primitive",
  description: "A rectangle, optionally with rounded corners. Origin is its center.",
  params: z.strictObject({
    width: z.number().min(1).max(1600).default(200),
    height: z.number().min(1).max(900).default(120),
    cornerRadius: z.number().min(0).max(400).default(0),
  }),
  geometry: (p) => ({ bbox: centered(0, 0, pNum(p, "width", 200), pNum(p, "height", 120)) }),
  entrance: "pop",
  draw: true,
  defaultStyle: { fill: "surface", stroke: "muted", strokeWidth: 3 },
  semanticRoles: ["shape", "panel"],
  examples: [
    { type: "rectangle", params: { width: 300, height: 160, cornerRadius: 16 }, place: { slot: "stage.right" } },
  ],
});

function polygonPoints(p: Params): Vec2[] {
  const pts = p.points as Vec2[] | undefined;
  if (Array.isArray(pts) && pts.length >= 3) return pts;
  const sides = Math.round(pNum(p, "sides", 6));
  const r = pNum(p, "radius", 60);
  return Array.from({ length: sides }, (_, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / sides;
    return { x: r * Math.cos(a), y: r * Math.sin(a) };
  });
}

export const polygon = defineComponentSpec({
  type: "polygon",
  category: "primitive",
  description:
    "A closed polygon: either explicit `points` (relative to the origin) or a regular polygon from `sides` and `radius`.",
  params: z.strictObject({
    points: z.array(Vec).min(3).max(64).optional(),
    sides: z.number().int().min(3).max(12).default(6),
    radius: z.number().min(1).max(800).default(60),
  }),
  geometry: (p) => ({ bbox: fromPoints(polygonPoints(p)) }),
  entrance: "pop",
  draw: true,
  defaultStyle: { fill: "object_c", stroke: "object_c.dark", strokeWidth: 0 },
  semanticRoles: ["shape"],
  examples: [{ type: "polygon", params: { sides: 3, radius: 70 }, place: { slot: "stage.left" } }],
});

export { polygonPoints };

export const line = defineComponentSpec({
  type: "line",
  category: "primitive",
  description: "A straight line between two points. Endpoints may be anchor references that follow their objects.",
  params: z.strictObject({
    from: Point,
    to: Point,
    dashed: z.boolean().default(false),
  }),
  geometry: (p) => {
    const a = pVec(p, "from");
    const b = pVec(p, "to");
    return { bbox: fromPoints([a, b]), anchors: { start: a, end: b, mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } } };
  },
  anchors: ["start", "end", "mid"],
  entrance: "draw",
  draw: true,
  worldSpace: true,
  pointParams: ["from", "to"],
  defaultStyle: { stroke: "muted", strokeWidth: 4 },
  semanticRoles: ["connector"],
  examples: [{ type: "line", params: { from: { x: 300, y: 600 }, to: { x: 1300, y: 600 } } }],
});

const pathCache = new Map<string, Rect>();
export function pathBBox(d: string): Rect {
  let r = pathCache.get(d);
  if (!r) {
    try {
      r = pathBounds(parsePath(d));
    } catch {
      r = { x: 0, y: 0, w: 0, h: 0 };
    }
    if (pathCache.size > 500) pathCache.clear();
    pathCache.set(d, r);
  }
  return r;
}

export const path = defineComponentSpec({
  type: "path",
  category: "primitive",
  description:
    "A vector path from SVG path data (M L H V C S Q T A Z only), in coordinates relative to the object origin. Validated by a strict parser.",
  params: z.strictObject({
    d: z.string().min(1).max(20000),
    closed: z.boolean().default(false),
  }),
  geometry: (p) => ({ bbox: pathBBox(pStr(p, "d")) }),
  validate: (p) => {
    try {
      parsePath(pStr(p, "d"));
      return [];
    } catch (e) {
      return [
        {
          code: "PATH_INVALID",
          param: "d",
          message: `Invalid path data: ${(e as Error).message}.`,
          suggestion: "Use absolute or relative M L H V C S Q T A Z commands with numeric arguments.",
        },
      ];
    }
  },
  entrance: "draw",
  draw: true,
  defaultStyle: { stroke: "text", strokeWidth: 4 },
  semanticRoles: ["shape", "curve"],
  examples: [{ type: "path", params: { d: "M0 0 C 60 -80 140 -80 200 0" }, place: { slot: "stage.center" } }],
});

export const text = defineComponentSpec({
  type: "text",
  category: "primitive",
  description:
    "Text rendered in the theme font. Use **double asterisks** for emphasis. `text` may be a binding to show computed values. Origin is the center of the text box.",
  params: z.strictObject({
    text: TextOrBind,
    size: TextSize.default("body"),
    weight: Weight.default("regular"),
    align: z.enum(["left", "center", "right"]).default("center"),
    maxWidth: z.number().min(40).max(1600).optional(),
    lineHeight: z.number().min(0.9).max(2.5).optional(),
  }),
  geometry: (p, ctx) => {
    const l = textLayoutOf(p, ctx.theme);
    return { bbox: centered(0, 0, l.width, l.height) };
  },
  entrance: "write",
  write: true,
  labelLike: true,
  textParams: ["text"],
  defaultStyle: { text: "text" },
  semanticRoles: ["text", "label", "title"],
  examples: [
    {
      type: "text",
      params: { text: "Archimedes' **principle**", size: "title", weight: "bold" },
      place: { slot: "title_bar" },
    },
  ],
});

export { resolveTextSize };

export const group = defineComponentSpec({
  type: "group",
  category: "primitive",
  description:
    "An invisible container. Objects whose `parent` is this group move, scale and fade with it. Its bounds are the union of its children.",
  params: z.strictObject({}),
  geometry: () => ({ bbox: { x: 0, y: 0, w: 0, h: 0 } }),
  entrance: "fade",
  semanticRoles: ["container"],
  examples: [{ type: "group", place: { slot: "stage.center" } }],
});

export const image = defineComponentSpec({
  type: "image",
  category: "primitive",
  description:
    "Placeholder for a local image asset (by asset id). No external URLs. v1 has no asset library, so this renders a labelled placeholder.",
  params: z.strictObject({
    asset: z.string().regex(/^[a-z0-9_-]{1,64}$/),
    width: z.number().min(8).max(1600).default(240),
    height: z.number().min(8).max(900).default(160),
    alt: z.string().max(120).optional(),
  }),
  geometry: (p) => ({ bbox: centered(0, 0, pNum(p, "width", 240), pNum(p, "height", 160)) }),
  entrance: "fade",
  defaultStyle: { fill: "surface_alt", stroke: "muted" },
  semanticRoles: ["image"],
  examples: [{ type: "image", params: { asset: "diagram_1", alt: "diagram" } }],
});
