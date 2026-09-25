/** Tier 1 math components: equation, readout, graph, axes, grid. */
import { z } from "zod";
import { ColorRef, Id, NumOrBind, Vec } from "../dsl/primitives";
import { evalExpr, exprRefs, parseExpr } from "../expr/expr";
import { formatNumber } from "../expr/format";
import { centered, type Rect } from "../math/rect";
import { checkTex } from "../mathjax/allowlist";
import { type TexLayout, typesetTex } from "../mathjax/typeset";
import { glyph, layoutText, type TextLayout } from "../text/layout";
import type { Theme } from "../theme/theme";
import { resolveTextSize, TextSize } from "./common";
import { defineComponentSpec } from "./registry";
import { type Params, pBool, pNum, pStr } from "./spec";

// ------------------------------------------------------------------ equation

const RELATIONS = new Set([
  "=",
  "\\approx",
  "<",
  ">",
  "\\leq",
  "\\geq",
  "\\le",
  "\\ge",
  "\\neq",
  "\\ne",
  "\\propto",
  "\\to",
  "\\rightarrow",
  "\\Rightarrow",
  "\\equiv",
  "\\sim",
]);
const BINARIES = new Set(["+", "-", "\\times", "\\cdot", "\\div", "\\pm", "\\mp"]);

export interface EquationPart {
  id: string;
  tex: string;
}

/** Joins parts, wrapping each in \cssId so its geometry comes back as its own group. */
export function equationTex(parts: EquationPart[]): string {
  return parts
    .map((part) => {
      const inner = `\\cssId{t-${part.id}}{${part.tex}}`;
      const trimmed = part.tex.trim();
      if (RELATIONS.has(trimmed)) return `\\mathrel{${inner}}`;
      if (BINARIES.has(trimmed)) return `\\mathbin{${inner}}`;
      return inner;
    })
    .join(" ");
}

const texCache = new Map<string, TexLayout>();

export function typesetEquation(parts: EquationPart[]): TexLayout {
  const tex = equationTex(parts);
  let hit = texCache.get(tex);
  if (!hit) {
    hit = typesetTex(tex);
    if (texCache.size > 400) texCache.clear();
    texCache.set(tex, hit);
  }
  return hit;
}

export interface EquationLayout {
  scale: number;
  width: number;
  height: number;
  /** Local position of the TeX origin (baseline start). */
  ox: number;
  oy: number;
  pad: number;
  box: boolean;
  parts: { id: string | null; d: string; bbox: Rect }[];
}

export function equationLayout(p: Params, prep: unknown): EquationLayout {
  const tex = prep as TexLayout | undefined;
  const scale = pNum(p, "size", 52) / 1000;
  const box = pBool(p, "box", false);
  const pad = box ? pNum(p, "size", 52) * 0.55 : 0;
  if (!tex) return { scale, width: 0, height: 0, ox: 0, oy: 0, pad, box, parts: [] };
  const w = tex.width * scale;
  const h = (tex.ascent + tex.descent) * scale;
  const width = w + 2 * pad;
  const height = h + 2 * pad;
  const ox = -w / 2;
  const oy = -h / 2 + tex.ascent * scale;
  return {
    scale,
    width,
    height,
    ox,
    oy,
    pad,
    box,
    parts: tex.parts.map((part) => ({
      id: part.id,
      d: part.d,
      bbox: {
        x: ox + part.bbox.x * scale,
        y: oy + part.bbox.y * scale,
        w: part.bbox.w * scale,
        h: part.bbox.h * scale,
      },
    })),
  };
}

const partsOf = (p: Params): EquationPart[] => (Array.isArray(p.parts) ? (p.parts as EquationPart[]) : []);

export const equation = defineComponentSpec({
  type: "equation",
  category: "math",
  description:
    "A typeset equation (TeX subset) split into parts with ids, so parts can be written one by one (`write` with `terms`) and highlighted individually (`highlight` with `terms`). Each part is an anchor 'term_<id>'. Numbers computed by the engine belong in readouts or bound labels, not in TeX.",
  params: z.strictObject({
    parts: z
      .array(z.strictObject({ id: Id, tex: z.string().min(1).max(200) }))
      .min(1)
      .max(24)
      .describe(
        "e.g. [{id:'fb',tex:'F_b'},{id:'eq',tex:'='},{id:'rho',tex:'\\\\rho_f'},{id:'v',tex:'V'},{id:'g',tex:'g'}]",
      ),
    size: z.number().min(16).max(160).default(52).describe("Pixels per em"),
    color: ColorRef.default("text"),
    box: z.boolean().default(false).describe("Draw a soft card behind the equation"),
  }),
  prepare: (p) => typesetEquation(partsOf(p)),
  prepareKey: (p) => equationTex(partsOf(p)),
  validate: (p) => {
    const problems: { code: string; message: string; param: string; suggestion?: string }[] = [];
    partsOf(p).forEach((part, i) => {
      for (const msg of checkTex(part.tex)) {
        problems.push({
          code: "TEX_UNSUPPORTED",
          param: `parts/${i}/tex`,
          message: msg,
          suggestion: "Use plain TeX math: letters, subscripts, \\frac, \\sqrt, Greek letters, \\cdot, \\times.",
        });
      }
    });
    const ids = partsOf(p).map((x) => x.id);
    if (new Set(ids).size !== ids.length)
      problems.push({ code: "DUPLICATE_ID", param: "parts", message: "Equation part ids must be unique." });
    if (problems.length === 0) {
      try {
        typesetEquation(partsOf(p));
      } catch (e) {
        problems.push({ code: "TEX_INVALID", param: "parts", message: `TeX error: ${(e as Error).message}` });
      }
    }
    return problems;
  },
  geometry: (p, ctx) => {
    const l = equationLayout(p, ctx.prep);
    const anchors: Record<string, { x: number; y: number }> = {};
    for (const part of l.parts)
      if (part.id) anchors[`term_${part.id}`] = { x: part.bbox.x + part.bbox.w / 2, y: part.bbox.y + part.bbox.h / 2 };
    return { bbox: centered(0, 0, l.width, l.height), anchors };
  },
  dynamicAnchors: (p) => partsOf(p).map((x) => `term_${x.id}`),
  entrance: "write",
  write: true,
  labelLike: true,
  colorParams: ["color"],
  semanticRoles: ["equation", "formula"],
  examples: [
    {
      type: "equation",
      params: {
        parts: [
          { id: "fb", tex: "F_b" },
          { id: "eq", tex: "=" },
          { id: "rho", tex: "\\rho_{fluid}" },
          { id: "v", tex: "V_{sub}" },
          { id: "g", tex: "g" },
        ],
        box: true,
      },
      place: { slot: "equation_panel" },
    },
  ],
});

// ------------------------------------------------------------------ readout

export interface ReadoutCell {
  ch: string;
  x: number;
  w: number;
}

export interface ReadoutLayout {
  width: number;
  height: number;
  pad: number;
  label: TextLayout | null;
  valueSize: number;
  unitSize: number;
  valueCells: ReadoutCell[];
  valueWidth: number;
  unit: TextLayout | null;
  style: string;
}

const DIGITS = "0123456789";

/** Digits laid out in fixed cells (tabular), so a changing number never jitters. */
export function tabularCells(text: string, size: number): { cells: ReadoutCell[]; width: number } {
  const scale = size / 1000;
  const digitW = Math.max(...[...DIGITS].map((d) => glyph(d, 700).a)) * scale;
  const cells: ReadoutCell[] = [];
  let x = 0;
  for (const ch of text) {
    const w = DIGITS.includes(ch) ? digitW : glyph(ch, 700).a * scale;
    cells.push({ ch, x, w });
    x += w;
  }
  return { cells, width: x };
}

export function readoutText(p: Params, fraction = 1): string {
  const v = pNum(p, "value", 0) * fraction;
  return formatNumber(v, pStr(p, "format", "0.0"));
}

export function readoutLayout(p: Params, theme: Theme, fraction = 1): ReadoutLayout {
  const style = pStr(p, "style", "card");
  const valueSize = resolveTextSize(p, theme, "size", "heading");
  const unitSize = Math.round(valueSize * 0.55);
  const labelText = pStr(p, "label");
  const label = labelText
    ? layoutText(labelText, { size: theme.typography.sizes.small, weight: 600, align: "left" })
    : null;
  const unitText = pStr(p, "unit");
  const unit = unitText ? layoutText(unitText, { size: unitSize, weight: 600, align: "left" }) : null;
  const { cells, width: valueWidth } = tabularCells(readoutText(p, fraction), valueSize);
  const finalWidth = tabularCells(readoutText(p, 1), valueSize).width;
  const pad = style === "plain" ? 0 : valueSize * 0.42;
  const lineW = Math.max(valueWidth, finalWidth) + (unit ? unit.width + unitSize * 0.3 : 0);
  const width = Math.max(lineW, label?.width ?? 0) + 2 * pad;
  const height = valueSize * 1.15 + (label ? label.height + 4 : 0) + 2 * pad;
  return { width, height, pad, label, valueSize, unitSize, valueCells: cells, valueWidth, unit, style };
}

export const readout = defineComponentSpec({
  type: "readout",
  category: "math",
  description:
    "A live number display (value + unit + small label). Bind `value` to a solver output or expression; digits are tabular so changing values do not jitter. Counts up from zero when it appears.",
  params: z.strictObject({
    label: z.string().max(60).optional(),
    value: NumOrBind.default(0),
    format: z.string().max(24).default("0.0"),
    unit: z.string().max(16).optional(),
    role: ColorRef.default("accent"),
    size: TextSize.default("heading"),
    style: z.enum(["card", "pill", "plain"]).default("card"),
    countUp: z.boolean().default(true),
  }),
  geometry: (p, ctx) => {
    const l = readoutLayout(p, ctx.theme);
    return { bbox: centered(0, 0, l.width, l.height) };
  },
  entrance: "pop",
  labelLike: true,
  colorParams: ["role"],
  textParams: ["label"],
  semanticRoles: ["value", "measurement"],
  examples: [
    {
      type: "readout",
      params: {
        label: "Buoyant force",
        value: { bind: "buoy.buoyantForce" },
        format: "0.0",
        unit: "N",
        role: "force_buoyant",
      },
    },
  ],
});

// ------------------------------------------------------------------ graph / axes / grid

const AxisSpec = z.strictObject({
  min: z.number(),
  max: z.number(),
  label: z.string().max(40).optional(),
  ticks: z.number().int().min(0).max(20).default(5),
});

const Series = z.strictObject({
  id: Id,
  expr: z.string().max(200).optional().describe("y as a function of x (and t for motion), e.g. 'sin(x)'"),
  points: z.array(Vec).max(400).optional().describe("Data points in axis units"),
  role: ColorRef.default("accent"),
  style: z.enum(["line", "area", "points", "bars"]).default("line"),
  label: z.string().max(40).optional(),
});

export interface GraphLayout {
  plot: Rect;
  width: number;
  height: number;
  x: { min: number; max: number; label: string; ticks: number };
  y: { min: number; max: number; label: string; ticks: number };
  toLocal: (x: number, y: number) => { x: number; y: number };
  bbox: Rect;
}

export function graphLayout(p: Params): GraphLayout {
  const width = pNum(p, "width", 560);
  const height = pNum(p, "height", 340);
  const ax = (k: string) => {
    const a = (p[k] ?? {}) as { min?: number; max?: number; label?: string; ticks?: number };
    const min = typeof a.min === "number" ? a.min : 0;
    const max = typeof a.max === "number" && a.max !== min ? a.max : min + 1;
    return { min, max, label: a.label ?? "", ticks: a.ticks ?? 5 };
  };
  const x = ax("x");
  const y = ax("y");
  const plot = { x: -width / 2, y: -height / 2, w: width, h: height };
  const toLocal = (vx: number, vy: number) => ({
    x: plot.x + ((vx - x.min) / (x.max - x.min)) * plot.w,
    y: plot.y + plot.h - ((vy - y.min) / (y.max - y.min)) * plot.h,
  });
  const left = 64;
  const bottom = x.label ? 70 : 44;
  const top = y.label ? 34 : 12;
  return {
    plot,
    width,
    height,
    x,
    y,
    toLocal,
    bbox: { x: plot.x - left, y: plot.y - top, w: plot.w + left + 24, h: plot.h + top + bottom },
  };
}

/** Sample a series in plot-local coordinates (pure function of params and t). */
export function seriesPoints(p: Params, series: z.infer<typeof Series>, t: number): { x: number; y: number }[] {
  const l = graphLayout(p);
  if (series.points?.length) return series.points.map((pt) => l.toLocal(pt.x, pt.y));
  if (!series.expr) return [];
  let ast: ReturnType<typeof parseExpr>;
  try {
    ast = parseExpr(series.expr);
  } catch {
    return [];
  }
  const out: { x: number; y: number }[] = [];
  const n = 160;
  for (let i = 0; i <= n; i++) {
    const xv = l.x.min + ((l.x.max - l.x.min) * i) / n;
    let yv: number;
    try {
      yv = evalExpr(ast, (name) => (name === "x" ? xv : name === "t" ? t : undefined));
    } catch {
      continue;
    }
    if (!Number.isFinite(yv)) continue;
    const clamped = Math.max(l.y.min, Math.min(l.y.max, yv));
    out.push(l.toLocal(xv, clamped));
  }
  return out;
}

const seriesOf = (p: Params) => (Array.isArray(p.series) ? (p.series as z.infer<typeof Series>[]) : []);

export const graph = defineComponentSpec({
  type: "graph",
  category: "math",
  description:
    "A plot with axes (y up). Series come from data points or a safe expression in x (and t for motion). `draw` reveals the curves left to right.",
  params: z.strictObject({
    width: z.number().min(120).max(1400).default(560),
    height: z.number().min(80).max(760).default(340),
    x: AxisSpec.prefault({ min: 0, max: 10 }),
    y: AxisSpec.prefault({ min: 0, max: 10 }),
    series: z.array(Series).max(6).default([]),
    grid: z.boolean().default(true),
  }),
  validate: (p) => {
    const problems: { code: string; message: string; param: string }[] = [];
    seriesOf(p).forEach((s, i) => {
      if (!s.expr) return;
      try {
        for (const ref of exprRefs(parseExpr(s.expr))) {
          if (ref !== "x" && ref !== "t")
            problems.push({
              code: "EXPR_UNKNOWN_IDENT",
              param: `series/${i}/expr`,
              message: `Series expressions may only use x and t, not '${ref}'.`,
            });
        }
      } catch (e) {
        problems.push({ code: "EXPR_PARSE", param: `series/${i}/expr`, message: (e as Error).message });
      }
    });
    return problems;
  },
  geometry: (p) => {
    const l = graphLayout(p);
    const anchors: Record<string, { x: number; y: number }> = {
      origin: l.toLocal(Math.max(l.x.min, Math.min(l.x.max, 0)), Math.max(l.y.min, Math.min(l.y.max, 0))),
      plot_center: { x: l.plot.x + l.plot.w / 2, y: l.plot.y + l.plot.h / 2 },
    };
    for (const s of seriesOf(p)) {
      const pts = seriesPoints(p, s, 0);
      const last = pts[pts.length - 1];
      if (last) anchors[`series_${s.id}_end`] = last;
    }
    return { bbox: l.bbox, anchors };
  },
  anchors: ["origin", "plot_center"],
  dynamicAnchors: (p) => seriesOf(p).map((s) => `series_${s.id}_end`),
  entrance: "draw",
  draw: true,
  semanticRoles: ["graph", "chart", "function"],
  examples: [
    {
      type: "graph",
      params: {
        x: { min: 0, max: 6.3, label: "x" },
        y: { min: -1.2, max: 1.2, label: "sin x" },
        series: [{ id: "s", expr: "sin(x)", role: "accent" }],
      },
    },
  ],
});

export const axes = defineComponentSpec({
  type: "axes",
  category: "math",
  description: "Coordinate axes with ticks and labels (a graph without series).",
  params: z.strictObject({
    width: z.number().min(120).max(1400).default(560),
    height: z.number().min(80).max(760).default(340),
    x: AxisSpec.prefault({ min: 0, max: 10 }),
    y: AxisSpec.prefault({ min: 0, max: 10 }),
    grid: z.boolean().default(false),
  }),
  geometry: (p) => {
    const l = graphLayout(p);
    return { bbox: l.bbox, anchors: { origin: l.toLocal(l.x.min, l.y.min) } };
  },
  anchors: ["origin"],
  entrance: "draw",
  draw: true,
  semanticRoles: ["axes"],
  examples: [
    {
      type: "axes",
      params: { x: { min: 0, max: 5, label: "time (s)" }, y: { min: 0, max: 20, label: "speed (m/s)" } },
    },
  ],
});

export const grid = defineComponentSpec({
  type: "grid",
  category: "math",
  description: "A decorative square grid (graph paper) behind other content.",
  params: z.strictObject({
    width: z.number().min(40).max(1600).default(600),
    height: z.number().min(40).max(900).default(400),
    spacing: z.number().min(8).max(200).default(40),
  }),
  geometry: (p) => ({ bbox: centered(0, 0, pNum(p, "width", 600), pNum(p, "height", 400)) }),
  entrance: "fade",
  decorative: true,
  semanticRoles: ["background"],
  examples: [{ type: "grid", params: { width: 800, height: 500 }, layer: "background" }],
});
