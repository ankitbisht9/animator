/** Tier 1 physics components. Geometry is exact so solvers and validators agree with what is drawn. */
import { z } from "zod";
import { ColorRef, Id, NumOrBind, Point } from "../dsl/primitives";
import { formatNumber } from "../expr/format";
import { clamp } from "../math/num";
import { centered, fromPoints, type Rect, union } from "../math/rect";
import type { Vec2 } from "../math/vec";
import { layoutText, type TextLayout } from "../text/layout";
import type { Theme } from "../theme/theme";
import { FACES, MATERIAL_NAMES } from "./materials";
import { defineComponentSpec } from "./registry";
import { objectRefOf, type Params, pNum, pStr, pVec } from "./spec";

const Material = z.enum(MATERIAL_NAMES).meta({ id: "Material" });
const Face = z.enum(FACES).meta({ id: "Face" });

// ------------------------------------------------------------------ container

export const CONTAINER_SHAPES = ["tank", "beaker", "cylinder", "overflow_can"] as const;

export interface ContainerLayout {
  shape: string;
  width: number;
  height: number;
  wall: number;
  /** Inner (liquid) region: x0..x1, top of opening to floor. */
  inner: { x0: number; x1: number; top: number; floor: number };
  /** y of the liquid surface (local). */
  surfaceY: number;
  level: number;
  spout: { y: number; x0: number; x1: number; tipY: number } | null;
  bbox: Rect;
}

export function containerLayout(p: Params): ContainerLayout {
  const shape = pStr(p, "shape", "tank");
  const width = pNum(p, "width", 420);
  const height = pNum(p, "height", 360);
  const wall = pNum(p, "wall", 8);
  const level = clamp(pNum(p, "level", 0.5), 0, 1);
  const inner = { x0: -width / 2 + wall, x1: width / 2 - wall, top: -height / 2, floor: height / 2 - wall };
  const innerH = inner.floor - inner.top;
  const surfaceY = inner.floor - level * innerH;
  let spout: ContainerLayout["spout"] = null;
  let bbox: Rect = { x: -width / 2, y: -height / 2, w: width, h: height };
  if (shape === "overflow_can") {
    const sy = inner.floor - clamp(pNum(p, "spoutLevel", 0.75), 0.1, 0.98) * innerH;
    spout = { y: sy, x0: width / 2, x1: width / 2 + 96, tipY: sy + 40 };
    bbox = union(bbox, { x: width / 2, y: sy - 14, w: 104, h: 62 });
  }
  return { shape, width, height, wall, inner, surfaceY, level, spout, bbox };
}

export const container = defineComponentSpec({
  type: "container",
  category: "physics",
  description:
    "A glass container with liquid: tank, beaker, measuring cylinder or overflow (Eureka) can. `level` is the fill fraction (0..1) and is driven by the buoyancy solver when a body enters. Anchors: water_surface, water_center, floor, rim, rim_left, rim_right, inner_left, inner_right, spout.",
  params: z.strictObject({
    shape: z.enum(CONTAINER_SHAPES).default("tank"),
    width: z.number().min(60).max(1400).default(420),
    height: z.number().min(60).max(800).default(360),
    level: z.number().min(0).max(1).default(0.55),
    wall: z.number().min(2).max(30).default(8),
    fluid: ColorRef.default("fluid"),
    glass: ColorRef.default("glass"),
    ticks: z.boolean().default(false).describe("Graduation marks on the side"),
    spoutLevel: z
      .number()
      .min(0.1)
      .max(0.98)
      .default(0.75)
      .describe("overflow_can only: spout height as a fill fraction"),
    ripple: z.number().min(0).max(40).default(0).describe("Surface ripple amplitude in px (animated by solvers)"),
    rippleX: z.number().min(-700).max(700).default(0),
    outflow: z
      .number()
      .min(0)
      .max(1)
      .default(0)
      .describe("overflow_can only: strength of the stream pouring from the spout (driven by the buoyancy solver)"),
    bubbles: z.boolean().default(true).describe("Gentle ambient bubbles"),
    unitsPerMeter: z.number().min(10).max(5000).default(400),
  }),
  geometry: (p) => {
    const l = containerLayout(p);
    const i = l.inner;
    const anchors: Record<string, Vec2> = {
      water_surface: { x: 0, y: l.surfaceY },
      water_center: { x: 0, y: (l.surfaceY + i.floor) / 2 },
      floor: { x: 0, y: i.floor },
      rim: { x: 0, y: -l.height / 2 },
      rim_left: { x: -l.width / 2, y: -l.height / 2 },
      rim_right: { x: l.width / 2, y: -l.height / 2 },
      inner_left: { x: i.x0, y: l.surfaceY },
      inner_right: { x: i.x1, y: l.surfaceY },
    };
    if (l.spout) anchors.spout = { x: l.spout.x1, y: l.spout.tipY };
    return { bbox: l.bbox, anchors };
  },
  anchors: [
    "water_surface",
    "water_center",
    "floor",
    "rim",
    "rim_left",
    "rim_right",
    "inner_left",
    "inner_right",
    "spout",
  ],
  entrance: "pop",
  colorParams: ["fluid", "glass"],
  semanticRoles: ["container", "fluid"],
  examples: [
    {
      type: "container",
      params: { shape: "tank", width: 420, height: 340, level: 0.55, ticks: true },
      place: { slot: "stage.center" },
    },
  ],
});

// ------------------------------------------------------------------ block & ball

export const block = defineComponentSpec({
  type: "block",
  category: "physics",
  description:
    "A solid block with a recognizable material look (wood, cork, ice, plastic, stone, metal, gold...). The material also sets the default density for the buoyancy solver. Optional friendly face for younger audiences.",
  params: z.strictObject({
    width: z.number().min(10).max(800).default(120),
    height: z.number().min(10).max(800).default(100),
    material: Material.default("wood"),
    face: Face.default("none"),
    label: z.string().max(24).optional().describe("Short text on the block, e.g. 'A' or '2 kg'"),
    cornerRadius: z.number().min(0).max(60).default(10),
  }),
  geometry: (p) => {
    const w = pNum(p, "width", 120);
    const h = pNum(p, "height", 100);
    return {
      bbox: centered(0, 0, w, h),
      anchors: { attach_top: { x: 0, y: -h / 2 }, attach_bottom: { x: 0, y: h / 2 } },
    };
  },
  anchors: ["attach_top", "attach_bottom"],
  entrance: "pop",
  semanticRoles: ["body", "object"],
  examples: [
    {
      type: "block",
      params: { width: 120, height: 100, material: "wood" },
      place: { relativeTo: "tank", side: "above", gap: 80 },
    },
  ],
});

export const ball = defineComponentSpec({
  type: "ball",
  category: "physics",
  description: "A glossy ball with a material look. Optional friendly face.",
  params: z.strictObject({
    radius: z.number().min(4).max(400).default(48),
    material: Material.default("plastic"),
    face: Face.default("none"),
  }),
  geometry: (p) => {
    const r = pNum(p, "radius", 48);
    return { bbox: centered(0, 0, 2 * r, 2 * r), anchors: { attach_top: { x: 0, y: -r } } };
  },
  anchors: ["attach_top"],
  entrance: "pop",
  semanticRoles: ["body", "object"],
  examples: [{ type: "ball", params: { radius: 40, material: "plastic" } }],
});

// ------------------------------------------------------------------ rope, spring

export function ropePath(p: Params): { a: Vec2; b: Vec2; c: Vec2 } {
  const a = pVec(p, "from");
  const b = pVec(p, "to");
  const sag = pNum(p, "sag", 0);
  return { a, b, c: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 + sag } };
}

export const rope = defineComponentSpec({
  type: "rope",
  category: "physics",
  description: "A rope or string between two points (anchor references follow their objects). `sag` makes it droop.",
  params: z.strictObject({
    from: Point,
    to: Point,
    sag: z.number().min(-300).max(300).default(0),
    thickness: z.number().min(1).max(20).default(4),
    role: ColorRef.default("muted"),
  }),
  geometry: (p) => {
    const r = ropePath(p);
    return { bbox: fromPoints([r.a, r.b, r.c]), anchors: { start: r.a, end: r.b } };
  },
  anchors: ["start", "end"],
  entrance: "draw",
  draw: true,
  worldSpace: true,
  pointParams: ["from", "to"],
  colorParams: ["role"],
  semanticRoles: ["connector", "tension"],
  examples: [{ type: "rope", params: { from: "scale.hook", to: "block.attach_top" } }],
});

export function springPoints(p: Params): Vec2[] {
  const a = pVec(p, "from");
  const b = pVec(p, "to");
  const coils = Math.round(pNum(p, "coils", 10));
  const w = pNum(p, "width", 34);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const lead = Math.min(18, len * 0.12);
  const pts: Vec2[] = [a, { x: a.x + ux * lead, y: a.y + uy * lead }];
  const body = len - 2 * lead;
  const n = coils * 2;
  for (let i = 1; i < n; i++) {
    const s = lead + (body * i) / n;
    const side = i % 2 === 1 ? 1 : -1;
    pts.push({ x: a.x + ux * s - uy * side * (w / 2), y: a.y + uy * s + ux * side * (w / 2) });
  }
  pts.push({ x: b.x - ux * lead, y: b.y - uy * lead }, b);
  return pts;
}

export const spring = defineComponentSpec({
  type: "spring",
  category: "physics",
  description: "A coil spring between two points; it stretches and compresses as its endpoints move.",
  params: z.strictObject({
    from: Point,
    to: Point,
    coils: z.number().int().min(2).max(40).default(10),
    width: z.number().min(6).max(120).default(34),
    thickness: z.number().min(1).max(12).default(4),
    role: ColorRef.default("muted"),
  }),
  geometry: (p) => {
    const pts = springPoints(p);
    return { bbox: fromPoints(pts), anchors: { start: pts[0] as Vec2, end: pts[pts.length - 1] as Vec2 } };
  },
  anchors: ["start", "end"],
  entrance: "draw",
  draw: true,
  worldSpace: true,
  pointParams: ["from", "to"],
  colorParams: ["role"],
  semanticRoles: ["spring", "elastic"],
  examples: [{ type: "spring", params: { from: { x: 800, y: 150 }, to: "mass.attach_top", coils: 12 } }],
});

// ------------------------------------------------------------------ pulley, pendulum, balance

export const pulley = defineComponentSpec({
  type: "pulley",
  category: "physics",
  description: "A pulley wheel on a ceiling mount. Ropes leave from its 'left' and 'right' anchors.",
  params: z.strictObject({
    radius: z.number().min(10).max(200).default(42),
    mount: z.number().min(0).max(300).default(60).describe("Length of the mounting bar above the wheel"),
    angle: z.number().default(0).describe("Wheel rotation in degrees (animate to show turning)"),
  }),
  geometry: (p) => {
    const r = pNum(p, "radius", 42);
    const m = pNum(p, "mount", 60);
    return {
      bbox: { x: -r - 4, y: -r - m, w: 2 * r + 8, h: 2 * r + m + 4 },
      anchors: { left: { x: -r, y: 0 }, right: { x: r, y: 0 }, axle: { x: 0, y: 0 }, mount: { x: 0, y: -r - m } },
    };
  },
  anchors: ["left", "right", "axle", "mount"],
  entrance: "pop",
  semanticRoles: ["machine"],
  examples: [{ type: "pulley", params: { radius: 40 }, place: { slot: "stage.top" } }],
});

export function pendulumBob(p: Params): Vec2 {
  const L = pNum(p, "length", 280);
  const a = (pNum(p, "angle", 20) * Math.PI) / 180;
  return { x: L * Math.sin(a), y: L * Math.cos(a) };
}

export const pendulum = defineComponentSpec({
  type: "pendulum",
  category: "physics",
  description:
    "A pendulum hanging from a pivot (the object origin). `angle` is degrees from vertical; the pendulum solver animates it.",
  params: z.strictObject({
    length: z.number().min(20).max(800).default(280),
    angle: z.number().min(-179).max(179).default(20),
    bobRadius: z.number().min(6).max(120).default(32),
    material: Material.default("metal"),
    showArc: z.boolean().default(true),
  }),
  geometry: (p) => {
    const bob = pendulumBob(p);
    const r = pNum(p, "bobRadius", 32);
    const L = pNum(p, "length", 280);
    return {
      bbox: union({ x: -L * 0.75, y: -16, w: L * 1.5, h: 16 }, centered(bob.x, bob.y, 2 * r, 2 * r)),
      anchors: { pivot: { x: 0, y: 0 }, bob },
    };
  },
  anchors: ["pivot", "bob"],
  entrance: "pop",
  semanticRoles: ["oscillator"],
  examples: [{ type: "pendulum", params: { length: 300, angle: 25 }, place: { slot: "stage.top" } }],
});

export function balanceLayout(p: Params) {
  const arm = pNum(p, "armLength", 300);
  const tilt = (pNum(p, "tilt", 0) * Math.PI) / 180;
  const hang = pNum(p, "hang", 120);
  const post = pNum(p, "height", 220);
  const cx = Math.cos(tilt) * (arm / 2);
  const cy = Math.sin(tilt) * (arm / 2);
  return {
    arm,
    tilt,
    hang,
    post,
    left: { x: -cx, y: -cy + hang },
    right: { x: cx, y: cy + hang },
    beamL: { x: -cx, y: -cy },
    beamR: { x: cx, y: cy },
  };
}

export const balance = defineComponentSpec({
  type: "balance",
  category: "physics",
  description:
    "A two-pan beam balance. Origin is the pivot; `tilt` (degrees, positive = right side down) shows which side is heavier.",
  params: z.strictObject({
    armLength: z.number().min(80).max(900).default(300),
    tilt: z.number().min(-30).max(30).default(0),
    hang: z.number().min(30).max(400).default(120),
    height: z.number().min(40).max(600).default(220),
    panWidth: z.number().min(30).max(300).default(110),
  }),
  geometry: (p) => {
    const l = balanceLayout(p);
    const pw = pNum(p, "panWidth", 110);
    return {
      bbox: { x: -l.arm / 2 - pw / 2, y: -40, w: l.arm + pw, h: Math.max(l.post, l.hang + 40) + 40 },
      anchors: { pivot: { x: 0, y: 0 }, left_pan: l.left, right_pan: l.right, base: { x: 0, y: l.post } },
    };
  },
  anchors: ["pivot", "left_pan", "right_pan", "base"],
  entrance: "pop",
  semanticRoles: ["comparison", "weight"],
  examples: [{ type: "balance", params: { tilt: -8 }, place: { slot: "stage.center" } }],
});

// ------------------------------------------------------------------ spring scale

export function springScaleLayout(p: Params) {
  const w = pNum(p, "width", 66);
  const h = pNum(p, "height", 230);
  const hook = pNum(p, "hook", 38);
  const max = Math.max(1e-6, pNum(p, "max", 20));
  const reading = pNum(p, "reading", 0);
  const top = -h / 2;
  const scaleTop = top + 34;
  const scaleBottom = h / 2 - 26;
  const frac = clamp(reading / max, 0, 1);
  return {
    w,
    h,
    hook,
    max,
    reading,
    top,
    scaleTop,
    scaleBottom,
    frac,
    pointerY: scaleTop + frac * (scaleBottom - scaleTop),
  };
}

export const springScale = defineComponentSpec({
  type: "spring_scale",
  category: "physics",
  description:
    "A Newton meter (spring balance). Bind `reading` to a force (e.g. the buoyancy solver's apparentWeight) and the pointer moves along the scale. Hang objects from the 'hook' anchor with a rope.",
  params: z.strictObject({
    reading: NumOrBind.default(0),
    max: z.number().min(0.001).max(1e6).default(20),
    unit: z.string().max(8).default("N"),
    format: z.string().max(16).default("0.0"),
    width: z.number().min(30).max(200).default(66),
    height: z.number().min(100).max(600).default(230),
    hook: z.number().min(10).max(120).default(38),
    showValue: z.boolean().default(true),
  }),
  geometry: (p) => {
    const l = springScaleLayout(p);
    return {
      bbox: { x: -l.w / 2, y: l.top - 22, w: l.w + (p.showValue === false ? 0 : 130), h: l.h + 22 + l.hook },
      anchors: {
        top: { x: 0, y: l.top - 22 },
        hook: { x: 0, y: l.h / 2 + l.hook },
        pointer: { x: l.w / 2, y: l.pointerY },
      },
    };
  },
  anchors: ["top", "hook", "pointer"],
  entrance: "pop",
  semanticRoles: ["measurement", "force"],
  examples: [
    { type: "spring_scale", params: { reading: { bind: "buoy.apparentWeight" }, max: 20 }, place: { x: 800, y: 260 } },
  ],
});

// ------------------------------------------------------------------ pipe & flow

const PipeSection = z.strictObject({
  id: Id,
  x: z.number().min(0).max(2000).describe("Distance from the pipe start in px"),
  width: z.number().min(6).max(600).describe("Inner diameter in px"),
  y: z.number().min(-400).max(400).default(0).describe("Centerline offset (height change)"),
});

export interface PipeLayout {
  length: number;
  x0: number;
  sections: { id: string; x: number; width: number; y: number }[];
  widthAt: (x: number) => number;
  centerAt: (x: number) => number;
  wall: number;
  bbox: Rect;
}

const smooth = (u: number) => u * u * (3 - 2 * u);

export function pipeLayout(p: Params): PipeLayout {
  const raw = (
    Array.isArray(p.sections) ? (p.sections as { id: string; x: number; width: number; y?: number }[]) : []
  ).map((s) => ({
    id: s.id,
    x: s.x,
    width: s.width,
    y: s.y ?? 0,
  }));
  const sections =
    raw.length >= 2
      ? [...raw].sort((a, b) => a.x - b.x)
      : [
          { id: "a", x: 0, width: 120, y: 0 },
          { id: "b", x: 600, width: 120, y: 0 },
        ];
  const first = sections[0] as { x: number };
  const last = sections[sections.length - 1] as { x: number };
  const length = Math.max(1, last.x - first.x);
  const x0 = -length / 2 - first.x;
  const seg = (x: number) => {
    let i = 0;
    while (i < sections.length - 2 && x > (sections[i + 1] as { x: number }).x) i++;
    const a = sections[i] as { x: number; width: number; y: number };
    const b = sections[i + 1] as { x: number; width: number; y: number };
    const u = b.x > a.x ? clamp((x - a.x) / (b.x - a.x), 0, 1) : 0;
    return { a, b, u: smooth(u) };
  };
  const widthAt = (lx: number) => {
    const { a, b, u } = seg(lx - x0);
    return a.width + (b.width - a.width) * u;
  };
  const centerAt = (lx: number) => {
    const { a, b, u } = seg(lx - x0);
    return a.y + (b.y - a.y) * u;
  };
  const wall = pNum(p, "wall", 10);
  let minY = 0;
  let maxY = 0;
  for (const s of sections) {
    minY = Math.min(minY, s.y - s.width / 2 - wall);
    maxY = Math.max(maxY, s.y + s.width / 2 + wall);
  }
  return {
    length,
    x0,
    sections: sections.map((s) => ({ ...s, x: s.x + x0 })),
    widthAt,
    centerAt,
    wall,
    bbox: { x: -length / 2 - 9, y: minY - 9, w: length + 18, h: maxY - minY + 18 },
  };
}

const sectionIds = (p: Params) => (Array.isArray(p.sections) ? (p.sections as { id: string }[]).map((s) => s.id) : []);

export const pipe = defineComponentSpec({
  type: "pipe",
  category: "physics",
  description:
    "A horizontal pipe whose inner diameter (and height) varies smoothly between named sections, e.g. a Venturi tube. Anchors: inlet, outlet, and per section 'section_<id>', 'section_<id>_top', 'section_<id>_bottom'. Add flow_particles to show the flow.",
  params: z.strictObject({
    sections: z.array(PipeSection).min(2).max(8),
    wall: z.number().min(2).max(40).default(10),
    fluid: ColorRef.default("fluid"),
    showSections: z.boolean().default(true).describe("Mark and name each section"),
  }),
  geometry: (p) => {
    const l = pipeLayout(p);
    const anchors: Record<string, Vec2> = {
      inlet: { x: -l.length / 2, y: l.centerAt(-l.length / 2) },
      outlet: { x: l.length / 2, y: l.centerAt(l.length / 2) },
    };
    for (const s of l.sections) {
      anchors[`section_${s.id}`] = { x: s.x, y: s.y };
      anchors[`section_${s.id}_top`] = { x: s.x, y: s.y - s.width / 2 - l.wall };
      anchors[`section_${s.id}_bottom`] = { x: s.x, y: s.y + s.width / 2 + l.wall };
    }
    return { bbox: l.bbox, anchors };
  },
  anchors: ["inlet", "outlet"],
  dynamicAnchors: (p) => sectionIds(p).flatMap((id) => [`section_${id}`, `section_${id}_top`, `section_${id}_bottom`]),
  entrance: "draw",
  draw: true,
  colorParams: ["fluid"],
  semanticRoles: ["pipe", "flow"],
  examples: [
    {
      type: "pipe",
      params: {
        sections: [
          { id: "wide", x: 0, width: 160 },
          { id: "narrow", x: 420, width: 70 },
          { id: "out", x: 840, width: 160 },
        ],
      },
      place: { slot: "stage.center" },
    },
  ],
});

/** Travel-time table for particles: speed ∝ 1 / area ∝ 1 / width² (continuity). */
export function flowTable(l: PipeLayout, inletSpeed: number) {
  const n = 240;
  const xs: number[] = [];
  const ts: number[] = [0];
  const w0 = l.widthAt(-l.length / 2);
  for (let i = 0; i <= n; i++) xs.push(-l.length / 2 + (l.length * i) / n);
  for (let i = 1; i <= n; i++) {
    const xm = ((xs[i - 1] as number) + (xs[i] as number)) / 2;
    const v = Math.max(1e-6, inletSpeed * (w0 / l.widthAt(xm)) ** 2);
    ts.push((ts[i - 1] as number) + l.length / n / v);
  }
  return { xs, ts, total: ts[n] as number, speedAt: (x: number) => inletSpeed * (w0 / l.widthAt(x)) ** 2 };
}

export const flowParticles = defineComponentSpec({
  type: "flow_particles",
  category: "physics",
  description:
    "Seeded particles streaming through a pipe. They speed up where the pipe narrows (continuity: v ∝ 1/A). `speed` is the inlet speed in px/s; the fluid_flow solver can drive it.",
  params: z.strictObject({
    pipe: Id,
    count: z.number().int().min(1).max(200).default(44),
    speed: z.number().min(0).max(2000).default(90),
    size: z.number().min(1).max(30).default(6),
    role: ColorRef.default("surface"),
    streaks: z.boolean().default(true).describe("Stretch particles along the flow in proportion to their speed"),
  }),
  geometry: (p) => ({ bbox: objectRefOf(p, "pipe")?.box ?? { x: 0, y: 0, w: 0, h: 0 } }),
  entrance: "fade",
  worldSpace: true,
  decorative: true,
  objectParams: ["pipe"],
  colorParams: ["role"],
  semanticRoles: ["flow"],
  examples: [{ type: "flow_particles", params: { pipe: "venturi", count: 50, speed: 80 } }],
});

// ------------------------------------------------------------------ gauges

export function gaugeLayout(p: Params, theme: Theme) {
  const r = pNum(p, "radius", 64);
  const stem = pNum(p, "stem", 34);
  const max = Math.max(1e-6, pNum(p, "max", 200));
  const value = pNum(p, "value", 0);
  const frac = clamp(value / max, 0, 1);
  // Sweep from 225° (lower left) clockwise to -45° (lower right).
  const angle = (-225 + 270 * frac) * (Math.PI / 180);
  const text = `${formatNumber(value, pStr(p, "format", "0"))} ${pStr(p, "unit", "kPa")}`.trim();
  const label = pStr(p, "label")
    ? layoutText(pStr(p, "label"), { size: theme.typography.sizes.small, weight: 600, align: "center" })
    : null;
  const value_ = layoutText(text, { size: Math.max(19, r * 0.24), weight: 700, align: "center" });
  return { r, stem, max, frac, angle, value: value_, label };
}

export const pressureGauge = defineComponentSpec({
  type: "pressure_gauge",
  category: "physics",
  description:
    "A dial pressure gauge on a short stem (anchor 'stem' at its bottom). Bind `value` to a pressure; the needle and readout follow.",
  params: z.strictObject({
    value: NumOrBind.default(0),
    max: z.number().min(0.001).max(1e9).default(200),
    unit: z.string().max(8).default("kPa"),
    format: z.string().max(16).default("0"),
    radius: z.number().min(20).max(200).default(64),
    stem: z.number().min(0).max(200).default(34),
    label: z.string().max(30).optional(),
    role: ColorRef.default("pressure"),
  }),
  geometry: (p, ctx) => {
    const l = gaugeLayout(p, ctx.theme);
    const labelH = l.label ? l.label.height + 8 : 0;
    return {
      bbox: { x: -l.r - 6, y: -l.r - 6 - labelH, w: 2 * l.r + 12, h: 2 * l.r + l.stem + 12 + labelH },
      anchors: { stem: { x: 0, y: l.r + l.stem } },
    };
  },
  anchors: ["stem"],
  entrance: "pop",
  colorParams: ["role"],
  semanticRoles: ["measurement", "pressure"],
  examples: [
    {
      type: "pressure_gauge",
      params: { value: { bind: "flow.p_wide" }, max: 200, label: "P₁" },
      place: { at: "venturi.section_wide_top", anchor: "stem" },
    },
  ],
});

export function manometerLayout(p: Params) {
  const w = pNum(p, "width", 130);
  const h = pNum(p, "height", 260);
  const tube = pNum(p, "tube", 24);
  const diff = pNum(p, "difference", 0);
  const base = h / 2 - tube;
  const mid = -h / 2 + (1 - clamp(pNum(p, "level", 0.55), 0.1, 0.9)) * (h - tube);
  const half = clamp(diff / 2, -(mid + h / 2) + 10, base - mid - 10);
  return { w, h, tube, leftLevel: mid + half, rightLevel: mid - half, xL: -w / 2 + tube / 2, xR: w / 2 - tube / 2 };
}

export const manometer = defineComponentSpec({
  type: "manometer",
  category: "physics",
  description:
    "A U-tube manometer. `difference` is the level difference in px (left lower when positive, i.e. higher pressure on the left).",
  params: z.strictObject({
    difference: NumOrBind.default(0),
    width: z.number().min(40).max(400).default(130),
    height: z.number().min(60).max(600).default(260),
    tube: z.number().min(8).max(60).default(24),
    level: z.number().min(0.1).max(0.9).default(0.55),
    fluid: ColorRef.default("pressure"),
  }),
  geometry: (p) => {
    const l = manometerLayout(p);
    return {
      bbox: centered(0, 0, l.w, l.h),
      anchors: {
        left_top: { x: l.xL, y: -l.h / 2 },
        right_top: { x: l.xR, y: -l.h / 2 },
        left_level: { x: l.xL, y: l.leftLevel },
        right_level: { x: l.xR, y: l.rightLevel },
      },
    };
  },
  anchors: ["left_top", "right_top", "left_level", "right_level"],
  entrance: "pop",
  colorParams: ["fluid"],
  semanticRoles: ["measurement", "pressure"],
  examples: [{ type: "manometer", params: { difference: { bind: "flow.dp_px" } } }],
});

// ------------------------------------------------------------------ generic particles

export const particles = defineComponentSpec({
  type: "particles",
  category: "physics",
  description: "Seeded ambient particles in a region: rising bubbles, sparkles, drifting dots, snow or steam.",
  params: z.strictObject({
    kind: z.enum(["bubbles", "sparkles", "dots", "snow", "steam"]).default("bubbles"),
    count: z.number().int().min(1).max(200).default(24),
    width: z.number().min(10).max(1600).default(400),
    height: z.number().min(10).max(900).default(300),
    speed: z.number().min(0).max(10).default(1),
    size: z.number().min(1).max(60).default(8),
    role: ColorRef.default("surface"),
  }),
  geometry: (p) => ({ bbox: centered(0, 0, pNum(p, "width", 400), pNum(p, "height", 300)) }),
  entrance: "fade",
  decorative: true,
  colorParams: ["role"],
  semanticRoles: ["ambience"],
  examples: [{ type: "particles", params: { kind: "bubbles", width: 300, height: 200 } }],
});

export type { TextLayout };
