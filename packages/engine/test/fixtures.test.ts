/**
 * Every hand-authored fixture lesson must lift, validate and compile cleanly,
 * scene by scene with continuity, with no errors AND no warnings. These are the
 * quality bar the LLM pipeline is measured against, so they must stay spotless.
 */
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { compileScene, lift, type SceneState, sceneEndState } from "../src";

const root = resolve(__dirname, "../../../fixtures/scenes");
const lessons = readdirSync(root).sort();

/** Warnings a fixture triggers on purpose (the engine tour demos a camera zoom that crops). */
const INTENDED: Record<string, string[]> = {
  "engine-tour": ["02-motion.json warning CAMERA_CROPS /objects/note"],
};

describe("fixture lessons", () => {
  for (const slug of lessons) {
    it(`${slug}: every scene compiles with no issues`, () => {
      const files = readdirSync(resolve(root, slug))
        .filter((f) => f.endsWith(".json") && f !== "lesson.json")
        .sort();
      expect(files.length).toBeGreaterThan(0);
      let prev: SceneState | null = null;
      const found: string[] = [];
      files.forEach((file, i) => {
        const l = lift(JSON.parse(readFileSync(resolve(root, slug, file), "utf8")));
        if (!l.ok || !l.scene) {
          found.push(...l.issues.map((x) => `${file} ${x.code} ${x.path}`));
          return;
        }
        const c = compileScene(l.scene, { prev });
        found.push(...c.issues.map((x) => `${file} ${x.severity} ${x.code} ${x.path}`));
        if (c.ok && c.compiled) prev = sceneEndState(c.compiled, prev, i);
      });
      expect(found).toEqual(INTENDED[slug] ?? []);
    });
  }

  it("archimedes has the 12 scenes of Gate A", () => {
    const files = readdirSync(resolve(root, "archimedes")).filter((f) => /^\d\d-.*\.json$/.test(f));
    expect(files).toHaveLength(12);
  });
});
