import { describe, expect, it } from "vitest";
import { buildLesson, type LessonCache, locate } from "../src/lesson/lesson";
import { voiceAt } from "../src/player/voice";

const scene = (id: string, duration: number, extra: Record<string, unknown> = {}) => ({
  dsl_version: "1.0",
  id,
  title: id,
  duration,
  narration: { beats: [{ id: "b1", text: "x" }] },
  objects: [{ id: `${id}_dot`, type: "circle", place: { slot: "stage.center" } }],
  ...extra,
});

describe("lesson building", () => {
  it("lays scenes out on a global timeline and locates times", () => {
    const lesson = buildLesson("L", [scene("a", 4), scene("b", 6)]);
    expect(lesson.duration).toBe(10);
    expect(lesson.scenes.map((s) => s.start)).toEqual([0, 4]);
    expect(locate(lesson, 3)).toMatchObject({ scene: { id: "a" }, local: 3 });
    expect(locate(lesson, 4)).toMatchObject({ scene: { id: "b" }, local: 0 });
    expect(locate(lesson, 99)).toMatchObject({ scene: { id: "b" }, local: 6 });
  });

  it("carries objects from one scene to the next", () => {
    const lesson = buildLesson("L", [scene("a", 4), scene("b", 4, { objects: [] })]);
    const b = lesson.scenes[1]?.compiled;
    expect(b?.objects.map((o) => o.id)).toContain("a_dot");
  });

  it("flags a broken scene instead of hiding it, and keeps the timeline going", () => {
    const lesson = buildLesson("L", [
      scene("a", 4),
      { ...scene("b", 5), objects: [{ id: "x", type: "nope" }] },
      scene("c", 3),
    ]);
    expect(lesson.scenes[1]?.ok).toBe(false);
    expect(lesson.scenes[1]?.issues.some((i) => i.severity === "error")).toBe(true);
    expect(lesson.scenes[2]?.start).toBe(9);
    expect(lesson.scenes[2]?.ok).toBe(true);
  });

  it("reuses compiled scenes when later scenes arrive (progressive playback)", () => {
    const cache: LessonCache = { keys: [], scenes: [] };
    const a = buildLesson("L", [scene("a", 4)], cache);
    const b = buildLesson("L", [scene("a", 4), scene("b", 6)], cache);
    expect(b.scenes[0]).toBe(a.scenes[0]);
    expect(b.scenes[1]?.start).toBe(4);
    expect(b.duration).toBe(10);
    const c = buildLesson("L", [scene("a", 5), scene("b", 6)], cache);
    expect(c.scenes[0]).not.toBe(b.scenes[0]);
    expect(c.scenes[1]?.start).toBe(5);
  });
});

describe("narration audio", () => {
  const twoBeats = scene("a", 6, {
    narration: {
      beats: [
        { id: "b1", text: "One two." },
        { id: "b2", text: "Three." },
      ],
    },
  });
  const narration = {
    provider: "fake",
    beats: [
      { id: "b1", start: 0, audio: "a.mp3", duration: 1.5, words: [] as [number, number][] },
      { id: "b2", start: 3, audio: "b.mp3", duration: 2, words: [] as [number, number][] },
    ],
  };

  it("finds the speech playing at a time, and silence between beats", () => {
    const lesson = buildLesson("L", [twoBeats], undefined, [narration]);
    const s = lesson.scenes[0];
    expect(s?.narration).toBe(narration);
    expect(voiceAt(s, 1)).toEqual({ url: "/api/tts/a.mp3", offset: 1 });
    expect(voiceAt(s, 2)).toBeNull();
    expect(voiceAt(s, 4)).toEqual({ url: "/api/tts/b.mp3", offset: 1 });
    expect(voiceAt(s, 5.5)).toBeNull();
  });

  it("plays a scene spoken in one take from each beat's offset, as one continuous audio", () => {
    const take = {
      provider: "fake",
      beats: [
        { id: "b1", start: 0, audio: "t.mp3", offset: 0, duration: 3, words: [] as [number, number][] },
        { id: "b2", start: 3, audio: "t.mp3", offset: 3, duration: 2, words: [] as [number, number][] },
      ],
    };
    const s = buildLesson("L", [twoBeats], undefined, [take]).scenes[0];
    expect(voiceAt(s, 2.9)).toEqual({ url: "/api/tts/t.mp3", offset: 2.9 });
    expect(voiceAt(s, 3.1)?.url).toBe("/api/tts/t.mp3");
    expect(voiceAt(s, 3.1)?.offset).toBeCloseTo(3.1); // the same audio keeps playing: no restart
    expect(voiceAt(s, 5.5)).toBeNull();
  });

  it("stays silent without narration", () => {
    const lesson = buildLesson("L", [twoBeats]);
    expect(lesson.scenes[0]?.narration).toBeNull();
    expect(voiceAt(lesson.scenes[0], 1)).toBeNull();
  });

  it("rebuilds a cached scene when its narration arrives", () => {
    const cache: LessonCache = { keys: [], scenes: [] };
    const silent = buildLesson("L", [twoBeats], cache);
    const spoken = buildLesson("L", [twoBeats], cache, [narration]);
    expect(silent.scenes[0]?.narration).toBeNull();
    expect(spoken.scenes[0]?.narration).toBe(narration);
  });
});
