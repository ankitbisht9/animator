import { describe, expect, it } from "vitest";
import { compileScene, lift, type SceneState, sceneEndState, summarizeState } from "../src";
import { base } from "./helpers";

function chain(scenes: Record<string, unknown>[]) {
  let prev: SceneState | null = null;
  return scenes.map((raw, i) => {
    const l = lift(base(raw));
    if (!l.ok || !l.scene) throw new Error(JSON.stringify(l.issues));
    const c = compileScene(l.scene, { prev });
    if (c.ok && c.compiled) prev = sceneEndState(c.compiled, prev, i);
    return { result: c, state: prev };
  });
}

describe("scene continuity", () => {
  it("carries objects with their end state into the next scene", () => {
    const [a, b] = chain([
      {
        id: "s1",
        objects: [{ id: "blk", type: "block", place: { slot: "stage.left" } }],
        timeline: [{ action: "move", target: "blk", to: { slot: "stage.right" } }],
      },
      { id: "s2", timeline: [{ action: "highlight", target: "blk" }] },
    ]);
    expect(b?.result.ok).toBe(true);
    const x1 = a?.state?.objects.blk?.x;
    const carried = b?.result.compiled?.objects.find((o) => o.id === "blk");
    expect(carried?.carried).toBe(true);
    expect(carried?.ch.x.base).toBe(x1);
    expect(b?.state?.registry.blk).toMatchObject({ type: "block", createdIn: "s1", lastScene: "s2" });
  });

  it("drops a one-shot burst once it has played instead of carrying it", () => {
    const [a, b] = chain([
      {
        id: "s1",
        duration: 4,
        objects: [{ id: "yay", type: "burst", place: { slot: "stage.center" } }],
        timeline: [{ action: "create", target: "yay", start: { at: 0.5 }, duration: 1.5 }],
      },
      { id: "s2" },
    ]);
    expect(a?.state?.objects.yay).toMatchObject({ lifecycle: "removed", visible: false });
    expect(b?.result.compiled?.objects.some((o) => o.id === "yay")).toBe(false);
  });

  it("a solver in a later scene can use a pipe carried over from an earlier one", () => {
    const pipe = {
      id: "pipe",
      type: "pipe",
      params: {
        sections: [
          { id: "wide", x: 0, width: 160 },
          { id: "narrow", x: 420, width: 70 },
        ],
      },
      place: { slot: "stage.center" },
    };
    const [, b] = chain([
      { id: "s1", objects: [pipe] },
      {
        id: "s2",
        solvers: [{ id: "flow", type: "fluid_flow", pipe: "pipe", flowRate: 0.02, inletPressure: 180000 }],
        objects: [
          {
            id: "gauge",
            type: "pressure_gauge",
            params: { value: { bind: "flow.p_narrow_kpa" }, label: "neck" },
            place: { slot: "stage.top_right" },
          },
        ],
      },
    ]);
    const codes = b?.result.issues.map((i) => i.code) ?? [];
    expect(codes).not.toContain("EXPR_UNKNOWN_IDENT");
    expect(b?.result.ok).toBe(true);
  });

  it("rejects a carried id reused for a different type (identity)", () => {
    const [, b] = chain([
      { id: "s1", objects: [{ id: "blk", type: "block", place: { slot: "stage.left" } }] },
      { id: "s2", objects: [{ id: "blk", type: "ball", place: { slot: "stage.left" } }] },
    ]);
    expect(b?.result.issues.map((i) => i.code)).toContain("IDENTITY_TYPE_CHANGE");
  });

  it("a fresh scene stands on its own: earlier ids and objects do not apply", () => {
    const [, b, c] = chain([
      { id: "s1", objects: [{ id: "blk", type: "block", place: { slot: "stage.left" } }] },
      {
        id: "s2",
        transition_in: { type: "fade", fresh: true },
        objects: [{ id: "blk", type: "ball", place: { slot: "stage.left" } }],
      },
      { id: "s3", transition_in: { type: "fade", fresh: true }, timeline: [{ action: "highlight", target: "blk" }] },
    ]);
    expect(b?.result.ok).toBe(true);
    expect(b?.result.compiled?.transitionIn.fresh).toBe(true);
    expect(b?.state?.registry.blk).toMatchObject({ type: "ball", createdIn: "s2" }); // a new registry
    // Nothing carries into a fresh scene either: s3 cannot refer to s2's ball.
    expect(c?.result.issues.map((i) => i.code)).toContain("UNKNOWN_OBJECT");
  });

  it("warns about identity drift: same id, new look, no transform", () => {
    const [, b] = chain([
      {
        id: "s1",
        objects: [{ id: "blk", type: "block", params: { material: "wood" }, place: { slot: "stage.left" } }],
      },
      {
        id: "s2",
        objects: [{ id: "blk", type: "block", params: { material: "stone" }, place: { slot: "stage.left" } }],
      },
    ]);
    expect(b?.result.issues.map((i) => i.code)).toContain("IDENTITY_DRIFT");
  });

  it("warns about abrupt disappearance of non-persistent objects", () => {
    const [, b] = chain([
      { id: "s1", objects: [{ id: "tmp", type: "circle", persist: false, place: { slot: "stage.left" } }] },
      { id: "s2" },
    ]);
    expect(b?.result.issues.map((i) => i.code)).toContain("ABRUPT_DISAPPEARANCE");
    const [, c] = chain([
      { id: "s1", objects: [{ id: "tmp", type: "circle", persist: false, place: { slot: "stage.left" } }] },
      { id: "s2", transition_in: { type: "fade" } },
    ]);
    expect(c?.result.issues.map((i) => i.code)).not.toContain("ABRUPT_DISAPPEARANCE");
  });

  it("a cut starts a clean stage except for kept objects", () => {
    const [, b] = chain([
      {
        id: "s1",
        objects: [
          { id: "a", type: "circle", place: { slot: "stage.left" } },
          { id: "k", type: "circle", place: { slot: "stage.right" } },
        ],
      },
      { id: "s2", transition_in: { type: "cut", keep: ["k"] } },
    ]);
    expect(b?.result.compiled?.objects.map((o) => o.id)).toEqual(["k"]);
  });

  it("removed objects stay removed", () => {
    const [, b] = chain([
      {
        id: "s1",
        objects: [{ id: "a", type: "circle", place: { slot: "stage.left" } }],
        timeline: [{ action: "remove", target: "a" }],
      },
      { id: "s2" },
    ]);
    expect(b?.result.compiled?.objects.map((o) => o.id)).toEqual([]);
  });

  it("summarizes state compactly for the LLM", () => {
    const [a] = chain([{ id: "s1", objects: [{ id: "tank", type: "container", place: { slot: "stage.center" } }] }]);
    const text = summarizeState(a?.state as SceneState);
    expect(text).toContain("tank (container, visible)");
    expect(text.length).toBeLessThan(600);
  });
});
