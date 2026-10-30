import { compileScene, evaluate, LAYOUTS, lift } from "@animator/engine";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  eyesOpen,
  gaze,
  HOST_BOX,
  HOST_LOOKS,
  isHostLook,
  mouthOf,
  type SceneNarration,
  SceneView,
  sayingAt,
} from "../src";

/** A ball on the right that is highlighted from 2 s to 3 s, and one sentence of narration. */
function compiled() {
  const l = lift({
    dsl_version: "1.0",
    id: "s1",
    title: "Host",
    duration: 6,
    narration: { beats: [{ id: "b1", text: "Look at **the ball** now." }] },
    objects: [{ id: "ball", type: "circle", params: { radius: 60 }, place: { x: 1300, y: 300 } }],
    timeline: [
      { id: "in", action: "create", target: "ball", start: { at: 0 }, duration: 0.5 },
      { id: "hi", action: "highlight", target: "ball", style: "glow", start: { at: 2 }, duration: 1 },
    ],
  });
  if (!l.scene) throw new Error("bad scene");
  const res = compileScene(l.scene);
  if (!res.compiled) throw new Error("did not compile");
  return res.compiled;
}

/** The voice says "Look" 0.1-0.4 s, "at" 0.5-0.6, "the" 0.7-0.8, "ball" 0.9-1.3, "now." 1.5-1.8. */
const speech: SceneNarration = {
  beats: [
    {
      id: "b1",
      start: 0,
      audio: "x.mp3",
      duration: 2,
      words: [
        [0.1, 0.4],
        [0.5, 0.6],
        [0.7, 0.8],
        [0.9, 1.3],
        [1.5, 1.8],
      ],
    },
  ],
};

describe("the host (ADR-069)", () => {
  it("sits in the frame's corner, clear of every layout's stage, title and captions", () => {
    const h = HOST_BOX;
    for (const layout of Object.values(LAYOUTS)) {
      for (const [name, r] of Object.entries(layout.regions)) {
        const apart = h.x + h.w <= r.x || r.x + r.w <= h.x || h.y + h.h <= r.y || r.y + r.h <= h.y;
        expect(apart, `${layout.id}.${name}`).toBe(true);
      }
    }
    expect(HOST_LOOKS.every(isHostLook)).toBe(true);
    expect(isHostLook("nobody")).toBe(false);
  });

  it("is drawn only when asked for", () => {
    const c = compiled();
    const frame = evaluate(c, 1);
    expect(renderToStaticMarkup(<SceneView compiled={c} frame={frame} />)).not.toContain("data-host");
    const withHost = renderToStaticMarkup(<SceneView compiled={c} frame={frame} host="asha" narration={speech} />);
    expect(withHost).toContain('data-host="asha"');
  });

  it("talks as the voice says each word, and is quiet between words", () => {
    const c = compiled();
    const at = (t: number) => sayingAt(c, evaluate(c, t), speech);
    expect(at(1.1)?.word).toBe("ball");
    expect(at(1.1)?.u).toBeCloseTo(0.5, 2);
    expect(at(1.4)).toBeNull(); // between "ball" and "now."
    expect(mouthOf(at(1.1))).toEqual({ open: 1, shape: "a" });
    expect(mouthOf(at(1.4)).open).toBe(0);
    // Without a voice, the caption is read at an even pace.
    expect(sayingAt(c, evaluate(c, 0.1), null)?.word).toBe("Look");
  });

  it("shapes the mouth by the syllables and the vowels said", () => {
    expect(mouthOf({ word: "go", u: 0.5 }).shape).toBe("o");
    expect(mouthOf({ word: "see", u: 0.5 }).shape).toBe("e");
    expect(mouthOf({ word: "water", u: 0.25 }).open).toBeCloseTo(1, 5); // the middle of "wa"
    expect(mouthOf({ word: "water", u: 0.5 }).open).toBeCloseTo(0, 5); // between "wa" and "ter"
    expect(mouthOf({ word: "mother", u: 0.05 }).open).toBe(0); // the lips meet on "m"
    expect(mouthOf(null).open).toBe(0);
  });

  it("blinks every few seconds, the same way every time", () => {
    const samples = Array.from({ length: 600 }, (_, i) => eyesOpen(i / 50, 7));
    expect(samples).toEqual(Array.from({ length: 600 }, (_, i) => eyesOpen(i / 50, 7)));
    const shut = samples.filter((v) => v < 0.5).length;
    expect(shut).toBeGreaterThan(0); // it blinks in 12 seconds
    expect(shut / samples.length).toBeLessThan(0.05); // and its eyes are open nearly all the time
  });

  it("looks at what is highlighted, raising its brows", () => {
    const c = compiled();
    const highlighted = gaze(evaluate(c, 2.5));
    expect(highlighted.gx).toBeGreaterThan(1.5); // the ball is far to the right
    expect(highlighted.gy).toBeLessThan(0); // and above
    expect(highlighted.lively).toBeGreaterThan(0.5);
    expect(gaze(evaluate(c, 4.5))).toEqual({ gx: 0, gy: 0, lively: 0 }); // nothing happening: at the learner
  });
});
