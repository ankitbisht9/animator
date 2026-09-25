/**
 * A lesson is an ordered list of scenes compiled with the same engine code the
 * worker runs. Each scene starts from the previous scene's engine-computed end
 * state (continuity), and playback maps a global time onto (scene, local time).
 */
import {
  type CompiledScene,
  compileScene,
  lift,
  type SceneState,
  sceneEndState,
  type ValidationIssue,
} from "@animator/engine";

export interface LessonScene {
  index: number;
  id: string;
  title: string;
  /** Global start time in seconds. */
  start: number;
  duration: number;
  ok: boolean;
  compiled: CompiledScene | null;
  issues: ValidationIssue[];
  /** Hover descriptions/values need the compiled object's bound param names. */
  endState: SceneState | null;
}

export interface Lesson {
  title: string;
  scenes: LessonScene[];
  duration: number;
}

/** Fallback shown in place of a scene that failed validation (never silently). */
function degradedDuration(raw: unknown): number {
  const d = (raw as { duration?: unknown })?.duration;
  return typeof d === "number" && d > 0 && d <= 120 ? d : 5;
}

export function buildLesson(title: string, rawScenes: unknown[]): Lesson {
  const scenes: LessonScene[] = [];
  let prev: SceneState | null = null;
  let start = 0;
  rawScenes.forEach((raw, index) => {
    const lifted = lift(raw);
    let compiled: CompiledScene | null = null;
    let issues: ValidationIssue[] = lifted.issues;
    let endState: SceneState | null = null;
    let ok = false;
    if (lifted.ok && lifted.scene) {
      const res = compileScene(lifted.scene, { prev });
      issues = res.issues;
      ok = res.ok;
      if (res.ok && res.compiled) {
        compiled = res.compiled;
        endState = sceneEndState(res.compiled, prev, index);
        prev = endState;
      }
    }
    const duration = compiled?.duration ?? degradedDuration(raw);
    const id = compiled?.id ?? String((raw as { id?: unknown })?.id ?? `scene_${index + 1}`);
    const sceneTitle = compiled?.title ?? String((raw as { title?: unknown })?.title ?? `Scene ${index + 1}`);
    scenes.push({ index, id, title: sceneTitle, start, duration, ok, compiled, issues, endState });
    start += duration;
  });
  return { title, scenes, duration: start };
}

export function locate(lesson: Lesson, t: number): { scene: LessonScene; local: number } | null {
  if (lesson.scenes.length === 0) return null;
  const tt = Math.max(0, Math.min(t, lesson.duration));
  for (const scene of lesson.scenes) {
    if (tt < scene.start + scene.duration) return { scene, local: tt - scene.start };
  }
  const last = lesson.scenes[lesson.scenes.length - 1] as LessonScene;
  return { scene: last, local: last.duration };
}
