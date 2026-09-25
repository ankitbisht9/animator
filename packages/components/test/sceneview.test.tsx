import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { compileScene, componentTypes, evaluate, lift, type SceneState, sceneEndState } from "@animator/engine";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RENDERERS, SceneView } from "../src";

const FIXTURES = resolve(__dirname, "../../../fixtures/scenes");

function lessonScenes(slug: string): unknown[] {
  const dir = resolve(FIXTURES, slug);
  return readdirSync(dir)
    .filter((f) => f.endsWith(".json") && f !== "lesson.json")
    .sort()
    .map((f) => JSON.parse(readFileSync(resolve(dir, f), "utf8")));
}

function compileLesson(slug: string) {
  let prev: SceneState | null = null;
  return lessonScenes(slug).map((raw, i) => {
    const l = lift(raw);
    if (!l.ok || !l.scene) throw new Error(JSON.stringify(l.issues));
    const c = compileScene(l.scene, { prev });
    if (!c.ok || !c.compiled) throw new Error(JSON.stringify(c.issues.filter((x) => x.severity === "error")));
    prev = sceneEndState(c.compiled, prev, i);
    return c.compiled;
  });
}

describe("renderer registry", () => {
  it("has a renderer for every catalog component and nothing else", () => {
    expect(Object.keys(RENDERERS).sort()).toEqual(componentTypes().sort());
  });
});

describe("SceneView golden SVG frames", () => {
  const scenes = compileLesson("engine-tour");
  const times = [0.5, 2, 3.5, 5, 7.9];
  for (const cs of scenes) {
    for (const t of times.filter((x) => x <= cs.duration)) {
      it(`${cs.id} @ ${t}s`, async () => {
        const svg = renderToStaticMarkup(<SceneView compiled={cs} frame={evaluate(cs, t)} />);
        expect(svg.startsWith("<svg")).toBe(true);
        await expect(svg).toMatchFileSnapshot(`./__snapshots__/${cs.id}-${t}.svg`);
      });
    }
  }

  it("never emits script, foreignObject or external references", () => {
    for (const cs of scenes) {
      for (const t of times) {
        const svg = renderToStaticMarkup(<SceneView compiled={cs} frame={evaluate(cs, t)} />);
        expect(svg).not.toMatch(/<script|foreignObject|href="http|javascript:/i);
      }
    }
  });

  it("renders identically on repeated evaluation (determinism)", () => {
    const cs = scenes[0];
    if (!cs) throw new Error("no scene");
    const a = renderToStaticMarkup(<SceneView compiled={cs} frame={evaluate(cs, 3.3)} />);
    const b = renderToStaticMarkup(<SceneView compiled={cs} frame={evaluate(cs, 3.3)} />);
    expect(a).toBe(b);
  });
});
