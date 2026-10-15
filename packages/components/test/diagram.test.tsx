/** Diagrams (ADR-061): drawn from the engine's layout, built in order, marked by part, morphing. */
import { compileScene, evaluate, lift } from "@animator/engine";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SceneView } from "../src";

const HISTORY = {
  layout: "lanes",
  lanes: [
    { id: "main", label: "main" },
    { id: "feature", label: "feature" },
  ],
  nodes: [
    { id: "a", label: "A", lane: "main" },
    { id: "b", label: "B", lane: "main" },
    { id: "e", label: "E", lane: "main", note: "fix login" },
    { id: "c", label: "C", lane: "feature" },
    { id: "d", label: "D", lane: "feature", ghost: true },
  ],
  edges: [
    { from: "a", to: "b" },
    { from: "b", to: "e" },
    { from: "b", to: "c" },
    { from: "c", to: "d", id: "c_d" },
  ],
};

function compiled(objects: unknown[], timeline: unknown[], theme = "educational_clean") {
  const l = lift({
    dsl_version: "1.0",
    id: "s1",
    title: "Diagram",
    duration: 8,
    theme,
    narration: { beats: [{ id: "b1", text: "Two lines of work." }] },
    objects,
    timeline,
  });
  if (!l.ok || !l.scene) throw new Error(JSON.stringify(l.issues));
  const c = compileScene(l.scene, { prev: null });
  if (!c.ok || !c.compiled) throw new Error(JSON.stringify(c.issues.filter((i) => i.severity === "error")));
  return c.compiled;
}

const svgAt = (cs: ReturnType<typeof compiled>, t: number) =>
  renderToStaticMarkup(<SceneView compiled={cs} frame={evaluate(cs, t)} />);
const history = { id: "history", type: "diagram", params: HISTORY, place: { slot: "stage.center" } };

describe("diagram renderer", () => {
  it("draws every node, edge and lane tag, with no broken numbers", () => {
    const svg = svgAt(compiled([history], [{ id: "c", action: "create", target: "history", start: { at: 0 } }]), 6);
    expect(svg).not.toContain("NaN");
    // five nodes: their outlines (circles drawn as two arcs)
    expect(svg.match(/A[\d.]+ [\d.]+ 0 1 1/g)?.length).toBe(10);
    // a ghost commit is dashed
    expect(svg).toContain('stroke-dasharray="8 6"');
    // lane tags in the lanes' colors
    expect(svg).toMatch(/fill="#2f6fdb"/);
  });

  it("builds node by node while it is created, and adds what `write` names when it says", () => {
    const cs = compiled(
      [history],
      [
        { id: "c", action: "create", target: "history", start: { at: 0 }, duration: 3 },
        { id: "w", action: "write", target: "history", terms: ["c_d"], start: { at: 5 }, duration: 1 },
      ],
    );
    const nodes = (svg: string) => svg.match(/A[\d.]+ [\d.]+ 0 1 1/g)?.length ?? 0;
    const early = nodes(svgAt(cs, 0.4));
    const done = nodes(svgAt(cs, 4));
    expect(early).toBeLessThan(done);
    expect(done).toBe(10);
    const paths = (svg: string) => (svg.match(/<path d="M[^"]*C/g) ?? []).length;
    expect(paths(svgAt(cs, 4.5))).toBeLessThan(paths(svgAt(cs, 6.5))); // the named edge c_d comes later
  });

  it("rings a part named by `highlight`", () => {
    const cs = compiled(
      [history],
      [
        { id: "c", action: "create", target: "history", start: { at: 0 } },
        { id: "h", action: "highlight", target: "history", terms: ["e"], start: { at: 3 }, duration: 2 },
      ],
    );
    const plain = svgAt(cs, 2.5);
    const marked = svgAt(cs, 4);
    expect(marked.length).toBeGreaterThan(plain.length);
    expect(marked).toContain('stroke-width="4"');
  });

  it("rings several parts at once, each in its own color", () => {
    const cs = compiled(
      [history],
      [
        { id: "c", action: "create", target: "history", start: { at: 0 } },
        {
          id: "h1",
          action: "highlight",
          target: "history",
          terms: ["a"],
          color: "highlight",
          start: { at: 3 },
          duration: 3,
        },
        {
          id: "h2",
          action: "highlight",
          target: "history",
          terms: ["e"],
          color: "accent",
          start: { at: 4 },
          duration: 3,
        },
      ],
    );
    const rings = (svg: string) => (svg.match(/stroke-width="4"/g) ?? []).length;
    const plain = rings(svgAt(cs, 2.5));
    expect(rings(svgAt(cs, 3.5))).toBe(plain + 1);
    expect(rings(svgAt(cs, 5))).toBe(plain + 2);
  });

  it("moves shared nodes from their old places when it changes into its next state", () => {
    const after = {
      id: "after",
      type: "diagram",
      params: {
        ...HISTORY,
        nodes: HISTORY.nodes.map((n) => (n.id === "c" ? { ...n, label: "C'" } : n)),
        edges: [
          { from: "a", to: "b" },
          { from: "b", to: "e" },
          { from: "e", to: "c" },
          { from: "c", to: "d" },
        ],
      },
      place: { slot: "stage.center" },
    };
    const cs = compiled(
      [history, after],
      [
        { id: "c", action: "create", target: "history", start: { at: 0 } },
        { id: "t", action: "transform", target: "history", into: "after", start: { at: 3 }, duration: 2 },
      ],
    );
    const start = svgAt(cs, 3.05);
    const mid = svgAt(cs, 4);
    const end = svgAt(cs, 5.5);
    expect(start).not.toContain("NaN");
    expect(mid).not.toContain("NaN");
    expect(mid).not.toEqual(end);
    expect(svgAt(cs, 7)).toEqual(end);
  });

  it("follows the dark theme", () => {
    const svg = svgAt(
      compiled(
        [history],
        [{ id: "c", action: "create", target: "history", start: { at: 0 } }],
        "educational_clean_dark",
      ),
      6,
    );
    expect(svg).not.toContain("NaN");
    expect(svg.length).toBeGreaterThan(1000);
  });
});
