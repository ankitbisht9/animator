/**
 * The only silent repairs the engine makes (each reported as a `normalization`
 * issue): clamp opacities to [0, 1], sort keyframes by time, and trim times to
 * 3 decimals. Anything structural or semantic goes back to the LLM.
 */
import type { SceneT } from "../dsl/scene";
import { roundTo } from "../math/num";
import { issue, type ValidationIssue } from "./issues";

const note = (path: string, message: string) =>
  issue("NORMALIZED", path, message, { severity: "info", source: "normalization" });

export function normalizeScene(input: SceneT): { scene: SceneT; issues: ValidationIssue[] } {
  const scene = structuredClone(input);
  const issues: ValidationIssue[] = [];

  const trimTime = (v: number, path: string): number => {
    const r = roundTo(v, 3);
    if (r !== v) issues.push(note(path, `Trimmed time ${v} to ${r}.`));
    return r;
  };

  scene.duration = trimTime(scene.duration, "/duration");
  scene.objects.forEach((o, i) => {
    if (o.opacity < 0 || o.opacity > 1) {
      const c = Math.min(1, Math.max(0, o.opacity));
      issues.push(note(`/objects/${i}/opacity`, `Clamped opacity ${o.opacity} to ${c}.`));
      o.opacity = c;
    }
  });
  scene.cues.forEach((c, i) => {
    if (typeof c.at === "number") c.at = trimTime(c.at, `/cues/${i}/at`);
  });
  scene.narration.beats.forEach((b, i) => {
    if (typeof b.at === "number") b.at = trimTime(b.at, `/narration/beats/${i}/at`);
  });
  scene.timeline.forEach((a, i) => {
    if (a.duration !== undefined) a.duration = trimTime(a.duration, `/timeline/${i}/duration`);
    if (a.start) {
      if (typeof a.start.at === "number") a.start.at = trimTime(a.start.at, `/timeline/${i}/start/at`);
      if (a.start.delay !== undefined) a.start.delay = trimTime(a.start.delay, `/timeline/${i}/start/delay`);
    }
    if (a.action === "animate") {
      a.keyframes.forEach((k, j) => {
        k.time = trimTime(k.time, `/timeline/${i}/keyframes/${j}/time`);
      });
      const sorted = [...a.keyframes].sort((x, y) => x.time - y.time);
      if (sorted.some((k, j) => k !== a.keyframes[j])) {
        issues.push(note(`/timeline/${i}/keyframes`, "Sorted keyframes by time."));
        a.keyframes = sorted;
      }
    }
  });
  return { scene, issues };
}
