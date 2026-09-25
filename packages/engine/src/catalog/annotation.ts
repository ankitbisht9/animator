/**
 * Tier 1 annotation components. Each complex component exposes a `layout*`
 * function used by BOTH its spec geometry (bounds/anchors for layout and
 * validation) and its renderer, so what is validated is exactly what is drawn.
 */
import { z } from "zod";
import { ColorRef, Id, NumOrBind, Point, TextOrBind } from "../dsl/primitives";
import { clamp } from "../math/num";
import { centered, fromPoints, type Rect, union } from "../math/rect";
import { add, directionVector, mul, normalize, sub, type Vec2 } from "../math/vec";
import { layoutText, type TextLayout } from "../text/layout";
import type { Theme } from "../theme/theme";
import { resolveTextSize, resolveWeight, TextSize, Weight } from "./common";
import { defineComponentSpec } from "./registry";
import { objectRefOf, type Params, pNum, pStr, pVec } from "./spec";

export const ICON_NAMES = [
  "lightbulb",
  "check",
  "cross",
  "question",
  "star",
  "info",
  "warning",
  "magnifier",
  "drop",
  "balance",
  "clock",
  "arrow_up",
  "arrow_down",
  "sparkle",
  "target",
  "book",
  "ship",
] as const;
export const IconName = z.enum(ICON_NAMES).meta({ id: "IconName" });

// ------------------------------------------------------------------ stroke sampling

/** Points about every `step` px along a polyline (connector checks in layout validation). */
function densify(points: Vec2[], step = 8): Vec2[] {
  const out: Vec2[] = [];
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i] as Vec2;
    const b = points[i + 1] as Vec2;
    const n = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / step));
    for (let k = 0; k < n; k++) out.push({ x: a.x + ((b.x - a.x) * k) / n, y: a.y + ((b.y - a.y) * k) / n });
  }
  const last = points[points.length - 1];
  if (last) out.push(last);
  return out;
}

function quadraticPoints(a: Vec2, c: Vec2, b: Vec2, n = 32): Vec2[] {
  const out: Vec2[] = [];
  for (let k = 0; k <= n; k++) {
    const s = k / n;
    const r = 1 - s;
    out.push({ x: r * r * a.x + 2 * r * s * c.x + s * s * b.x, y: r * r * a.y + 2 * r * s * c.y + s * s * b.y });
  }
  return densify(out);
}

// ------------------------------------------------------------------ label pills

export interface PillLayout {
  text: TextLayout;
  w: number;
  h: number;
  padX: number;
  padY: number;
}

export function pillLayout(text: string, size: number, weight: 400 | 600 | 700 = 600, maxWidth?: number): PillLayout {
  const padX = size * 0.55;
  const padY = size * 0.28;
  const t = layoutText(text, { size, weight, align: "center", lineHeight: 1.2, ...(maxWidth ? { maxWidth } : {}) });
  return { text: t, w: t.width + 2 * padX, h: t.height + 2 * padY, padX, padY };
}

// ------------------------------------------------------------------ arrow

export interface ArrowLayout {
  start: Vec2;
  end: Vec2;
  /** Quadratic control point (null = straight). */
  control: Vec2 | null;
  endDir: Vec2;
  startDir: Vec2;
  head: number;
}

export function arrowLayout(p: Params): ArrowLayout {
  const a = pVec(p, "from");
  const b = pVec(p, "to");
  const u = normalize(sub(b, a));
  const start = add(a, mul(u, pNum(p, "startGap", 0)));
  const end = sub(b, mul(u, pNum(p, "endGap", 0)));
  const curve = pNum(p, "curve", 0);
  const mid = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
  const control = curve ? add(mid, mul({ x: -u.y, y: u.x }, curve)) : null;
  return {
    start,
    end,
    control,
    endDir: normalize(sub(end, control ?? start)),
    startDir: normalize(sub(control ?? end, start)),
    head: pNum(p, "headSize", 22),
  };
}

export const arrow = defineComponentSpec({
  type: "arrow",
  category: "annotation",
  description:
    "A straight or curved arrow between two points (anchor references follow their objects). Use `curve` to bend it and gaps to keep clear of the objects it joins.",
  params: z.strictObject({
    from: Point,
    to: Point,
    curve: z.number().min(-600).max(600).default(0).describe("Sideways bend in px (0 = straight)"),
    head: z.enum(["end", "start", "both", "none"]).default("end"),
    headSize: z.number().min(6).max(80).default(22),
    startGap: z.number().min(0).max(200).default(0),
    endGap: z.number().min(0).max(200).default(0),
    dashed: z.boolean().default(false),
  }),
  geometry: (p) => {
    const l = arrowLayout(p);
    const pts = [l.start, l.end, ...(l.control ? [l.control] : [])];
    const bbox = fromPoints(pts);
    const h = l.head / 2;
    return {
      bbox: { x: bbox.x - h, y: bbox.y - h, w: bbox.w + 2 * h, h: bbox.h + 2 * h },
      anchors: {
        start: l.start,
        end: l.end,
        mid: l.control
          ? { x: (l.start.x + 2 * l.control.x + l.end.x) / 4, y: (l.start.y + 2 * l.control.y + l.end.y) / 4 }
          : { x: (l.start.x + l.end.x) / 2, y: (l.start.y + l.end.y) / 2 },
      },
    };
  },
  anchors: ["start", "end", "mid"],
  entrance: "draw",
  draw: true,
  worldSpace: true,
  strokeLine: (p) => {
    const l = arrowLayout(p);
    return l.control ? quadraticPoints(l.start, l.control, l.end) : densify([l.start, l.end]);
  },
  pointParams: ["from", "to"],
  defaultStyle: { stroke: "text", strokeWidth: 5 },
  semanticRoles: ["connector", "pointer", "flow"],
  examples: [{ type: "arrow", params: { from: "block.right", to: "label.left", curve: 40, endGap: 10 } }],
});

// ------------------------------------------------------------------ vector arrow

export interface VectorLayout {
  tail: Vec2;
  tip: Vec2;
  dir: Vec2;
  length: number;
  thickness: number;
  headLength: number;
  headWidth: number;
  label: (PillLayout & { center: Vec2 }) | null;
  bbox: Rect;
}

export function vectorLayout(p: Params, theme: Theme): VectorLayout {
  const from = pVec(p, "from");
  const mag = pNum(p, "magnitude", 1);
  let dir = directionVector((p.direction as string | number | undefined) ?? "down");
  if (mag < 0) dir = mul(dir, -1);
  const minL = pNum(p, "minLength", 36);
  const maxL = pNum(p, "maxLength", 360);
  const length = mag === 0 ? 0 : clamp(Math.abs(mag) * pNum(p, "scale", 12), minL, maxL);
  const origin = pStr(p, "origin", "tail");
  const tail =
    origin === "head" ? sub(from, mul(dir, length)) : origin === "center" ? sub(from, mul(dir, length / 2)) : from;
  const tip = add(tail, mul(dir, length));
  const thickness = pNum(p, "thickness", 10);
  const headLength = Math.min(length * 0.45, thickness * 2.6);
  const headWidth = thickness * 2.8;
  const n = { x: -dir.y, y: dir.x };
  const shaftBox = fromPoints([
    add(tail, mul(n, headWidth / 2)),
    sub(tail, mul(n, headWidth / 2)),
    add(tip, mul(n, headWidth / 2)),
    sub(tip, mul(n, headWidth / 2)),
  ]);
  let label: VectorLayout["label"] = null;
  const text = pStr(p, "label");
  if (text) {
    const pill = pillLayout(text, resolveTextSize(p, theme, "labelSize", "small"), 600);
    const vertical = Math.abs(dir.y) >= Math.abs(dir.x);
    const side = pStr(p, "labelSide", "auto");
    let normal = vertical ? { x: 1, y: 0 } : { x: 0, y: -1 };
    if (side === "left") normal = { x: -1, y: 0 };
    else if (side === "right") normal = { x: 1, y: 0 };
    else if (side === "above") normal = { x: 0, y: -1 };
    else if (side === "below") normal = { x: 0, y: 1 };
    // Near the tip by default: arrows often start inside the object they act on.
    const along = side === "end" ? 1 : side === "start" ? 0.15 : 0.72;
    const base = add(tail, mul(dir, length * along));
    const reach = (Math.abs(normal.x) * pill.w) / 2 + (Math.abs(normal.y) * pill.h) / 2;
    const center = add(base, mul(normal, headWidth / 2 + 12 + reach));
    label = { ...pill, center };
  }
  const bbox = label ? union(shaftBox, centered(label.center.x, label.center.y, label.w, label.h)) : shaftBox;
  return { tail, tip, dir, length, thickness, headLength, headWidth, label, bbox };
}

export const vectorArrow = defineComponentSpec({
  type: "vector_arrow",
  category: "annotation",
  description:
    "A bold physics vector (force, velocity, acceleration). Length = |magnitude| x scale px (clamped), so bind `magnitude` to a solver output to keep arrows proportional. `role` picks the concept color (force_gravity, force_buoyant, force_normal, force_applied, velocity).",
  params: z.strictObject({
    from: Point.describe("Tail point (or tip, with origin: 'head')"),
    direction: z.union([z.enum(["up", "down", "left", "right"]), z.number().min(-360).max(360)]).default("down"),
    magnitude: NumOrBind.default(1),
    scale: z.number().min(0.01).max(1000).default(12).describe("Pixels per unit of magnitude"),
    minLength: z.number().min(0).max(400).default(36),
    maxLength: z.number().min(10).max(900).default(360),
    origin: z.enum(["tail", "head", "center"]).default("tail"),
    role: ColorRef.default("force_applied"),
    thickness: z.number().min(2).max(40).default(10),
    label: TextOrBind.optional(),
    labelSide: z.enum(["auto", "left", "right", "above", "below", "start", "end"]).default("auto"),
    labelSize: TextSize.default("small"),
  }),
  geometry: (p, ctx) => {
    const l = vectorLayout(p, ctx.theme);
    return {
      bbox: l.bbox,
      anchors: {
        tail: l.tail,
        tip: l.tip,
        mid: { x: (l.tail.x + l.tip.x) / 2, y: (l.tail.y + l.tip.y) / 2 },
        ...(l.label ? { label: l.label.center } : {}),
      },
    };
  },
  anchors: ["tail", "tip", "mid", "label"],
  entrance: "draw",
  draw: true,
  worldSpace: true,
  strokeLine: (p, theme) => {
    const l = vectorLayout(p, theme);
    return densify([l.tail, l.tip]);
  },
  pointParams: ["from"],
  colorParams: ["role"],
  textParams: ["label"],
  semanticRoles: ["force", "velocity", "vector"],
  examples: [
    {
      type: "vector_arrow",
      params: {
        from: "block.center",
        direction: "down",
        magnitude: { bind: "buoy.weight" },
        scale: 6,
        role: "force_gravity",
        label: { bind: "buoy.weight", format: "W = 0.0 N" },
      },
    },
  ],
});

// ------------------------------------------------------------------ dimension

export interface DimensionLayout {
  a: Vec2;
  b: Vec2;
  normal: Vec2;
  label: (PillLayout & { center: Vec2 }) | null;
  bbox: Rect;
}

export function dimensionLayout(p: Params, theme: Theme): DimensionLayout {
  const from = pVec(p, "from");
  const to = pVec(p, "to");
  const u = normalize(sub(to, from));
  let normal = { x: -u.y, y: u.x };
  if (normal.x < 0 || (normal.x === 0 && normal.y > 0)) normal = mul(normal, -1);
  const off = pNum(p, "offset", 0);
  const a = add(from, mul(normal, off));
  const b = add(to, mul(normal, off));
  const text = pStr(p, "label");
  let label: DimensionLayout["label"] = null;
  if (text) {
    const pill = pillLayout(text, resolveTextSize(p, theme, "labelSize", "small"), 600);
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    const reach = (Math.abs(normal.x) * pill.w) / 2 + (Math.abs(normal.y) * pill.h) / 2;
    label = { ...pill, center: add(mid, mul(normal, reach + 12)) };
  }
  const ends = fromPoints([
    add(a, mul(normal, 12)),
    sub(a, mul(normal, 12)),
    add(b, mul(normal, 12)),
    sub(b, mul(normal, 12)),
  ]);
  return {
    a,
    b,
    normal,
    label,
    bbox: label ? union(ends, centered(label.center.x, label.center.y, label.w, label.h)) : ends,
  };
}

export const dimension = defineComponentSpec({
  type: "dimension",
  category: "annotation",
  description:
    "A measurement line with end ticks and a label (e.g. a height or a rise in water level). `offset` shifts it sideways from the measured points.",
  params: z.strictObject({
    from: Point,
    to: Point,
    offset: z.number().min(-400).max(400).default(0),
    label: TextOrBind.optional(),
    labelSize: TextSize.default("small"),
    role: ColorRef.default("muted"),
  }),
  geometry: (p, ctx) => {
    const l = dimensionLayout(p, ctx.theme);
    return { bbox: l.bbox, anchors: { start: l.a, end: l.b, ...(l.label ? { label: l.label.center } : {}) } };
  },
  anchors: ["start", "end", "label"],
  entrance: "draw",
  draw: true,
  worldSpace: true,
  strokeLine: (p, theme) => {
    const l = dimensionLayout(p, theme);
    return densify([l.a, l.b]);
  },
  pointParams: ["from", "to"],
  colorParams: ["role"],
  textParams: ["label"],
  semanticRoles: ["measurement"],
  examples: [{ type: "dimension", params: { from: "tank.floor", to: "tank.water_surface", offset: 260, label: "h" } }],
});

// ------------------------------------------------------------------ bracket

export const bracket = defineComponentSpec({
  type: "bracket",
  category: "annotation",
  description: "A curly brace spanning two points, bulging to one side, with an optional label.",
  params: z.strictObject({
    from: Point,
    to: Point,
    side: z.enum(["left", "right"]).default("right"),
    depth: z.number().min(6).max(80).default(20),
    label: TextOrBind.optional(),
    role: ColorRef.default("muted"),
  }),
  geometry: (p, ctx) => {
    const l = bracketLayout(p, ctx.theme);
    return { bbox: l.bbox, anchors: { tip: l.tip, ...(l.label ? { label: l.label.center } : {}) } };
  },
  anchors: ["tip", "label"],
  entrance: "draw",
  draw: true,
  worldSpace: true,
  strokeLine: (p, theme) => {
    const l = bracketLayout(p, theme);
    const half = mul(l.n, l.depth / 2);
    const m = { x: (l.a.x + l.b.x) / 2, y: (l.a.y + l.b.y) / 2 };
    return densify([l.a, add(l.a, half), add(m, half), l.tip, add(m, half), add(l.b, half), l.b]);
  },
  pointParams: ["from", "to"],
  colorParams: ["role"],
  textParams: ["label"],
  semanticRoles: ["grouping"],
  examples: [{ type: "bracket", params: { from: "block.top_right", to: "block.bottom_right", label: "submerged" } }],
});

export function bracketLayout(p: Params, theme: Theme) {
  const a = pVec(p, "from");
  const b = pVec(p, "to");
  const u = normalize(sub(b, a));
  let n = { x: -u.y, y: u.x };
  if (pStr(p, "side", "right") === "left") n = mul(n, -1);
  const depth = pNum(p, "depth", 20);
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  const tip = add(mid, mul(n, depth));
  const text = pStr(p, "label");
  let label: (PillLayout & { center: Vec2 }) | null = null;
  if (text) {
    const pill = pillLayout(text, theme.typography.sizes.small, 600);
    const reach = (Math.abs(n.x) * pill.w) / 2 + (Math.abs(n.y) * pill.h) / 2;
    label = { ...pill, center: add(tip, mul(n, reach + 10)) };
  }
  const box = fromPoints([a, b, tip]);
  return {
    a,
    b,
    n,
    u,
    depth,
    tip,
    label,
    bbox: label ? union(box, centered(label.center.x, label.center.y, label.w, label.h)) : box,
  };
}

// ------------------------------------------------------------------ label

export const LABEL_STYLES = ["plain", "pill", "tag"] as const;

export function labelLayout(p: Params, theme: Theme) {
  const style = pStr(p, "style", "pill");
  const size = resolveTextSize(p, theme, "size", "label");
  const weight = resolveWeight(p, "weight", 600);
  const maxWidth = pNum(p, "maxWidth", 0) || undefined;
  const pill = pillLayout(pStr(p, "text"), size, weight, maxWidth);
  if (style === "plain") return { ...pill, w: pill.text.width, h: pill.text.height, padX: 0, padY: 0, style };
  return { ...pill, style };
}

export const label = defineComponentSpec({
  type: "label",
  category: "annotation",
  description:
    "A short text label, usually attached to an object by the `label` action. Styles: plain text, a pill, or a colored tag. `pointTo` draws a leader line to a point.",
  params: z.strictObject({
    text: TextOrBind,
    style: z.enum(LABEL_STYLES).default("pill"),
    color: ColorRef.default("text"),
    size: TextSize.default("label"),
    weight: Weight.default("semibold"),
    maxWidth: z.number().min(40).max(1200).optional(),
    pointTo: Point.optional(),
  }),
  geometry: (p, ctx) => {
    const l = labelLayout(p, ctx.theme);
    return { bbox: centered(0, 0, l.w, l.h) };
  },
  entrance: "pop",
  labelLike: true,
  pointParams: ["pointTo"],
  colorParams: ["color"],
  textParams: ["text"],
  semanticRoles: ["label"],
  examples: [
    {
      type: "label",
      params: { text: "water", style: "tag", color: "fluid" },
      place: { relativeTo: "tank", side: "left" },
    },
  ],
});

// ------------------------------------------------------------------ callout

export function calloutLayout(p: Params, theme: Theme) {
  const size = resolveTextSize(p, theme, "size", "label");
  const maxWidth = pNum(p, "maxWidth", 360);
  const text = layoutText(pStr(p, "text"), { size, weight: 400, maxWidth, align: "left", lineHeight: 1.3 });
  const pad = size * 0.7;
  return { text, w: text.width + 2 * pad, h: text.height + 2 * pad, pad };
}

export const callout = defineComponentSpec({
  type: "callout",
  category: "annotation",
  description:
    "A small explanatory box with a pointer to a point of interest (`target`). Place the box with `place`; the pointer follows the target.",
  params: z.strictObject({
    text: TextOrBind,
    target: Point.optional(),
    size: TextSize.default("label"),
    maxWidth: z.number().min(80).max(800).default(360),
    role: ColorRef.default("accent"),
  }),
  geometry: (p, ctx) => {
    const l = calloutLayout(p, ctx.theme);
    return { bbox: centered(0, 0, l.w, l.h) };
  },
  entrance: "pop",
  labelLike: true,
  pointParams: ["target"],
  colorParams: ["role"],
  textParams: ["text"],
  semanticRoles: ["explanation"],
  examples: [
    {
      type: "callout",
      params: { text: "The block pushes water **aside**.", target: "tank.water_surface" },
      place: { slot: "stage.top_right" },
    },
  ],
});

// ------------------------------------------------------------------ note (annotation)

export function noteLayout(p: Params, theme: Theme) {
  const width = pNum(p, "width", 380);
  const pad = 22;
  const iconSize = p.icon ? 44 : 0;
  const textW = width - 2 * pad - (iconSize ? iconSize + 14 : 0);
  const title = pStr(p, "title")
    ? layoutText(pStr(p, "title"), { size: theme.typography.sizes.label, weight: 700, maxWidth: textW, align: "left" })
    : null;
  const body = layoutText(pStr(p, "text"), {
    size: theme.typography.sizes.small,
    weight: 400,
    maxWidth: textW,
    align: "left",
    lineHeight: 1.35,
  });
  const h = pad * 2 + (title ? title.height + 6 : 0) + body.height;
  return { width, height: Math.max(h, iconSize + 2 * pad), pad, iconSize, title, body };
}

export const note = defineComponentSpec({
  type: "note",
  category: "annotation",
  description:
    "An annotation card: optional icon and title, and a short explanation. Good for 'Key idea' or 'Remember' moments.",
  params: z.strictObject({
    text: TextOrBind,
    title: z.string().max(60).optional(),
    icon: IconName.optional(),
    role: ColorRef.default("accent"),
    width: z.number().min(160).max(900).default(380),
  }),
  geometry: (p, ctx) => {
    const l = noteLayout(p, ctx.theme);
    return { bbox: centered(0, 0, l.width, l.height) };
  },
  entrance: "slide_up",
  labelLike: true,
  colorParams: ["role"],
  textParams: ["text", "title"],
  semanticRoles: ["key_idea", "annotation"],
  examples: [
    {
      type: "note",
      params: {
        title: "Key idea",
        icon: "lightbulb",
        text: "Floating objects displace their own **weight** of water.",
      },
    },
  ],
});

// ------------------------------------------------------------------ marker

export const marker = defineComponentSpec({
  type: "marker",
  category: "annotation",
  description:
    "A persistent highlight around another object (box, circle, underline or glow) that follows it. For a temporary emphasis use the `highlight` action instead.",
  params: z.strictObject({
    target: Id,
    shape: z.enum(["box", "circle", "underline", "glow"]).default("box"),
    padding: z.number().min(0).max(120).default(14),
    role: ColorRef.default("highlight"),
  }),
  geometry: (p) => {
    const ref = objectRefOf(p, "target");
    const pad = pNum(p, "padding", 14);
    const b = ref?.box ?? { x: 0, y: 0, w: 0, h: 0 };
    return { bbox: { x: b.x - pad, y: b.y - pad, w: b.w + 2 * pad, h: b.h + 2 * pad } };
  },
  entrance: "draw",
  draw: true,
  worldSpace: true,
  decorative: true,
  objectParams: ["target"],
  colorParams: ["role"],
  semanticRoles: ["emphasis"],
  examples: [{ type: "marker", params: { target: "equation", shape: "box" } }],
});

// ------------------------------------------------------------------ caption block

export const captionBlock = defineComponentSpec({
  type: "caption",
  category: "annotation",
  description:
    "A lower-third style text block on a soft background (for on-stage captions separate from the narration bar).",
  params: z.strictObject({
    text: TextOrBind,
    size: TextSize.default("body"),
    maxWidth: z.number().min(100).max(1500).default(900),
  }),
  geometry: (p, ctx) => {
    const t = layoutText(pStr(p, "text"), {
      size: resolveTextSize(p, ctx.theme),
      maxWidth: pNum(p, "maxWidth", 900),
      align: "center",
    });
    return { bbox: centered(0, 0, t.width + 48, t.height + 28) };
  },
  entrance: "slide_up",
  labelLike: true,
  textParams: ["text"],
  semanticRoles: ["caption"],
  examples: [{ type: "caption", params: { text: "Density decides: float or sink." }, place: { slot: "stage.bottom" } }],
});
