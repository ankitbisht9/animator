/**
 * Diagrams laid out by the engine (ADR-061): the model writes structure, the engine places it. Nodes
 * never overlap, edges meet their nodes' outlines and pass between other nodes, parts are anchors and
 * terms, and one diagram changes into its next state with shared nodes moving.
 */
import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  compileScene,
  type DiagramLayout,
  diagramLayout,
  evaluate,
  evaluateObjects,
  insideNode,
  type LaidNode,
  sampleCurve,
  sceneEndState,
  type Vec2,
} from "../src";
import { compiled, lifted } from "./helpers";

type Raw = Record<string, unknown>;
const lanesParams: Raw = {
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
    { id: "m", label: "M", lane: "main" },
  ],
  edges: [
    { from: "a", to: "b" },
    { from: "b", to: "e" },
    { from: "b", to: "c" },
    { from: "c", to: "d" },
    { from: "e", to: "m" },
    { from: "d", to: "m" },
  ],
};
const flowParams: Raw = {
  layout: "layered",
  direction: "down",
  nodes: [
    { id: "start", label: "Order placed", shape: "pill" },
    { id: "check", label: "In stock?", shape: "diamond" },
    { id: "ship", label: "Ship it" },
    { id: "wait", label: "Back-order" },
    { id: "done", label: "Delivered", shape: "pill" },
  ],
  edges: [
    { from: "start", to: "check" },
    { from: "check", to: "ship", label: "yes" },
    { from: "check", to: "wait", label: "no" },
    { from: "ship", to: "done" },
    { from: "wait", to: "ship" },
    { from: "start", to: "done", id: "skip" },
  ],
};
const diagramObj = (id: string, params: Raw, extra: Raw = {}) => ({
  id,
  type: "diagram",
  params,
  place: { slot: "stage.center" },
  ...extra,
});

/** No two nodes' shapes overlap (checked on a grid of points of the smaller one). */
function overlapping(l: DiagramLayout): [string, string] | null {
  for (let i = 0; i < l.nodes.length; i++) {
    for (let j = i + 1; j < l.nodes.length; j++) {
      const a = l.nodes[i] as LaidNode;
      const b = l.nodes[j] as LaidNode;
      if (Math.abs(a.cx - b.cx) * 2 >= a.w + b.w || Math.abs(a.cy - b.cy) * 2 >= a.h + b.h) continue;
      for (let u = -0.45; u <= 0.45; u += 0.15)
        for (let v = -0.45; v <= 0.45; v += 0.15) {
          const p = { x: a.cx + u * a.w, y: a.cy + v * a.h };
          if (insideNode(a, p.x - a.cx, p.y - a.cy) && insideNode(b, p.x - b.cx, p.y - b.cy)) return [a.id, b.id];
        }
    }
  }
  return null;
}

/** Every edge starts and ends just outside its nodes, and passes outside every other node. */
function edgeProblems(l: DiagramLayout, through = true): string[] {
  const byId = new Map(l.nodes.map((n) => [n.id, n]));
  const out: string[] = [];
  const dist = (n: LaidNode, p: Vec2) => {
    // How far outside the outline p is (0 on it), measured along the ray from the centre.
    for (let pad = 0; pad < 80; pad += 0.5) if (!insideNode(n, p.x - n.cx, p.y - n.cy, pad)) return pad;
    return 80;
  };
  for (const e of l.edges) {
    const a = byId.get(e.from) as LaidNode;
    const b = byId.get(e.to) as LaidNode;
    if (!e.shaft.length) {
      out.push(`${e.from}->${e.to}: not drawn`);
      continue;
    }
    const first = (e.shaft[0] as { p0: Vec2 }).p0;
    const last = (e.shaft[e.shaft.length - 1] as { p3: Vec2 }).p3;
    if (insideNode(a, first.x - a.cx, first.y - a.cy) || dist(a, first) > 4 + l.headSize)
      out.push(`${e.from}->${e.to}: start off ${e.from}'s outline`);
    if (insideNode(b, last.x - b.cx, last.y - b.cy) || dist(b, last) > 4 + l.headSize)
      out.push(`${e.from}->${e.to}: end off ${e.to}'s outline`);
    for (const h of e.heads) {
      const n = Math.hypot(h.tip.x - b.cx, h.tip.y - b.cy) < Math.hypot(h.tip.x - a.cx, h.tip.y - a.cy) ? b : a;
      if (insideNode(n, h.tip.x - n.cx, h.tip.y - n.cy) || dist(n, h.tip) > 6)
        out.push(`${e.from}->${e.to}: head off the outline`);
    }
    if (!through) continue;
    for (const p of sampleCurve(e.shaft, 24)) {
      for (const n of l.nodes) {
        if (n.id === e.from || n.id === e.to) continue;
        if (insideNode(n, p.x - n.cx, p.y - n.cy, -2)) {
          out.push(`${e.from}->${e.to}: through ${n.id}`);
          break;
        }
      }
    }
  }
  return [...new Set(out)];
}

describe("diagram layouts", () => {
  it("lanes: each commit after its parents and the one before it on its lane, on its lane's row", () => {
    const l = diagramLayout(lanesParams);
    const at = new Map(l.nodes.map((n) => [n.id, n]));
    const x = (id: string) => (at.get(id) as LaidNode).cx;
    const y = (id: string) => (at.get(id) as LaidNode).cy;
    expect(x("a")).toBeLessThan(x("b"));
    expect(x("b")).toBeLessThan(x("e"));
    expect(x("c")).toBeCloseTo(x("e"), 5); // one step after b, like e
    expect(x("d")).toBeGreaterThan(x("c"));
    expect(x("m")).toBeGreaterThan(x("d")); // after both parents
    expect(y("a")).toBeCloseTo(y("m"), 5);
    expect(y("c")).toBeGreaterThan(y("a"));
    expect(l.lanes.map((t) => t.id)).toEqual(["main", "feature"]);
    expect(l.lanes.map((t) => t.color)).toEqual(["blue", "green"]);
    expect((at.get("c") as LaidNode).color).toBe("green");
    expect(overlapping(l)).toBeNull();
    expect(edgeProblems(l)).toEqual([]);
  });

  it("layered: a flowchart's nodes apart, its edges between them, with arrowheads on the outlines", () => {
    const l = diagramLayout(flowParams);
    expect(overlapping(l)).toBeNull();
    expect(edgeProblems(l)).toEqual([]);
    expect(l.edges.every((e) => e.heads.length === 1)).toBe(true);
    const by = new Map(l.nodes.map((n) => [n.id, n]));
    expect((by.get("start") as LaidNode).cy).toBeLessThan((by.get("check") as LaidNode).cy); // runs down
    expect(l.edges.filter((e) => e.label).length).toBe(2);
  });

  it("ring: nodes round a circle, edges along it", () => {
    const l = diagramLayout({
      layout: "ring",
      nodes: ["evaporation", "condensation", "rain", "collection"].map((id) => ({ id, label: id })),
      edges: [
        { from: "evaporation", to: "condensation" },
        { from: "condensation", to: "rain" },
        { from: "rain", to: "collection" },
        { from: "collection", to: "evaporation" },
      ],
    });
    expect(l.nodes.every((n) => n.shape === "pill")).toBe(true);
    const [top] = l.nodes;
    expect((top as LaidNode).cy).toBeLessThan(Math.min(...l.nodes.slice(1).map((n) => n.cy)));
    expect(overlapping(l)).toBeNull();
    expect(edgeProblems(l)).toEqual([]);
  });

  it("is centred on its origin and gives circles of one diagram one size", () => {
    const l = diagramLayout({
      layout: "lanes",
      nodes: [
        { id: "a", label: "A" },
        { id: "b", label: "Merge" },
      ],
      edges: [{ from: "a", to: "b" }],
    });
    expect(l.bbox.x + l.bbox.w / 2).toBeCloseTo(0, 6);
    expect(l.bbox.y + l.bbox.h / 2).toBeCloseTo(0, 6);
    expect((l.nodes[0] as LaidNode).w).toBe((l.nodes[1] as LaidNode).w);
  });

  it("never piles nodes up and always meets its nodes, whatever the structure", () => {
    const graph = fc.integer({ min: 2, max: 9 }).chain((n) =>
      fc.record({
        n: fc.constant(n),
        layout: fc.constantFrom("lanes", "layered", "ring"),
        direction: fc.constantFrom("right", "down", "left", "up"),
        edges: fc.array(fc.tuple(fc.integer({ min: 0, max: n - 1 }), fc.integer({ min: 0, max: n - 1 })), {
          maxLength: 14,
        }),
        lanes: fc.array(fc.integer({ min: 0, max: 2 }), { minLength: n, maxLength: n }),
        labels: fc.array(fc.constantFrom("A", "C'", "Merge", "In stock?", "Back-order queue", ""), {
          minLength: n,
          maxLength: n,
        }),
      }),
    );
    fc.assert(
      fc.property(graph, (g) => {
        const nodes = Array.from({ length: g.n }, (_, i) => ({
          id: `n${i}`,
          label: g.labels[i],
          ...(g.layout === "lanes" ? { lane: `l${g.lanes[i]}` } : {}),
        }));
        // Edges from earlier to later nodes (a history; flowcharts too), no duplicates.
        const seen = new Set<string>();
        const edges = g.edges
          .filter(([a, b]) => a !== b)
          .map(([a, b]) => (a < b ? [a, b] : [b, a]) as [number, number])
          .filter(([a, b]) => !seen.has(`${a}-${b}`) && seen.add(`${a}-${b}`))
          .map(([a, b]) => ({ from: `n${a}`, to: `n${b}` }));
        const l = diagramLayout({ layout: g.layout, direction: g.direction, nodes, edges });
        expect(overlapping(l)).toBeNull();
        // Ends always meet their nodes, and every edge passes between the other nodes.
        expect(edgeProblems(l)).toEqual([]);
      }),
      { numRuns: 300, seed: 61 },
    );
  });
});

describe("the diagram object", () => {
  it("fits its box, and its nodes are anchors on the canvas", () => {
    const cs = compiled({ objects: [diagramObj("history", { ...lanesParams, width: 900, height: 360 })] });
    const e = evaluateObjects(cs, 3).objects.get("history");
    expect(e).toBeDefined();
    if (!e) return;
    expect(e.layoutBBox.w).toBeLessThanOrEqual(900.5);
    expect(e.layoutBBox.h).toBeLessThanOrEqual(360.5);
    const c = e.anchors.c as Vec2;
    const top = e.anchors.c_top as Vec2;
    expect(c.x).toBeCloseTo(top.x, 5);
    expect(top.y).toBeLessThan(c.y);
    expect(e.anchors.main).toBeDefined(); // a lane tag
    // corners too, on the outline of the round commit: as far from its centre as its top is
    const corner = e.anchors.c_bottom_right as Vec2;
    expect(corner.x).toBeGreaterThan(c.x);
    expect(corner.y).toBeGreaterThan(c.y);
    expect(Math.hypot(corner.x - c.x, corner.y - c.y)).toBeCloseTo(c.y - top.y, 0);
  });

  it("never shrinks its text below readable: given too little room, it says how much it needs", () => {
    const r = compileScene(lifted({ objects: [diagramObj("history", { ...lanesParams, width: 300, height: 150 })] }));
    expect(r.ok).toBe(true);
    expect(r.issues.some((i) => i.code === "TEXT_TOO_SMALL")).toBe(false);
    const crowded = r.issues.find((i) => i.code === "DIAGRAM_CROWDED");
    expect(crowded?.severity).toBe("warning");
    expect(crowded?.message).toMatch(/needs about \d+ x \d+ px/);
    const e = evaluateObjects(r.compiled as NonNullable<typeof r.compiled>, 3).objects.get("history");
    expect(e?.layoutBBox.w ?? 0).toBeGreaterThan(300);
  });

  it("checks its structure: unknown nodes, repeated or reserved names, loops", () => {
    const codes = (params: Raw) =>
      compileScene(lifted({ objects: [diagramObj("g", params)] }))
        .issues.filter((i) => i.severity === "error")
        .map((i) => i.code);
    expect(codes({ nodes: [{ id: "a" }], edges: [{ from: "a", to: "zz" }] })).toContain("DIAGRAM_UNKNOWN_NODE");
    expect(codes({ nodes: [{ id: "a" }, { id: "a" }] })).toContain("DIAGRAM_DUPLICATE_ID");
    expect(codes({ nodes: [{ id: "a" }, { id: "a_top" }] })).toContain("DIAGRAM_DUPLICATE_ID");
    expect(codes({ nodes: [{ id: "a" }, { id: "a_top_left" }] })).toContain("DIAGRAM_DUPLICATE_ID");
    expect(codes({ nodes: [{ id: "center" }] })).toContain("DIAGRAM_RESERVED_ID");
    expect(codes({ nodes: [{ id: "a" }], edges: [{ from: "a", to: "a" }] })).toContain("DIAGRAM_SELF_LOOP");
    expect(codes(lanesParams)).toEqual([]);
  });

  it("builds node by node when created, and adds parts named by `write` in order", () => {
    const cs = compiled({
      objects: [diagramObj("history", lanesParams)],
      timeline: [
        { id: "c", action: "create", target: "history", start: { at: 0 }, duration: 2 },
        { id: "w", action: "write", target: "history", terms: ["m"], start: { at: 3 }, duration: 1 },
      ],
    });
    const o = (t: number) => evaluate(cs, t).objects.find((x) => x.id === "history");
    expect(o(1)?.draw ?? 1).toBeLessThan(1);
    expect(o(2.5)?.terms?.m).toBe(0);
    expect(o(4.2)?.terms?.m).toBe(1);
    const bad = compileScene(
      lifted({
        objects: [diagramObj("history", lanesParams)],
        timeline: [{ id: "w", action: "write", target: "history", terms: ["zz"], start: { at: 1 } }],
      }),
    ).issues.find((i) => i.code === "UNKNOWN_TERM");
    expect(bad?.message).toContain("Diagram 'history' has no part 'zz'");
  });

  it("highlights parts by name without moving the whole diagram", () => {
    const cs = compiled({
      objects: [diagramObj("history", lanesParams)],
      timeline: [{ id: "h", action: "highlight", target: "history", terms: ["m"], start: { at: 1 }, duration: 2 }],
    });
    const before = evaluate(cs, 0.5).objects.find((x) => x.id === "history");
    const during = evaluate(cs, 2).objects.find((x) => x.id === "history");
    expect(during?.marks?.map((m) => m.terms)).toEqual([["m"]]);
    expect(during?.emph).toBeUndefined();
    expect(during?.matrix).toEqual(before?.matrix);
  });

  it("marks several parts at once, each in its own color; the whole diagram's emphasis stays one at a time", () => {
    const mark = (id: string, terms: string[], at: number, color: string) => ({
      id,
      action: "highlight",
      target: "history",
      terms,
      color,
      start: { at },
      duration: 3,
    });
    const r = compileScene(
      lifted({
        objects: [diagramObj("history", lanesParams)],
        timeline: [mark("path_a", ["a"], 1, "highlight"), mark("path_b", ["b", "e"], 2, "accent")],
      }),
    );
    expect(r.issues.filter((i) => i.code === "TRACK_CONFLICT")).toEqual([]);
    const marks = evaluate(r.compiled as never, 2.5).objects.find((x) => x.id === "history")?.marks ?? [];
    expect(marks.map((m) => m.terms)).toEqual([["a"], ["b", "e"]]);
    expect(new Set(marks.map((m) => m.color)).size).toBe(2);
    const whole = (id: string, at: number) => ({
      id,
      action: "highlight",
      target: "history",
      start: { at },
      duration: 3,
    });
    const both = compileScene(
      lifted({ objects: [diagramObj("history", lanesParams)], timeline: [whole("w1", 1), whole("w2", 2)] }),
    );
    expect(both.issues.some((i) => i.code === "TRACK_CONFLICT")).toBe(true);
  });

  it("an arrow from another object aimed at a node stops on that node's outline", () => {
    const cs = compiled({
      objects: [
        diagramObj("history", lanesParams),
        { id: "tag", type: "label", params: { text: "HEAD" }, place: { x: 1300, y: 160 } },
        { id: "point", type: "arrow", params: { from: "tag.left", to: "history.m" } },
      ],
    });
    const ev = evaluateObjects(cs, 3).objects;
    const end = ev.get("point")?.anchors.end as Vec2;
    const m = ev.get("history")?.anchors.m as Vec2;
    const top = ev.get("history")?.anchors.m_top as Vec2;
    const r = Math.abs(m.y - top.y);
    expect(Math.hypot(end.x - m.x, end.y - m.y)).toBeCloseTo(r, 0);
  });

  it("changes into its next state: shared nodes move from their old places, the old diagram goes", () => {
    const after: Raw = {
      ...lanesParams,
      nodes: [
        { id: "a", label: "A", lane: "main" },
        { id: "b", label: "B", lane: "main" },
        { id: "e", label: "E", lane: "main" },
        { id: "c", label: "C'", lane: "feature" },
        { id: "d", label: "D'", lane: "feature" },
      ],
      edges: [
        { from: "a", to: "b" },
        { from: "b", to: "e" },
        { from: "e", to: "c" },
        { from: "c", to: "d" },
      ],
    };
    const cs = compiled({
      duration: 8,
      objects: [diagramObj("before", lanesParams), diagramObj("after", after)],
      timeline: [
        { id: "c0", action: "create", target: "before", start: { at: 0 } },
        { id: "t", action: "transform", target: "before", into: "after", start: { at: 3 }, duration: 2 },
      ],
    });
    const at = (t: number) => evaluate(cs, t).objects;
    expect(at(2.9).some((x) => x.id === "after")).toBe(false);
    const mid = at(3.05).find((x) => x.id === "after");
    expect(mid).toBeDefined();
    expect(mid?.params.__morph as number).toBeLessThan(0.1);
    expect(at(5.5).find((x) => x.id === "after")?.params.__morph).toBe(1);
    expect(at(5.5).some((x) => x.id === "before")).toBe(false);
    const prep = cs.objects.find((x) => x.id === "after")?.prep as { morphFrom?: { nodes: Record<string, unknown> } };
    expect(Object.keys(prep.morphFrom?.nodes ?? {}).sort()).toEqual(["a", "b", "c", "d", "e"]);
    // The next scene starts from the new state, nothing of the change carried.
    const end = sceneEndState(cs, null).objects.after?.params ?? {};
    expect(end.__morph).toBeUndefined();
    expect(end.__handoff).toBeUndefined();
  });

  it("compiles identically twice (determinism)", () => {
    const spec = {
      objects: [diagramObj("history", lanesParams), diagramObj("flow", flowParams, { place: { x: 1200, y: 600 } })],
    };
    expect(JSON.stringify(compiled(spec))).toBe(JSON.stringify(compiled(spec)));
  });
});

describe("layout checks see a diagram's nodes, not its empty space", () => {
  const scene = (extra: unknown[]) => ({
    objects: [
      diagramObj("history", { ...lanesParams, width: 900, height: 360 }, { place: { x: 800, y: 450 } }),
      ...extra,
    ],
  });
  const codes = (extra: unknown[]) => compileScene(lifted(scene(extra))).issues.map((i) => `${i.code}:${i.message}`);
  const node = (id: string) => {
    const e = evaluateObjects(compiled(scene([])), 1).objects.get("history");
    return e?.anchors[id] as Vec2;
  };

  it("a label in the gap between the lanes is not on the diagram; one on a node is", () => {
    const a = node("a");
    const c = node("c");
    const gap = { id: "tip", type: "label", params: { text: "HEAD" }, place: { x: a.x, y: (a.y + c.y) / 2 } };
    expect(codes([gap]).filter((x) => x.startsWith("LABEL_OCCLUDES"))).toEqual([]);
    const on = { id: "tip", type: "label", params: { text: "HEAD" }, place: { x: a.x, y: a.y } };
    expect(codes([on]).some((x) => x.startsWith("LABEL_OCCLUDES:'tip'"))).toBe(true);
  });

  it("a box in the gap between the lanes is beside the diagram; one on a node covers it", () => {
    const a = node("a");
    const c = node("c");
    const box = (y: number) => ({
      id: "panel",
      type: "rectangle",
      params: { width: 60, height: 30 },
      place: { x: a.x - 80, y },
    });
    expect(codes([box((a.y + c.y) / 2)]).filter((x) => x.startsWith("OBJECT_OVERLAP"))).toEqual([]);
    const on = { ...box(a.y), place: { x: a.x, y: a.y } };
    expect(codes([on]).some((x) => x.startsWith("OBJECT_OVERLAP:'panel' covers part of 'history'"))).toBe(true);
  });

  it("its words count at the frame's edges: cut by the frame is an error, under the caption a warning", () => {
    const at = (place: Raw) =>
      compileScene(
        lifted({
          layout: "stage_with_caption",
          objects: [diagramObj("history", { ...lanesParams, width: 900, height: 360 }, { place })],
        }),
      ).issues;
    const w = evaluateObjects(compiled(scene([])), 1).objects.get("history")?.layoutBBox.w ?? 0;
    // a twentieth of it over the left edge: a lane tag cut in half
    const cut = at({ x: w / 2 - w / 20, y: 450 }).find((i) => i.code === "OUT_OF_BOUNDS");
    expect(cut?.severity).toBe("error");
    expect(cut?.fix?.dx).toBeGreaterThan(0);
    const low = at({ x: 800, y: 760 }).find((i) => i.code === "OUT_OF_STAGE");
    expect(low?.severity).toBe("warning");
    expect(at({ x: 800, y: 450 }).filter((i) => ["OUT_OF_BOUNDS", "OUT_OF_STAGE"].includes(i.code))).toEqual([]);
  });

  it("a label pointing at a node leads to its outline, not over its letter; at a plain shape, as written", () => {
    const tip = (pointTo: string) => ({
      id: "tip",
      type: "label",
      params: { text: "HEAD", pointTo },
      place: { x: 300, y: 120 },
    });
    const dot = { id: "dot", type: "circle", params: { radius: 30 }, place: { x: 1300, y: 150 } };
    const objects = (pointTo: string) => [
      diagramObj("history", { ...lanesParams, width: 900, height: 360 }, { place: { x: 800, y: 450 } }),
      dot,
      tip(pointTo),
    ];
    const at = (pointTo: string) => {
      const ev = evaluateObjects(compiled({ objects: objects(pointTo) }), 1).objects;
      return { end: ev.get("tip")?.params.pointTo as Vec2, ev };
    };
    const { end, ev } = at("history.a");
    const a = ev.get("history")?.anchors.a as Vec2;
    const r = Math.hypot(end.x - a.x, end.y - a.y);
    expect(r).toBeGreaterThan(15); // on the outline of the commit circle, short of its letter
    expect(r).toBeLessThan(40);
    expect(end.y).toBeLessThan(a.y); // on the side the label is
    const plain = at("dot.center");
    expect(plain.end).toEqual(plain.ev.get("dot")?.anchors.center);
  });

  it("an arrow across the empty space is fine; across a node it runs through the diagram", () => {
    const a = node("a");
    const c = node("c");
    const y = (a.y + c.y) / 2;
    const clear = { id: "pass", type: "arrow", params: { from: { x: 200, y }, to: { x: 1400, y } } };
    expect(codes([clear]).filter((x) => x.startsWith("CONNECTOR_CROSSES_OBJECT"))).toEqual([]);
    const cross = { id: "pass", type: "arrow", params: { from: { x: 200, y: a.y }, to: { x: 1400, y: a.y } } };
    expect(codes([cross]).some((x) => x.startsWith("CONNECTOR_CROSSES_OBJECT:'pass' runs through 'history'"))).toBe(
      true,
    );
  });
});
