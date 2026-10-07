/**
 * A generated lesson as it arrives: live progress over SSE and the scenes that are ready, compiled
 * into a playable lesson (progressive playback), plus the scenes still to come. Shared by the lesson
 * page and the classic generation page; the logic is the one the generation page always had.
 */
import type { SceneNarration } from "@animator/components";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, type GenerationSummary, subscribe, TERMINAL } from "../api";
import { formatTime } from "../player/clock";
import type { PendingScene } from "../player/Player";
import { buildLesson, type Lesson, type LessonCache } from "./lesson";

export const STATUS_TEXT: Record<string, string> = {
  planned: "queued",
  generating: "writing the scene",
  validating: "checking",
  correcting: "fixing",
  ready: "ready",
  degraded: "simplified",
  failed: "failed",
};

export function progressLine(g: GenerationSummary): string {
  if (g.status === "queued") return "Waiting to start…";
  if (g.status === "planning") return "Planning the lesson…";
  if (g.status === "complete") {
    const took = g.metrics.generation_s != null ? ` in ${formatTime(g.metrics.generation_s)}` : "";
    return `Lesson ready${took}.`;
  }
  if (g.status === "cancelled") return "Cancelled.";
  if (g.status === "paused") return "Paused.";
  if (g.status === "failed") return "Stopped.";
  const busy = g.scenes.find((s) => !["ready", "degraded", "failed", "planned"].includes(s.status));
  const done = g.scenes.filter((s) => ["ready", "degraded"].includes(s.status)).length;
  const planned = g.chapters.filter((c) => c.storyboarded).length;
  const where =
    planned < g.chapters.length ? ` · chapter ${planned} of ${g.chapters.length}` : ` of ${g.scenes.length}`;
  if (busy) return `Scene ${busy.index + 1}${where}: ${STATUS_TEXT[busy.status] ?? busy.status}…`;
  if (g.scenes.length === 0 || done === g.scenes.length) return "Storyboarding the next chapter…";
  return `Generating (${done} of ${g.scenes.length} scenes ready)…`;
}

export function explain(error: string): string | null {
  if (/API key|OPENAI_API_KEY|deactivated|401/i.test(error))
    return "Check OPENAI_API_KEY in .env, restart the API server, then press Resume.";
  if (/quota/i.test(error)) return "The OpenAI account has no quota left. Add credit, then press Resume.";
  if (/none of the configured models/i.test(error))
    return "None of the configured models is available to this key. Adjust ANIMATOR_MODEL_* or ANIMATOR_MODEL_FALLBACKS in .env.";
  if (/budget/i.test(error)) return "The per-lesson budget was reached (LLM_BUDGET_USD_PER_GENERATION in .env).";
  return null;
}

export interface LessonStream {
  summary: GenerationSummary | null;
  lesson: Lesson;
  pending: PendingScene[];
  running: boolean;
  title: string;
  error: string | null;
  setError: (e: string | null) => void;
}

export function useLessonStream(id: string): LessonStream {
  const [summary, setSummary] = useState<GenerationSummary | null>(null);
  const [scenes, setScenes] = useState<Map<number, unknown>>(new Map());
  const [voices, setVoices] = useState<Map<number, SceneNarration | null>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const cache = useRef<LessonCache>({ keys: [], scenes: [] });

  useEffect(() => {
    setError(null);
    return subscribe(
      id,
      (e) => {
        if (e.type === "snapshot") {
          setSummary(e.generation);
          setScenes(new Map(e.scenes.map((s) => [s.index, s.dsl])));
          setVoices(new Map(e.scenes.map((s) => [s.index, s.narration ?? null])));
        } else if (e.type === "update") {
          setSummary(e.generation);
        } else if (e.type === "scene" && e.dsl) {
          setScenes((prev) => new Map(prev).set(e.index, e.dsl));
          setVoices((prev) => new Map(prev).set(e.index, e.narration ?? null));
        }
      },
      () => {
        api.get(id).catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)));
      },
    );
  }, [id]);

  const title = summary?.title ?? summary?.request.topic ?? "Lesson";
  const lesson = useMemo(() => {
    const ready: unknown[] = [];
    const spoken: (SceneNarration | null)[] = [];
    for (let i = 0; scenes.has(i); i++) {
      ready.push(scenes.get(i));
      spoken.push(voices.get(i) ?? null);
    }
    return buildLesson(title, ready, cache.current, spoken);
  }, [scenes, voices, title]);

  const pending: PendingScene[] = useMemo(() => {
    if (!summary) return [];
    const out: PendingScene[] = summary.scenes
      .filter((s) => s.index >= lesson.scenes.length)
      .map((s) => ({ key: s.id, title: s.title, duration: s.duration, status: s.status }));
    if (!TERMINAL.includes(summary.status)) {
      const later = summary.chapters.filter((c) => !c.storyboarded);
      const known = summary.scenes.reduce((a, s) => a + s.duration, 0);
      const left = Math.max(0, summary.planned_duration_s - known);
      for (const c of later)
        out.push({
          key: `chapter:${c.id}`,
          title: `Chapter: ${c.title}`,
          duration: later.length ? left / later.length : 0,
          status: "chapter",
        });
    }
    return out;
  }, [summary, lesson.scenes.length]);

  const running = summary != null && !TERMINAL.includes(summary.status) && summary.status !== "paused";
  return { summary, lesson, pending, running, title, error, setError };
}
