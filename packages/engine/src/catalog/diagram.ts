/**
 * A diagram of nodes joined by edges, laid out by the engine (ADR-061). The model writes only the
 * structure; `layout/diagram.ts` decides every position, so nodes never pile up and edges always meet
 * their nodes. Its parts are named: each node is an anchor (`history.c2`, with `c2_top` and the other
 * sides) and every node, named edge and lane is a term that `write` reveals and `highlight` marks.
 */
import { z } from "zod";
import { ColorRef } from "../dsl/primitives";
import {
  DIAGRAM_DIRECTIONS,
  DIAGRAM_LAYOUTS,
  DIAGRAM_SIZES,
  type DiagramLayout,
  diagramFit,
  diagramKey,
  diagramLayout,
  EDGE_HEADS,
  NODE_SHAPES,
  outlinePoint,
  smallestText,
} from "../layout/diagram";
import { BOX_ANCHORS, type Rect } from "../math/rect";
import type { Vec2 } from "../math/vec";
import { layoutText } from "../text/layout";
import { defineComponentSpec } from "./registry";
import { type Params, pNum } from "./spec";

/** Part ids: short snake_case (they become anchors, with `_top` and the like after them). */
const PartId = z
  .string()
  .regex(/^[a-z][a-z0-9_]{0,24}$/, "part ids are lowercase snake_case, start with a letter, at most 25 characters")
  .describe("Lowercase snake_case, short: a, c1, main (never capitals or a prime: C' is c_prime, labelled C')");

export const DiagramNode = z
  .strictObject({
    id: PartId.describe(
      "The node's name (lowercase snake_case, short: a, c1, c_prime; its text goes in label): an anchor of the diagram ('history.c2') and a term to reveal or highlight",
    ),
    label: z.string().max(40).default("").describe("Text inside the node: a letter or a few words"),
    shape: z
      .enum(NODE_SHAPES)
      .optional()
      .describe(
        "circle (commits, states: short labels), box (steps), pill, diamond (a decision); default circle for lanes, box for layered, pill for ring",
      ),
    lane: PartId.optional().describe("lanes layout: the lane (branch) this node is on"),
    step: z
      .number()
      .int()
      .min(0)
      .max(60)
      .optional()
      .describe(
        "lanes layout: at least this many steps along (default: one after its parents and the node before it on its lane)",
      ),
    note: z.string().max(60).optional().describe("Small text under the node (a commit message, a value)"),
    color: ColorRef.optional().describe("Default: its lane's color (lanes), else the diagram's"),
    ghost: z.boolean().default(false).describe("Faded and dashed: a node that was replaced, or one still to come"),
  })
  .meta({ id: "DiagramNode" });

export const DiagramEdge = z
  .strictObject({
    id: PartId.optional().describe("Only to reveal or highlight this edge by name"),
    from: PartId.describe("A node id"),
    to: PartId.describe("A node id"),
    label: z.string().max(24).optional().describe("Short text beside the edge (a weight, a condition)"),
    head: z
      .enum(EDGE_HEADS)
      .optional()
      .describe("Arrowheads: end (default for layered and ring), none (default for lanes), both, start"),
    dashed: z.boolean().default(false),
    color: ColorRef.optional(),
  })
  .meta({ id: "DiagramEdge" });

export const DiagramLane = z
  .strictObject({
    id: PartId,
    label: z.string().max(24).optional().describe("The lane's name tag (default its id)"),
    color: ColorRef.optional().describe("Its nodes' color (default: blue, green, orange, purple, ... in turn)"),
  })
  .meta({ id: "DiagramLane" });

const SIDES = ["top", "bottom", "left", "right"] as const;
const SIDE_DIR: Record<(typeof SIDES)[number], Vec2> = {
  top: { x: 0, y: -1 },
  bottom: { x: 0, y: 1 },
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
};
/** A node's corners: on its outline, toward the corners of its box (a box node's own corners). */
const CORNERS = { top_left: [-1, -1], top_right: [1, -1], bottom_left: [-1, 1], bottom_right: [1, 1] } as const;
const CORNER_NAMES = Object.keys(CORNERS) as (keyof typeof CORNERS)[];
const RESERVED = new Set<string>(BOX_ANCHORS);

type Raw = Record<string, unknown>;
const listOf = (v: unknown): Raw[] => (Array.isArray(v) ? (v as Raw[]) : []);
const idOf = (v: Raw): string => (typeof v.id === "string" ? v.id : "");

/** The diagram's laid-out form: the prepared one when compiled, else computed (cached). */
export function layoutOf(p: Params, prep?: unknown): DiagramLayout {
  const l = prep as DiagramLayout | undefined;
  return l && Array.isArray(l.nodes) && l.bbox ? l : diagramLayout(p);
}

/** Scale from layout units to the object's frame (fit to `width` x `height`). */
export function diagramScale(p: Params, l: DiagramLayout): number {
  return diagramFit(l, pNum(p, "width", 1200), pNum(p, "height", 560));
}

/** Names of everything a `write` or `highlight` can name: nodes, named edges, lanes. */
export function diagramTerms(p: Params): string[] {
  return [
    ...listOf(p.nodes).map(idOf),
    ...listOf(p.edges)
      .map(idOf)
      .filter((x) => x),
    ...listOf(p.lanes).map(idOf),
  ].filter((x) => x);
}

/** A node's outline in the object's frame (for arrows from other objects aimed at a node). */
export function diagramNodeOutline(
  p: Params,
  prep: unknown,
  id: string,
): { kind: "ellipse"; cx: number; cy: number; rx: number; ry: number } | { kind: "polygon"; pts: Vec2[] } | null {
  const l = layoutOf(p, prep);
  const n = l.nodes.find((x) => x.id === id);
  if (!n) return null;
  const k = diagramScale(p, l);
  const cx = n.cx * k;
  const cy = n.cy * k;
  const hw = (n.w / 2) * k;
  const hh = (n.h / 2) * k;
  if (n.shape === "circle") return { kind: "ellipse", cx, cy, rx: hw, ry: hh };
  if (n.shape === "diamond")
    return {
      kind: "polygon",
      pts: [
        { x: cx, y: cy - hh },
        { x: cx + hw, y: cy },
        { x: cx, y: cy + hh },
        { x: cx - hw, y: cy },
      ],
    };
  return {
    kind: "polygon",
    pts: [
      { x: cx - hw, y: cy - hh },
      { x: cx + hw, y: cy - hh },
      { x: cx + hw, y: cy + hh },
      { x: cx - hw, y: cy + hh },
    ],
  };
}

/** Boxes of what is drawn (nodes and their notes, tags, edge labels) in the object's frame: what
 * the layout checks treat as solid, rather than the whole bounding box with its empty space. */
export function diagramSolidParts(p: Params, prep: unknown): Rect[] {
  const l = layoutOf(p, prep);
  const k = diagramScale(p, l);
  const box = (cx: number, cy: number, w: number, h: number): Rect => ({
    x: (cx - w / 2) * k,
    y: (cy - h / 2) * k,
    w: w * k,
    h: h * k,
  });
  const out: Rect[] = [];
  for (const n of l.nodes) {
    out.push(box(n.cx, n.cy, n.w, n.h));
    if (n.note) out.push(box(n.note.cx, n.note.cy, n.note.text.width, n.note.text.height));
  }
  for (const e of l.edges) if (e.label) out.push(box(e.label.cx, e.label.cy, e.label.w, e.label.h));
  for (const t of l.lanes) out.push(box(t.tag.cx, t.tag.cy, t.tag.w, t.tag.h));
  return out;
}

export const diagram = defineComponentSpec({
  type: "diagram",
  category: "annotation",
  description:
    "A diagram of nodes joined by edges that the engine lays out (never place its nodes yourself): `lanes` for histories on parallel lanes (git branches and commits), `layered` for flowcharts, processes, trees and networks, `ring` for cycles. Write only the structure: nodes (`id`, a short `label` inside, `lane`, a `note` under it), edges (`from`, `to`, an optional `label`) and lanes. Each node is an anchor of the diagram (`history.c2`, and `c2_top`, `c2_bottom`, `c2_left`, `c2_right` and the corners `c2_top_left` and so on, on its outline) for labels, callouts and arrows from other objects. `create` builds it node by node; `write` with `terms` (node, edge or lane ids) adds parts in that order, an edge appearing with the later of its nodes; `highlight` with `terms` marks parts; `transform` with `into` another diagram changes it into its next state, nodes with the same id moving to their new places.",
  params: z.strictObject({
    layout: z.enum(DIAGRAM_LAYOUTS).default("layered"),
    direction: z
      .enum(DIAGRAM_DIRECTIONS)
      .default("right")
      .describe("Which way it grows: lanes and layered diagrams run this way; ring: right = clockwise"),
    nodes: z.array(DiagramNode).min(1).max(40),
    edges: z.array(DiagramEdge).max(80).default([]),
    lanes: z.array(DiagramLane).max(8).default([]).describe("lanes layout: the lanes in order, top to bottom"),
    size: z.enum(DIAGRAM_SIZES).default("medium").describe("Node and text size"),
    width: z.number().min(100).max(1600).default(1200).describe("Most width it takes; a larger diagram is scaled down"),
    height: z.number().min(60).max(900).default(560).describe("Most height it takes"),
  }),
  geometry: (p, ctx) => {
    const l = layoutOf(p, ctx.prep);
    const k = diagramScale(p, l);
    const anchors: Record<string, Vec2> = {};
    for (const n of l.nodes) {
      anchors[n.id] = { x: n.cx * k, y: n.cy * k };
      for (const side of SIDES) {
        const d = SIDE_DIR[side];
        const o = outlinePoint(n, d.x, d.y);
        anchors[`${n.id}_${side}`] = { x: (n.cx + o.x) * k, y: (n.cy + o.y) * k };
      }
      for (const corner of CORNER_NAMES) {
        const [sx, sy] = CORNERS[corner];
        const o = outlinePoint(n, (sx * n.w) / 2, (sy * n.h) / 2);
        anchors[`${n.id}_${corner}`] = { x: (n.cx + o.x) * k, y: (n.cy + o.y) * k };
      }
    }
    for (const e of l.edges) if (e.id) anchors[e.id] = { x: e.mid.x * k, y: e.mid.y * k };
    for (const t of l.lanes) anchors[t.id] = { x: t.tag.cx * k, y: t.tag.cy * k };
    const b = l.bbox;
    return { bbox: { x: b.x * k, y: b.y * k, w: b.w * k, h: b.h * k }, anchors };
  },
  dynamicAnchors: (p) => {
    const nodes = listOf(p.nodes)
      .map(idOf)
      .filter((x) => x);
    return [
      ...nodes.flatMap((id) => [id, ...[...SIDES, ...CORNER_NAMES].map((s) => `${id}_${s}`)]),
      ...listOf(p.edges)
        .map(idOf)
        .filter((x) => x),
      ...listOf(p.lanes).map(idOf),
    ];
  },
  termIds: diagramTerms,
  prepare: (p) => diagramLayout(p),
  prepareKey: (p) => diagramKey(p),
  solidParts: diagramSolidParts,
  partOutline: diagramNodeOutline,
  validate: (p) => {
    const problems: { code: string; message: string; param: string; suggestion?: string; severity?: "warning" }[] = [];
    const nodes = listOf(p.nodes);
    const nodeIds = nodes.map(idOf);
    const named = new Map<string, string>();
    const claim = (name: string, where: string) => {
      if (!name) return;
      if (RESERVED.has(name)) {
        problems.push({
          code: "DIAGRAM_RESERVED_ID",
          param: where,
          message: `'${name}' is the name of a box anchor; a diagram part needs another name.`,
          suggestion: `Call it '${name}_node' or another short name.`,
        });
      } else if (named.has(name)) {
        problems.push({
          code: "DIAGRAM_DUPLICATE_ID",
          param: where,
          message: `Two parts of the diagram are called '${name}' (nodes, edges and lanes share one set of names, with '<node>_top' and the other sides and corners).`,
          suggestion: "Give each node, named edge and lane its own id.",
        });
      } else named.set(name, where);
    };
    nodes.forEach((n, i) => {
      const id = idOf(n);
      claim(id, `nodes/${i}/id`);
      for (const side of [...SIDES, ...CORNER_NAMES]) if (id) claim(`${id}_${side}`, `nodes/${i}/id`);
    });
    listOf(p.edges).forEach((e, i) => {
      if (idOf(e)) claim(idOf(e), `edges/${i}/id`);
      for (const end of ["from", "to"] as const) {
        const v = typeof e[end] === "string" ? (e[end] as string) : "";
        if (!nodeIds.includes(v))
          problems.push({
            code: "DIAGRAM_UNKNOWN_NODE",
            param: `edges/${i}/${end}`,
            message: `The diagram has no node '${v}'.`,
            suggestion: `Nodes: ${nodeIds.join(", ")}.`,
          });
      }
      if (e.from === e.to)
        problems.push({
          code: "DIAGRAM_SELF_LOOP",
          param: `edges/${i}`,
          message: "An edge cannot join a node to itself.",
          suggestion: "Drop it, or show the loop with a note on the node.",
        });
    });
    listOf(p.lanes).forEach((l, i) => claim(idOf(l), `lanes/${i}/id`));
    // Text the font cannot draw (shown as '?'): a warning, like any other text.
    const texts: [string, string][] = [
      ...nodes.flatMap((n, i): [string, string][] => [
        [`nodes/${i}/label`, typeof n.label === "string" ? n.label : ""],
        [`nodes/${i}/note`, typeof n.note === "string" ? n.note : ""],
      ]),
      ...listOf(p.edges).map((e, i): [string, string] => [
        `edges/${i}/label`,
        typeof e.label === "string" ? e.label : "",
      ]),
      ...listOf(p.lanes).map((l, i): [string, string] => [
        `lanes/${i}/label`,
        typeof l.label === "string" ? l.label : "",
      ]),
    ];
    for (const [where, text] of texts) {
      const missing = text ? layoutText(text, { size: 24 }).missing : [];
      if (missing.length)
        problems.push({
          code: "UNSUPPORTED_GLYPH",
          param: where,
          message: `The font cannot draw: ${missing.map((c) => JSON.stringify(c)).join(" ")}.`,
          suggestion: "Rephrase with plain characters (a prime is ' ).",
          severity: "warning",
        });
    }
    return problems;
  },
  minTextSize: (p) => {
    const l = layoutOf(p);
    return l.nodes.length ? smallestText(l) * diagramScale(p, l) : null;
  },
  entrance: "draw",
  draw: true,
  write: true,
  defaultStyle: { fill: "blue", stroke: "muted", strokeWidth: 4 },
  semanticRoles: ["diagram", "graph", "flowchart", "tree", "history"],
  examples: [
    {
      type: "diagram",
      params: {
        layout: "lanes",
        lanes: [
          { id: "main", label: "main" },
          { id: "feature", label: "feature" },
        ],
        nodes: [
          { id: "a", label: "A", lane: "main" },
          { id: "b", label: "B", lane: "main" },
          { id: "e", label: "E", lane: "main" },
          { id: "c", label: "C", lane: "feature" },
          { id: "d", label: "D", lane: "feature" },
        ],
        edges: [
          { from: "a", to: "b" },
          { from: "b", to: "e" },
          { from: "b", to: "c" },
          { from: "c", to: "d" },
        ],
      },
      place: { slot: "stage.center" },
    },
    {
      type: "diagram",
      params: {
        layout: "layered",
        direction: "down",
        nodes: [
          { id: "start", label: "Order placed", shape: "pill" },
          { id: "check", label: "In stock?", shape: "diamond" },
          { id: "ship", label: "Ship it" },
          { id: "wait", label: "Back-order" },
        ],
        edges: [
          { from: "start", to: "check" },
          { from: "check", to: "ship", label: "yes" },
          { from: "check", to: "wait", label: "no" },
        ],
      },
      place: { slot: "stage.center" },
    },
  ],
});
