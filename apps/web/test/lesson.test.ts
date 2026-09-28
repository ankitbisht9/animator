import { describe, expect, it } from "vitest";
import { buildLesson, type LessonCache, locate } from "../src/lesson/lesson";

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
