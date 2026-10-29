import { compileScene, evaluate, lift } from "@animator/engine";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { downIn, type FinishId, isFinish, liftable, lightFor, richStops, SceneView } from "../src";

/** A scene with a big circle, a small dot, a stage-wide ground strip and a diagram. */
function compiled() {
  const l = lift({
    dsl_version: "1.0",
    id: "s1",
    title: "Finish",
    duration: 4,
    narration: { beats: [{ id: "b1", text: "Shapes." }] },
    objects: [
      { id: "ball", type: "circle", params: { radius: 80 }, style: { fill: "blue" }, place: { x: 500, y: 400 } },
      { id: "dot", type: "circle", params: { radius: 8 }, style: { fill: "red" }, place: { x: 700, y: 400 } },
      {
        id: "ground",
        type: "rectangle",
        params: { width: 1400, height: 60 },
        style: { fill: "green" },
        place: { x: 800, y: 650 },
      },
      {
        id: "flow",
        type: "diagram",
        params: {
          layout: "layered",
          nodes: [
            { id: "a", label: "Start" },
            { id: "b", label: "End" },
          ],
          edges: [{ from: "a", to: "b" }],
          width: 500,
        },
        place: { x: 1150, y: 330 },
      },
    ],
    timeline: [
      { id: "a1", action: "create", target: "ball", start: { at: 0 } },
      { id: "a2", action: "create", target: "dot", start: { at: 0 } },
      { id: "a3", action: "create", target: "ground", start: { at: 0 } },
      { id: "a4", action: "create", target: "flow", start: { at: 0 } },
    ],
  });
  if (!l.ok || !l.scene) throw new Error(JSON.stringify(l.issues));
  const c = compileScene(l.scene, { prev: null });
  if (!c.ok || !c.compiled) throw new Error(JSON.stringify(c.issues));
  return c.compiled;
}

function svg(finish?: FinishId, t = 3) {
  const c = compiled();
  return renderToStaticMarkup(<SceneView compiled={c} frame={evaluate(c, t)} finish={finish} />);
}

describe("finishes (ADR-064)", () => {
  it("classic is the default and draws exactly as before", () => {
    expect(svg()).toEqual(svg("classic"));
    expect(svg("classic")).not.toContain('stop offset="0.55"');
  });

  it("rich lights each shape, edges the unstroked ones and lifts the large ones off the page", () => {
    const rich = svg("rich");
    expect(rich).not.toEqual(svg("classic"));
    expect(rich).toContain('<radialGradient id="a-ball-fill" cx="0.36" cy="0.3" r="0.8">');
    expect(rich).toContain('offset="0.55"'); // the three-stop light
    // The ball is big enough for a lift shadow; the dot and the stage-wide ground are not.
    const ball = rich.slice(rich.indexOf('data-id="ball"'), rich.indexOf('data-id="dot"'));
    const dot = rich.slice(rich.indexOf('data-id="dot"'), rich.indexOf('data-id="ground"'));
    const ground = rich.slice(rich.indexOf('data-id="ground"'), rich.indexOf('data-id="flow"'));
    expect(ball).toContain('transform="translate(0 6)"');
    expect(dot).not.toContain('transform="translate(0 6)"');
    expect(ground).not.toContain('transform="translate(0 6)"');
    // Diagram nodes get the light and a shadow too.
    const flow = rich.slice(rich.indexOf('data-id="flow"'));
    expect(flow).toContain("a-flow-dn-a");
  });

  it("is only the surface: while a shape is drawn on there is no shadow yet", () => {
    const early = svg("rich", 0.05);
    const ball = early.slice(early.indexOf('data-id="ball"'), early.indexOf('data-id="dot"'));
    expect(ball).not.toContain('transform="translate(0 6)"');
  });

  it("shades near-white fills gently and keeps light on dark ones", () => {
    const white = richStops("#ffffff", false);
    expect(white[0]?.[1]).toBe("#ffffff");
    expect(white[2]?.[1]).not.toBe("#ffffff");
    const dark = richStops("#1c2541", true);
    expect(dark[0]?.[1]).not.toBe("#1c2541");
  });

  it("lights objects fully and panels only as classic does, so slabs built side by side show no seams", () => {
    expect(lightFor(120, 60)).toBe(1);
    expect(lightFor(220, 40)).toBe(0.5);
    expect(lightFor(830, 230)).toBe(0);
    expect(richStops("#3366cc", false, 0)).toEqual(richStops("#3366cc", false, 0));
    expect(richStops("#3366cc", false, 0)[0]?.[1]).not.toEqual(richStops("#3366cc", false, 1)[0]?.[1]);
    const rich = svg("rich");
    const ground = rich.slice(rich.indexOf('data-id="ground"'), rich.indexOf('data-id="flow"'));
    expect(ground).not.toContain('stroke-width="2"'); // no crisp edge on a panel
  });

  it("drops shadows straight down whatever the object's turn", () => {
    expect(downIn([1, 0, 0, 1, 50, 50], 6)).toBe("translate(0 6)");
    expect(downIn([0, 1, -1, 0, 0, 0], 6)).toBe("translate(6 0)"); // turned 90°: down is local +x
    expect(downIn([2, 0, 0, 2, 0, 0], 6)).toBe("translate(0 3)"); // scaled 2x: half as far locally
    expect(downIn([0, 0, 0, 0, 0, 0], 6)).toBe("translate(0 6)");
    expect(isFinish("rich") && isFinish("classic") && !isFinish("glossy")).toBe(true);
    expect(liftable).toBeTypeOf("function");
  });
});
