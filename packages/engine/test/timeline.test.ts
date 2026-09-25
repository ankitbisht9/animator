import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { evaluate, evaluateObjects } from "../src";
import { compiled, errorCodes } from "./helpers";

const ball = { id: "ball", type: "circle", params: { radius: 40 }, place: { x: 400, y: 400 } };

describe("relative timing", () => {
  it("resolves after / with / delay / cues / beats", () => {
    const cs = compiled({
      duration: 10,
      cues: [{ id: "drop", at: 2 }],
      narration: {
        beats: [
          { id: "b1", text: "One." },
          { id: "b2", text: "Two.", at: 6 },
        ],
      },
      objects: [ball],
      timeline: [
        { id: "a", action: "create", target: "ball", start: { at: "cue:drop" }, duration: 1 },
        {
          id: "b",
          action: "move",
          target: "ball",
          by: { x: 100, y: 0 },
          start: { after: "a", delay: 0.5 },
          duration: 1,
        },
        { id: "c", action: "scale", target: "ball", to: 2, start: { with: "b" }, duration: 1 },
        { id: "d", action: "fade", target: "ball", to: 0.5, start: { at: "beat:b2" }, duration: 1 },
      ],
    });
    expect(cs.actions.a).toMatchObject({ start: 2, end: 3 });
    expect(cs.actions.b).toMatchObject({ start: 3.5, end: 4.5 });
    expect(cs.actions.c).toMatchObject({ start: 3.5, end: 4.5 });
    expect(cs.actions.d).toMatchObject({ start: 6, end: 7 });
    expect(cs.beats.map((b) => [b.id, b.start, b.end])).toEqual([
      ["b1", 0, 6],
      ["b2", 6, 10],
    ]);
  });

  it("spreads unanchored beats evenly", () => {
    const cs = compiled({
      duration: 9,
      narration: {
        beats: [
          { id: "a", text: "A." },
          { id: "b", text: "B." },
          { id: "c", text: "C." },
        ],
      },
    });
    expect(cs.beats.map((b) => b.start)).toEqual([0, 3, 6]);
  });

  it("detects timing cycles", () => {
    expect(
      errorCodes({
        objects: [ball],
        timeline: [
          { id: "a", action: "create", target: "ball", start: { after: "b" } },
          { id: "b", action: "fade", target: "ball", to: 1, start: { after: "a" } },
        ],
      }),
    ).toContain("TIMING_CYCLE");
  });

  it("rejects actions that run past the scene end", () => {
    expect(
      errorCodes({
        duration: 2,
        objects: [ball],
        timeline: [{ action: "move", target: "ball", by: { x: 1, y: 0 }, start: { at: 1.5 }, duration: 1 }],
      }),
    ).toContain("ACTION_EXCEEDS_SCENE");
  });

  it("rejects unknown references with suggestions", () => {
    expect(errorCodes({ objects: [ball], timeline: [{ action: "create", target: "bal" }] })).toContain(
      "UNKNOWN_OBJECT",
    );
    expect(
      errorCodes({ objects: [ball], timeline: [{ action: "create", target: "ball", start: { after: "nope" } }] }),
    ).toContain("UNKNOWN_ACTION_REF");
    expect(
      errorCodes({ objects: [ball], timeline: [{ action: "create", target: "ball", start: { at: "cue:nope" } }] }),
    ).toContain("UNKNOWN_CUE");
  });
});

describe("action compiler and tracks", () => {
  it("reports conflicting tracks on the same property", () => {
    expect(
      errorCodes({
        objects: [ball],
        timeline: [
          { id: "m1", action: "move", target: "ball", by: { x: 100, y: 0 }, start: { at: 1 }, duration: 2 },
          { id: "m2", action: "move", target: "ball", by: { x: 0, y: 100 }, start: { at: 2 }, duration: 2 },
        ],
      }),
    ).toContain("TRACK_CONFLICT");
  });

  it("chains moves from the value the previous move ended at", () => {
    const cs = compiled({
      objects: [ball],
      timeline: [
        { id: "m1", action: "move", target: "ball", by: { x: 100, y: 0 }, start: { at: 0 }, duration: 1 },
        { id: "m2", action: "move", target: "ball", by: { x: 100, y: 0 }, start: { after: "m1" }, duration: 1 },
      ],
    });
    const x = (t: number) => evaluateObjects(cs, t).objects.get("ball")?.layoutBBox.x ?? Number.NaN;
    expect(x(0)).toBeCloseTo(360);
    expect(x(1)).toBeCloseTo(460);
    expect(x(2)).toBeCloseTo(560);
    expect(x(6)).toBeCloseTo(560);
  });

  it("hides objects until created and fades them out when removed", () => {
    const cs = compiled({
      objects: [ball],
      timeline: [
        { action: "create", target: "ball", start: { at: 1 }, duration: 1, style: "fade" },
        { action: "remove", target: "ball", start: { at: 4 }, duration: 1 },
      ],
    });
    const visible = (t: number) => evaluate(cs, t).objects.some((o) => o.id === "ball");
    expect(visible(0.5)).toBe(false);
    expect(visible(3)).toBe(true);
    expect(visible(5.5)).toBe(false);
  });

  it("writes equations term by term", () => {
    // Uses text (write-capable) to check reveal progress; equations are covered in Phase 3 tests.
    const cs = compiled({
      objects: [{ id: "t", type: "text", params: { text: "Archimedes" }, place: { slot: "stage.center" } }],
      timeline: [{ action: "write", target: "t", start: { at: 1 }, duration: 2 }],
    });
    const reveal = (t: number) => evaluate(cs, t).objects.find((o) => o.id === "t")?.reveal ?? -1;
    expect(reveal(0.5)).toBe(-1);
    expect(reveal(2)).toBeGreaterThan(0);
    expect(reveal(2)).toBeLessThan(1);
    expect(reveal(3)).toBe(1);
  });

  it("evaluate is pure: order of evaluation never changes a frame", () => {
    const cs = compiled({
      objects: [
        ball,
        {
          id: "lbl",
          type: "text",
          params: { text: "Ball" },
          place: { relativeTo: "ball", side: "above", follow: true },
        },
      ],
      timeline: [
        { action: "create", target: "ball" },
        { action: "move", target: "ball", to: { slot: "stage.right" }, path: "arc" },
        { action: "highlight", target: "ball" },
        { action: "rotate", target: "ball", by: 90 },
      ],
    });
    fc.assert(
      fc.property(fc.array(fc.double({ min: 0, max: 6, noNaN: true }), { minLength: 2, maxLength: 8 }), (times) => {
        const first = times.map((t) => JSON.stringify(evaluate(cs, t)));
        const again = [...times]
          .reverse()
          .map((t) => JSON.stringify(evaluate(cs, t)))
          .reverse();
        return first.every((f, i) => f === again[i]);
      }),
      { numRuns: 30 },
    );
  });

  it("tweens are continuous and end exactly on their target", () => {
    fc.assert(
      // Durations have at most 3 decimals (the normalizer trims longer ones).
      fc.property(
        fc.integer({ min: -300, max: 500 }),
        fc.integer({ min: 100, max: 3000 }).map((ms) => ms / 1000),
        (dx, dur) => {
          const cs = compiled({
            duration: 5,
            objects: [ball],
            timeline: [
              { id: "m", action: "move", target: "ball", by: { x: dx, y: 0 }, start: { at: 1 }, duration: dur },
            ],
          });
          const x = (t: number) => evaluateObjects(cs, t).objects.get("ball")?.layoutBBox.x as number;
          const end = x(1 + dur);
          const eps = 1e-3;
          // ease_in_out peaks at 3x the average speed: a tiny time step moves at most that much.
          const step = Math.abs(x(1 + dur / 2 + eps) - x(1 + dur / 2));
          return Math.abs(end - (360 + dx)) < 1e-6 && step <= (Math.abs(dx) * 3 * eps) / dur + 1e-6;
        },
      ),
      { numRuns: 25 },
    );
  });

  it("compiles identically twice (determinism)", () => {
    const spec = {
      objects: [ball],
      timeline: [
        { action: "create", target: "ball" },
        { action: "move", target: "ball", to: { slot: "stage.right" } },
      ],
    };
    expect(JSON.stringify(compiled(spec))).toBe(JSON.stringify(compiled(spec)));
  });

  it("groups move members rigidly and ungroup keeps world positions", () => {
    const cs = compiled({
      duration: 8,
      objects: [ball, { id: "b2", type: "circle", params: { radius: 20 }, place: { x: 600, y: 400 } }],
      timeline: [
        { id: "g", action: "group", target: "pair", members: ["ball", "b2"], start: { at: 1 } },
        { id: "m", action: "move", target: "pair", by: { x: 0, y: 100 }, start: { at: 2 }, duration: 1 },
        { id: "r", action: "rotate", target: "pair", by: 180, start: { at: 3 }, duration: 1 },
        { id: "u", action: "ungroup", target: "pair", start: { at: 5 } },
      ],
    });
    const c = (id: string, t: number) => {
      const b = evaluateObjects(cs, t).objects.get(id)?.layoutBBox;
      return b ? [Math.round(b.x + b.w / 2), Math.round(b.y + b.h / 2)] : null;
    };
    expect(c("ball", 3)).toEqual([400, 500]);
    expect(c("b2", 3)).toEqual([600, 500]);
    // The pair turns 180 degrees about the center of its bounds (x = 490): members swap sides.
    expect(c("ball", 4.5)).toEqual([580, 500]);
    expect(c("b2", 4.5)).toEqual([380, 500]);
    expect(c("ball", 7)).toEqual([580, 500]);
  });
});
