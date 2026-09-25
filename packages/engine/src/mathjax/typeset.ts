/**
 * TeX -> flattened glyph paths via MathJax (SVG output, fontCache "none").
 *
 * The MathJax SVG tree is walked (never serialized to a string for the renderer),
 * transforms are composed and baked into path data, and geometry is grouped by
 * term: each equation part is wrapped in \cssId{t-<id>}{...} by the engine and
 * comes back as its own path group. Units are MathJax units (1000 per em), y-down,
 * baseline at y = 0.
 */
import { liteAdaptor } from "mathjax-full/js/adaptors/liteAdaptor.js";
import { RegisterHTMLHandler } from "mathjax-full/js/handlers/html.js";
import { TeX } from "mathjax-full/js/input/tex.js";
import "mathjax-full/js/input/tex/ams/AmsConfiguration.js";
import "mathjax-full/js/input/tex/base/BaseConfiguration.js";
import "mathjax-full/js/input/tex/html/HtmlConfiguration.js";
import { mathjax } from "mathjax-full/js/mathjax.js";
import { SVG } from "mathjax-full/js/output/svg.js";
import { parsePath, pathBounds, type Seg, serializePath, transformPath } from "../geom/path";
import { IDENTITY, type Mat, multiply, rotation, scaling, translation } from "../math/matrix";
import type { Rect } from "../math/rect";

export interface TexPart {
  /** Term id (without the `t-` prefix), or null for geometry outside any term. */
  id: string | null;
  d: string;
  bbox: Rect;
}

export interface TexLayout {
  width: number;
  ascent: number;
  descent: number;
  parts: TexPart[];
}

export class TexError extends Error {}

type LiteNode = unknown;

/** The subset of MathJax's lite DOM adaptor we use, typed loosely over opaque nodes. */
interface Adaptor {
  kind(n: LiteNode): string;
  childNodes(n: LiteNode): LiteNode[];
  getAttribute(n: LiteNode, name: string): unknown;
}

interface Engine {
  adaptor: Adaptor;
  convert: (tex: string) => LiteNode;
}

let engine: Engine | null = null;

function getEngine(): Engine {
  if (engine) return engine;
  const lite = liteAdaptor();
  RegisterHTMLHandler(lite);
  const adaptor = lite as unknown as Adaptor;
  const tex = new TeX({
    packages: ["base", "ams", "html"],
    formatError: (_jax: unknown, err: { message: string }) => {
      throw new TexError(err.message);
    },
  });
  const svg = new SVG({ fontCache: "none" });
  const doc = mathjax.document("", { InputJax: tex, OutputJax: svg });
  engine = {
    adaptor,
    convert: (src: string) => doc.convert(src, { display: true, em: 16, ex: 8, containerWidth: 100000 }) as LiteNode,
  };
  return engine;
}

const TRANSFORM_PART = /(translate|scale|rotate|matrix)\(([^)]*)\)/g;
const TRANSFORM_FULL = /^\s*((translate|scale|rotate|matrix)\([-\d.,\se]*\)\s*)*$/;

function parseTransform(s: string | null | undefined): Mat {
  if (!s) return IDENTITY;
  if (!TRANSFORM_FULL.test(s)) throw new TexError(`unexpected transform '${s}'`);
  let m: Mat = IDENTITY;
  for (const match of s.matchAll(TRANSFORM_PART)) {
    const args = (match[2] ?? "")
      .split(/[\s,]+/)
      .filter(Boolean)
      .map(Number);
    let t: Mat;
    switch (match[1]) {
      case "translate":
        t = translation(args[0] ?? 0, args[1] ?? 0);
        break;
      case "scale":
        t = scaling(args[0] ?? 1, args[1] ?? args[0] ?? 1);
        break;
      case "rotate":
        t = rotation(args[0] ?? 0);
        break;
      default:
        t = [args[0] ?? 1, args[1] ?? 0, args[2] ?? 0, args[3] ?? 1, args[4] ?? 0, args[5] ?? 0];
    }
    m = multiply(m, t);
  }
  return m;
}

const num = (v: unknown, fallback = 0): number => {
  const n = Number.parseFloat(String(v ?? ""));
  return Number.isFinite(n) ? n : fallback;
};

/** Typeset TeX (already allowlisted by the caller) into grouped, flattened geometry. */
export function typesetTex(tex: string): TexLayout {
  const { adaptor, convert } = getEngine();
  const container = convert(tex);
  const svg = adaptor.childNodes(container).find((n) => adaptor.kind(n) === "svg");
  if (!svg) throw new TexError("MathJax produced no SVG");
  const vb = String(adaptor.getAttribute(svg, "viewBox") ?? "")
    .split(/\s+/)
    .map(Number);
  if (vb.length !== 4 || vb.some((v) => !Number.isFinite(v))) throw new TexError("bad viewBox");
  const [, minY, width, height] = vb as [number, number, number, number];

  const groups = new Map<string | null, Seg[]>();
  const push = (id: string | null, segs: Seg[]) => {
    const g = groups.get(id);
    if (g) g.push(...segs);
    else groups.set(id, [...segs]);
  };

  const walk = (node: LiteNode, m: Mat, term: string | null) => {
    const kind = adaptor.kind(node);
    if (kind === "#text" || kind === "#comment") return;
    if (kind === "text") throw new TexError("unsupported character in math (no glyph available)");
    const own = parseTransform(adaptor.getAttribute(node, "transform") as string | undefined);
    const mm = multiply(m, own);
    const id = adaptor.getAttribute(node, "id") as string | undefined;
    const t = id?.startsWith("t-") ? id.slice(2) : term;
    if (kind === "path") {
      const d = adaptor.getAttribute(node, "d") as string | undefined;
      if (d) push(t, transformPath(parsePath(d), mm));
      return;
    }
    if (kind === "rect") {
      const x = num(adaptor.getAttribute(node, "x"));
      const y = num(adaptor.getAttribute(node, "y"));
      const w = num(adaptor.getAttribute(node, "width"));
      const h = num(adaptor.getAttribute(node, "height"));
      push(t, transformPath(parsePath(`M${x} ${y}H${x + w}V${y + h}H${x}Z`), mm));
      return;
    }
    if (kind === "svg" || kind === "g" || kind === "a") {
      for (const child of adaptor.childNodes(node)) walk(child, mm, t);
      return;
    }
    // defs/title/etc. carry no geometry we render.
  };

  for (const child of adaptor.childNodes(svg)) walk(child, IDENTITY, null);

  const parts: TexPart[] = [];
  for (const [id, segs] of groups) {
    if (segs.length === 0) continue;
    parts.push({ id, d: serializePath(segs), bbox: pathBounds(segs) });
  }
  return { width, ascent: -minY, descent: height + minY, parts };
}
