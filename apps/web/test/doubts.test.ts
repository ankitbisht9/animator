/** Doubts (ADR-050): an answer's scene compiles in the browser, on its own (a board) or continuing from
 * the lesson moment it reuses (a picture), with the same engine as the lesson. */
import { compileScene, lift, sceneEndState } from "@animator/engine";
import { describe, expect, it } from "vitest";
import type { DoubtAnswer } from "../src/doubts/api";
import { compileAnswer } from "../src/doubts/DoubtPlayer";

const lessonScene = {
  dsl_version: "1.0",
  id: "s01",
  title: "A ball",
  duration: 6,
  objects: [{ id: "ball", type: "circle", params: { radius: 60 }, place: { slot: "stage.center" } }],
  timeline: [{ id: "c", action: "create", target: "ball", start: { at: 0.2 } }],
};

function answer(scene: unknown, prev: DoubtAnswer["prev_state"] = null): DoubtAnswer {
  return {
    session_id: "d_000000000001",
    n: 1,
    question: "Why?",
    kind: "lesson",
    depth: "quick",
    title: "Because",
    sentences: ["Look at the ball."],
    suggestion: null,
    visual: { mode: prev ? "scene" : "board", scene: prev ? 1 : null },
    scene,
    prev_state: prev,
    narration: null,
    duration: 4,
    cap_s: 30,
    cost_usd: 0,
    model: "m",
    latency_s: 1,
  };
}

describe("doubt answers", () => {
  it("compile a board on its own", () => {
    const board = {
      dsl_version: "1.0",
      id: "doubt_1",
      title: "Because",
      duration: 4,
      narration: { beats: [{ id: "b1", text: "Look at the ball.", at: 0.3 }] },
      objects: [{ id: "doubt_title", type: "text", params: { text: "Because" }, place: { slot: "title_bar" } }],
      timeline: [{ id: "w", action: "write", target: "doubt_title", start: { at: 0.1 } }],
    };
    expect(compileAnswer(answer(board))?.duration).toBe(4);
  });

  it("continue from the lesson moment they reuse, pointing at what is there", () => {
    const lifted = lift(lessonScene);
    const compiled = compileScene(lifted.scene as never, { prev: null }).compiled;
    expect(compiled).toBeTruthy();
    const moment = sceneEndState(compiled as never, null, 0, 3);
    const picture = {
      dsl_version: "1.0",
      id: "doubt_2",
      title: "Because",
      duration: 4,
      transition_in: { type: "continue" },
      narration: { beats: [{ id: "b1", text: "Look at the ball.", at: 0.3 }] },
      objects: [],
      timeline: [
        { id: "g", action: "highlight", target: "ball", style: "glow", start: { at: "beat:b1" }, duration: 1 },
      ],
    };
    const got = compileAnswer(answer(picture, moment));
    expect(got?.objects.map((o) => o.id)).toContain("ball"); // the lesson's ball, carried over
    expect(compileAnswer(answer(picture, null))).toBeNull(); // without the moment there is no ball
  });
});
