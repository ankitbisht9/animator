/**
 * Headless render target for export and the visual critic.
 *
 *   await window.__loadLesson({ title, scenes })   -> summary
 *   await window.__renderFrame(frame, fps)          -> painted frame at t = frame / fps
 *   await window.__renderAt(sceneIndex, localT)     -> painted frame at a scene-local time
 *
 * Every captured frame mounts a fresh <SceneView> subtree (key = frame) so paint
 * output never depends on the previously painted frame (ADR-006).
 */
import { SceneView } from "@animator/components";
import { DSL_VERSION, ENGINE_VERSION, evaluate } from "@animator/engine";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fixtureLesson } from "../lesson/fixtures";
import { buildLesson, type Lesson, locate } from "../lesson/lesson";

interface RenderState {
  lesson: Lesson;
  /** Global time (frame export) ... */
  t: number;
  /** ... or an explicit scene and local time (keyframes: a scene's end is not the next scene's start). */
  at?: { scene: number; local: number };
  key: string;
}

declare global {
  interface Window {
    __loadLesson?: (payload: { title?: string; scenes: unknown[] }) => unknown;
    __renderFrame?: (frame: number, fps?: number) => Promise<void>;
    __renderAt?: (sceneIndex: number, localT: number) => Promise<void>;
    __ready?: boolean;
  }
}

function summary(lesson: Lesson) {
  return {
    title: lesson.title,
    engineVersion: ENGINE_VERSION,
    dslVersion: DSL_VERSION,
    duration: lesson.duration,
    scenes: lesson.scenes.map((s) => ({
      id: s.id,
      title: s.title,
      start: s.start,
      duration: s.duration,
      ok: s.ok,
      themeId: s.compiled?.themeId ?? null,
      beats: s.compiled?.beats ?? [],
      errors: s.issues.filter((i) => i.severity === "error"),
    })),
  };
}

export function RenderPage() {
  const [state, setState] = useState<RenderState | null>(null);
  const pending = useRef<(() => void) | null>(null);

  useEffect(() => {
    const load = (payload: { title?: string; scenes: unknown[] }) => {
      const lesson = buildLesson(payload.title ?? "Lesson", payload.scenes);
      setState({ lesson, t: 0, key: "0" });
      return summary(lesson);
    };
    window.__loadLesson = load;
    const paint = (update: Partial<RenderState>) =>
      new Promise<void>((resolve) => {
        pending.current = resolve;
        setState((s) => (s ? { ...s, at: undefined, ...update } : s));
      });
    window.__renderFrame = (frame, fps = 30) => paint({ t: frame / fps, key: `f${frame}` });
    window.__renderAt = (sceneIndex, localT) =>
      paint({ at: { scene: sceneIndex, local: localT }, key: `s${sceneIndex}-${localT}` });
    const slug = new URLSearchParams(window.location.search).get("lesson");
    const fixture = slug ? fixtureLesson(slug) : undefined;
    if (fixture) load({ title: fixture.meta.title, scenes: fixture.scenes });
    window.__ready = true;
  }, []);

  // Resolve the pending paint after React committed and the browser painted twice.
  useLayoutEffect(() => {
    if (!pending.current) return;
    const resolve = pending.current;
    pending.current = null;
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });

  if (!state) return <div className="render-root" />;
  const direct = state.at ? state.lesson.scenes[state.at.scene] : undefined;
  const loc =
    direct && state.at
      ? { scene: direct, local: Math.min(state.at.local, direct.duration) }
      : locate(state.lesson, state.t);
  const compiled = loc?.scene.compiled;
  return (
    <div className="render-root">
      {compiled && loc ? (
        <SceneView
          key={state.key}
          compiled={compiled}
          frame={evaluate(compiled, loc.local)}
          width="100%"
          height="100%"
        />
      ) : (
        <div className="render-degraded">{loc?.scene.title ?? ""}</div>
      )}
    </div>
  );
}
