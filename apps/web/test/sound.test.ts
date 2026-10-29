/** Sound (ADR-065): which actions make which sound, bursts thinned to one, nothing on a seek. */
import { describe, expect, it } from "vitest";
import { buildLesson } from "../src/lesson/lesson";
import { cuesBetween, type SoundCue, soundCues } from "../src/player/sound";

function scene(id: string, timeline: unknown[], extra: Record<string, unknown> = {}) {
  return {
    dsl_version: "1.0",
    id,
    title: id,
    duration: 8,
    transition_in: { type: "fade", fresh: true },
    narration: { beats: [{ id: "b1", text: "A ball." }] },
    objects: [
      { id: "title", type: "text", params: { text: "Title" }, place: { slot: "title_bar" }, layer: "overlay" },
      { id: "ball", type: "circle", params: { radius: 50 }, place: { x: 400, y: 400 } },
      { id: "box", type: "rectangle", params: { width: 120, height: 80 }, place: { x: 900, y: 400 } },
    ],
    timeline,
    ...extra,
  };
}

const lesson = buildLesson("Sounds", [
  scene("s01", [
    { id: "t", action: "write", target: "title", start: { at: 0.1 } },
    { id: "a", action: "create", target: "ball", start: { at: 1 } },
    { id: "b", action: "create", target: "box", start: { at: 1.05 } }, // the same moment: one pop
    { id: "m", action: "move", target: "ball", to: { x: 600, y: 400 }, duration: 1.2, start: { at: 3 } },
    { id: "h", action: "highlight", target: "box", start: { at: 5 } },
  ]),
  scene(
    "s02",
    [
      { id: "a", action: "create", target: "ball", start: { at: 0.5 } },
      { id: "j", action: "move", target: "ball", to: { x: 420, y: 400 }, duration: 0.2, start: { at: 2 } }, // too short
    ],
    {
      interactions: [
        { id: "q1", at: 4, question: "Does it roll?", choices: ["yes", "no"], answer: 0, explain: "It does." },
      ],
    },
  ),
]);

describe("sound cues", () => {
  const cues = soundCues(lesson);
  const at = (c: SoundCue) => `${c.kind}@${c.t.toFixed(2)}`;

  it("turns what happens on screen into a few soft sounds", () => {
    expect(lesson.scenes.every((s) => s.ok)).toBe(true);
    const second = lesson.scenes[1]?.start ?? 0;
    expect(cues.map(at)).toEqual([
      "pop@1.00", // the ball; the box a moment later makes no second pop; the title's write is silent
      "whoosh@3.00",
      "chime@5.00",
      "swish@" + (second + 0.02).toFixed(2), // the next scene
      "pop@" + (second + 0.5).toFixed(2),
      "quiz@" + (second + 3.75).toFixed(2), // just before the quick check; the short nudge is silent
    ]);
  });

  it("plays only what the clock passes while playing, never on a jump", () => {
    expect(cuesBetween(cues, 0.9, 1.0).map((c) => c.kind)).toEqual(["pop"]);
    expect(cuesBetween(cues, 1.0, 1.2)).toEqual([]); // already passed
    expect(cuesBetween(cues, 0, 4)).toEqual([]); // a seek across the scene
    expect(cuesBetween(cues, 3.2, 2.9)).toEqual([]); // backwards
  });
});
