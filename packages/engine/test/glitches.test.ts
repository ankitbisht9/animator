/**
 * Visual glitch checks (ADR-040): text hidden under later objects, content leaving the stage, a row of
 * items wrapping, containers drawn into each other, things sticking out of a container; and the
 * geometry fixes: endpoint-drawn objects turn in place, a
 * circle marker never cuts through what it circles.
 */
import { describe, expect, it } from "vitest";
import { compileScene, enclosure, evaluate, type ValidationIssue } from "../src";
import { compiled, lifted } from "./helpers";

const issues = (objects: unknown[], extra: Record<string, unknown> = {}) =>
  compileScene(lifted({ objects, ...extra })).issues;
const find = (objects: unknown[], code: string, extra: Record<string, unknown> = {}): ValidationIssue | undefined =>
  issues(objects, extra).find((i) => i.code === code);

const label = (id: string, text: string, x: number, y: number) => ({
  id,
  type: "label",
  params: { text, style: "pill" },
  place: { x, y },
});
const rect = (id: string, x: number, y: number, width = 300, height = 200) => ({
  id,
  type: "rectangle",
  params: { width, height },
  place: { x, y },
});

describe("text hidden under a later object", () => {
  it("flags a label covered by a shape drawn after it, and says how to move them apart", () => {
    const hidden = find([label("height", "height", 700, 450), rect("ramp", 760, 470)], "HIDDEN_TEXT");
    expect(hidden?.message).toContain("'height' is hidden under 'ramp'");
    expect(hidden?.path).toBe("/objects/height"); // the smaller one moves
    expect(Math.abs(hidden?.fix?.dx ?? 0) + Math.abs(hidden?.fix?.dy ?? 0)).toBeGreaterThan(0);
  });

  it("does not flag text written on top of a shape", () => {
    expect(find([rect("box", 760, 470), label("name", "box", 760, 470)], "HIDDEN_TEXT")).toBeUndefined();
  });

  it("counts the label of an arrow a card is drawn over", () => {
    const objects = [
      { id: "a", type: "block", params: { width: 80, height: 80 }, place: { x: 400, y: 450 } },
      {
        id: "v",
        type: "vector_arrow",
        params: { from: "a.right", direction: "right", magnitude: 1, scale: 200, label: "motion", labelSide: "above" },
      },
      {
        id: "card",
        type: "card",
        params: { title: "Recap", items: ["Height becomes motion."] },
        place: { x: 600, y: 430 },
      },
    ];
    expect(find(objects, "HIDDEN_TEXT")?.message).toContain("'v' is hidden under 'card'");
  });
});

describe("content leaving the stage", () => {
  it("flags text against the frame edge and says how far to move it in", () => {
    const edge = find([label("v", "v = how fast it moves", 1400, 450)], "OUT_OF_STAGE");
    expect(edge).toBeDefined();
    expect(edge?.fix?.dx).toBeLessThan(0);
    const moved = label("v", "v = how fast it moves", 1400 + (edge?.fix?.dx ?? 0), 450);
    expect(find([moved], "OUT_OF_STAGE")).toBeUndefined();
  });

  it("flags a drawing reaching behind the caption bar, not one ending just above it", () => {
    expect(find([rect("ramp", 800, 700, 500, 200)], "OUT_OF_STAGE")?.fix?.dy).toBeLessThan(0);
    expect(find([rect("ramp", 800, 600, 500, 200)], "OUT_OF_STAGE")).toBeUndefined();
  });

  it("leaves a title in the title bar alone", () => {
    const title = {
      id: "title_s01",
      type: "text",
      params: { text: "Title", size: "heading" },
      place: { slot: "title_bar" },
    };
    expect(find([title], "OUT_OF_STAGE")).toBeUndefined();
  });
});

describe("a row of items wrapping", () => {
  const row = (maxWidth?: number) => ({
    id: "row",
    type: "text",
    params: { text: "1   2   3   4   5   6   7   8   9   10   11   12   13   14   15   16", size: "heading", maxWidth },
    place: { x: 800, y: 400 },
  });

  it("flags a row of numbers that wraps, with the width that keeps it on one line", () => {
    const wrapped = find([row(500)], "TEXT_WRAPPED");
    expect(wrapped?.fix?.params?.maxWidth).toBeGreaterThan(500);
    const fixed = row(Number(wrapped?.fix?.params?.maxWidth));
    expect(find([fixed], "TEXT_WRAPPED")).toBeUndefined();
  });

  it("does not flag a sentence that wraps", () => {
    const sentence = {
      id: "s",
      type: "text",
      params: { text: "Water pushes up on anything that sits in it, and that push is buoyancy.", maxWidth: 300 },
      place: { x: 800, y: 400 },
    };
    expect(find([sentence], "TEXT_WRAPPED")).toBeUndefined();
  });
});

describe("containers", () => {
  const tank = (id: string, x: number, width = 400) => ({
    id,
    type: "container",
    params: { shape: "tank", width, height: 300, level: 0.5 },
    place: { x, y: 450 },
  });

  it("flags one container drawn into another, but not two side by side", () => {
    expect(find([tank("tank", 700), tank("cup", 820, 200)], "OBJECT_OVERLAP")).toBeDefined();
    expect(find([tank("tank", 500), tank("cup", 1100, 200)], "OBJECT_OVERLAP")).toBeUndefined();
  });

  it("flags a block sticking out through a container's floor", () => {
    const block = (y: number) => ({
      id: "stone",
      type: "block",
      params: { width: 100, height: 100 },
      place: { x: 700, y },
    });
    expect(find([tank("tank", 700), block(620)], "LEAVES_CONTAINER")?.message).toContain(
      "'stone' sticks out of 'tank'",
    );
    expect(find([tank("tank", 700), block(480)], "LEAVES_CONTAINER")).toBeUndefined();
  });
});

describe("lines and see-through shapes", () => {
  it("flags a plain line (a wire) running through a label", () => {
    const wire = { id: "wire", type: "line", params: { from: { x: 500, y: 450 }, to: { x: 1100, y: 450 } } };
    expect(find([wire, label("bulb", "dark bulb", 800, 450)], "CONNECTOR_CROSSES_LABEL")?.message).toContain(
      "'wire' runs across 'bulb'",
    );
    expect(find([wire, label("bulb", "dark bulb", 800, 380)], "CONNECTOR_CROSSES_LABEL")).toBeUndefined();
  });

  it("does not count an object over a see-through region as hiding it", () => {
    const region = { ...rect("water", 700, 450, 440, 110), opacity: 0.6 };
    const ship = {
      id: "ship",
      type: "icon",
      params: { name: "ship", size: 200, badge: false },
      place: { x: 720, y: 430 },
    };
    expect(find([region, ship], "OBJECT_OVERLAP")).toBeUndefined();
    expect(find([rect("water", 700, 450, 440, 110), ship], "OBJECT_OVERLAP")).toBeDefined();
  });
});

describe("endpoint-drawn objects turn in place", () => {
  it("rotates an arrow about its own middle, not the corner of the frame", () => {
    const cs = compiled({
      objects: [{ id: "d", type: "arrow", params: { from: { x: 1100, y: 500 }, to: { x: 1160, y: 500 } } }],
      timeline: [
        { id: "c", action: "draw", target: "d", start: { at: 0 } },
        { id: "r", action: "rotate", target: "d", to: 90, start: { at: 1 }, duration: 1 },
      ],
    });
    const box = evaluate(cs, 3).objects.find((o) => o.id === "d")?.bbox;
    expect(box).toBeDefined();
    if (!box) return;
    expect(box.x + box.w / 2).toBeCloseTo(1130, 0);
    expect(box.y + box.h / 2).toBeCloseTo(500, 0);
  });
});

describe("circle markers", () => {
  const corners = (b: { x: number; y: number; w: number; h: number }): [number, number][] => [
    [b.x, b.y],
    [b.x + b.w, b.y],
    [b.x, b.y + b.h],
    [b.x + b.w, b.y + b.h],
  ];

  it("ring a compact box with an ellipse that clears its corners", () => {
    const b = { x: 100, y: 100, w: 120, h: 80 };
    const e = enclosure(b, 10);
    expect(e.kind).toBe("ellipse");
    if (e.kind !== "ellipse") return;
    for (const [x, y] of corners(b)) expect(((x - e.cx) / e.rx) ** 2 + ((y - e.cy) / e.ry) ** 2).toBeLessThan(1);
  });

  it("ring a long row with a capsule whose round ends clear its corners", () => {
    const b = { x: 200, y: 400, w: 1000, h: 50 };
    const e = enclosure(b, 20);
    expect(e.kind).toBe("capsule");
    if (e.kind !== "capsule") return;
    const left = { x: e.x + e.r, y: e.y + e.r };
    expect(Math.hypot(b.x - left.x, b.y - left.y)).toBeLessThanOrEqual(e.r - 20 + 1e-6);
    expect(e.w).toBeLessThan(b.w + 2 * (b.h / 2 + 20) + 1e-6);
  });
});
