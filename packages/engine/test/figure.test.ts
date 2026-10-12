import { describe, expect, it } from "vitest";
import { canonicalJsonSchema, catalogInfo, evaluate, llmJsonSchema } from "../src";
import { compiled, errorCodes } from "./helpers";

// A 200 x 100 drawing: a body (left half) and a lid (top right), plus a named point.
const ART = {
  w: 200,
  h: 100,
  shapes: [
    { d: "M0 0 L100 0 L100 100 L0 100 Z", fill: "red", stroke: "red.dark", strokeWidth: 3, part: "body" },
    { d: "M120 0 L200 0 L200 40 L120 40 Z", fill: "#3366cc", part: "lid" },
  ],
  anchors: [{ name: "tip", x: 200, y: 100 }],
};

const scene = (art: unknown = ART, extra: Record<string, unknown> = {}) => ({
  objects: [
    { id: "thing", type: "figure", params: { art, width: 400 }, place: { x: 800, y: 400 } },
    {
      id: "thing_label",
      type: "label",
      params: { text: "lid", pointTo: "thing.lid" },
      place: { relativeTo: "thing", side: "right", gap: 40 },
    },
  ],
  timeline: [
    { id: "a1", action: "create", target: "thing", start: { at: 0 } },
    { id: "a2", action: "create", target: "thing_label", start: { at: 0.5 } },
  ],
  ...extra,
});

describe("figure (saved drawings, ADR-055)", () => {
  it("is the pipeline's: absent from the model's schema and catalog, present in the canonical schema", () => {
    const llm = llmJsonSchema() as { $defs: Record<string, { anyOf?: { $ref?: string }[] }> };
    expect(llm.$defs.Object_figure).toBeUndefined();
    expect(JSON.stringify(llm.$defs.Object)).not.toContain("Object_figure");
    expect(llm.$defs.Object_icon).toBeDefined();
    expect(catalogInfo().components.map((c) => c.type)).not.toContain("figure");
    const canonical = canonicalJsonSchema() as { $defs: Record<string, unknown> };
    expect(canonical.$defs.Object_figure).toBeDefined();
  });

  it("keeps its proportions and puts part anchors on the parts", () => {
    const cs = compiled(scene());
    const f = evaluate(cs, 2);
    const thing = f.objects.find((o) => o.id === "thing");
    expect(thing).toBeDefined();
    // 200 x 100 art at width 400: 400 x 200, centred on (800, 400).
    expect(thing?.bbox).toEqual({ x: 600, y: 300, w: 400, h: 200 });
    // The label's leader ends on the lid: art (160, 20) -> canvas (800 + (160 - 100) * 2, 400 + (20 - 50) * 2).
    const label = f.objects.find((o) => o.id === "thing_label");
    expect(label?.params.pointTo).toEqual({ x: 920, y: 340 });
  });

  it("names its parts and points as anchors, and only those", () => {
    expect(errorCodes(scene())).toEqual([]);
    const tip = scene();
    (tip.objects[1] as { params: { pointTo: string } }).params.pointTo = "thing.tip";
    expect(errorCodes(tip)).toEqual([]);
    const wrong = scene();
    (wrong.objects[1] as { params: { pointTo: string } }).params.pointTo = "thing.handle";
    expect(errorCodes(wrong)).toContain("UNKNOWN_ANCHOR");
  });

  it("brings a part into focus, and only a part it has", () => {
    const focused = scene();
    (focused.objects[0] as { params: Record<string, unknown> }).params.focus = "lid";
    expect(errorCodes(focused)).toEqual([]);
    (focused.objects[0] as { params: Record<string, unknown> }).params.focus = "handle";
    expect(errorCodes(focused)).toContain("FIGURE_INVALID");
    // A transform changes the focus as the narration moves on.
    const moving = scene(ART, {
      timeline: [
        { id: "a1", action: "create", target: "thing", start: { at: 0 } },
        { id: "a2", action: "create", target: "thing_label", start: { at: 0.5 } },
        { id: "a3", action: "transform", target: "thing", changes: [{ param: "focus", to: "lid" }], start: { at: 1 } },
      ],
    });
    const cs = compiled(moving);
    expect(evaluate(cs, 0.5).objects.find((o) => o.id === "thing")?.params.focus).toBe("");
    expect(evaluate(cs, 3).objects.find((o) => o.id === "thing")?.params.focus).toBe("lid");
  });

  it("rejects broken art", () => {
    const badPath = { ...ART, shapes: [{ d: "M0 0 L oops", fill: "red" }] };
    expect(errorCodes(scene(badPath))).toContain("FIGURE_INVALID");
    const badColor = { ...ART, shapes: [{ d: "M0 0 L10 10", fill: "rouge" }] };
    expect(errorCodes(scene(badColor))).toContain("FIGURE_INVALID");
    const reserved = { ...ART, anchors: [{ name: "center", x: 10, y: 10 }] };
    expect(errorCodes(scene(reserved))).toContain("FIGURE_INVALID");
    const outside = { ...ART, anchors: [{ name: "far", x: 900, y: 10 }] };
    expect(errorCodes(scene(outside))).toContain("FIGURE_INVALID");
  });
});
