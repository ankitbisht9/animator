/**
 * A generated lesson: live progress over SSE, and the player playing scenes as soon as they are
 * ready (progressive playback). Each scene's DSL is compiled here by the same engine the
 * worker ran; nothing is pre-rendered.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, type ExportJob, type GenerationSummary, subscribe, TERMINAL } from "../api";
import { buildLesson, type LessonCache } from "../lesson/lesson";
import { formatTime } from "../player/clock";
import { type PendingScene, Player } from "../player/Player";
import { Link } from "../router";

const STATUS_TEXT: Record<string, string> = {
  planned: "queued",
  generating: "writing the scene",
  validating: "checking",
  correcting: "fixing",
  ready: "ready",
  degraded: "simplified",
  failed: "failed",
};

function progressLine(g: GenerationSummary): string {
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

function explain(error: string): string | null {
  if (/API key|OPENAI_API_KEY|deactivated|401/i.test(error))
    return "Check OPENAI_API_KEY in .env, restart the API server, then press Resume.";
  if (/quota/i.test(error)) return "The OpenAI account has no quota left. Add credit, then press Resume.";
  if (/none of the configured models/i.test(error))
    return "None of the configured models is available to this key. Adjust ANIMATOR_MODEL_* or ANIMATOR_MODEL_FALLBACKS in .env.";
  if (/budget/i.test(error)) return "The per-lesson budget was reached (LLM_BUDGET_USD_PER_GENERATION in .env).";
  return null;
}

export function GenerationPage({ id }: { id: string }) {
  const [summary, setSummary] = useState<GenerationSummary | null>(null);
  const [scenes, setScenes] = useState<Map<number, unknown>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [exportJob, setExportJob] = useState<ExportJob | null>(null);
  const cache = useRef<LessonCache>({ keys: [], scenes: [] });

  useEffect(() => {
    setError(null);
    return subscribe(
      id,
      (e) => {
        if (e.type === "snapshot") {
          setSummary(e.generation);
          setScenes(new Map(e.scenes.map((s) => [s.index, s.dsl])));
        } else if (e.type === "update") {
          setSummary(e.generation);
        } else if (e.type === "scene" && e.dsl) {
          setScenes((prev) => new Map(prev).set(e.index, e.dsl));
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
    for (let i = 0; scenes.has(i); i++) ready.push(scenes.get(i));
    return buildLesson(title, ready, cache.current);
  }, [scenes, title]);

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
  const onTime = useCallback((t: number) => void api.playhead(id, t).catch(() => undefined), [id]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (exportJob?.state !== "running") return;
    const h = window.setInterval(() => {
      api.exportStatus(id).then(setExportJob, () => undefined);
    }, 2000);
    return () => window.clearInterval(h);
  }, [exportJob?.state, id]);

  if (!summary) {
    return (
      <div className="page narrow">
        {error ? (
          <>
            <h1>Lesson not available</h1>
            <p className="lead">{error}</p>
            <Link href="/">Back</Link>
          </>
        ) : (
          <p className="lead">Connecting…</p>
        )}
      </div>
    );
  }

  const hint = summary.error ? explain(summary.error) : null;
  return (
    <div className="page wide">
      <div className="lesson-head">
        <div>
          <div className="kicker">
            {summary.request.audience} · {summary.request.duration_min} min · AI-generated
          </div>
          <h1>{title}</h1>
          {summary.subtitle && <p className="lead">{summary.subtitle}</p>}
        </div>
      </div>

      <div className={`gen-status ${summary.status}`}>
        <div className="gen-line">
          {running && <span className="spinner small" aria-hidden="true" />}
          <b>{progressLine(summary)}</b>
          {summary.metrics.first_play_s != null && (
            <span className="gen-meta">first scene after {Math.round(summary.metrics.first_play_s)} s</span>
          )}
          <span className="gen-meta">
            {summary.metrics.llm_calls} AI calls · ${summary.metrics.cost_usd.toFixed(2)}
            {summary.metrics.corrections > 0 && ` · ${summary.metrics.corrections} fixes`}
            {summary.metrics.degraded > 0 && ` · ${summary.metrics.degraded} simplified`}
          </span>
          <span className="gen-actions">
            {running && (
              <button type="button" className="btn" disabled={busy} onClick={() => act(() => api.cancel(id))}>
                Cancel
              </button>
            )}
            {(summary.status === "paused" || (summary.status === "failed" && !/budget/i.test(summary.error ?? ""))) && (
              <button type="button" className="btn primary" disabled={busy} onClick={() => act(() => api.resume(id))}>
                Resume
              </button>
            )}
            {summary.status === "complete" && (
              <button
                type="button"
                className="btn"
                disabled={busy || exportJob?.state === "running"}
                onClick={() => act(async () => setExportJob(await api.startExport(id)))}
              >
                {exportJob?.state === "running" ? "Exporting…" : "Export MP4"}
              </button>
            )}
            <Link href={`/debug/${id}`} className="btn ghost">
              Debug
            </Link>
          </span>
        </div>
        {summary.error && (
          <div className="gen-error">
            {summary.error}
            {hint && <div className="gen-hint">{hint}</div>}
          </div>
        )}
        {error && <div className="gen-error">{error}</div>}
        {exportJob && (
          <div className="gen-export">
            {exportJob.state === "running" && <>Rendering video… {exportJob.log.at(-1)}</>}
            {exportJob.state === "done" && (
              <a href={`/api/generations/${id}/export/video`} download>
                Download the MP4
              </a>
            )}
            {exportJob.state === "failed" && <>Export failed: {exportJob.error}</>}
          </div>
        )}
      </div>

      <Player lesson={lesson} pending={pending} generating={running} onTime={running ? onTime : undefined} />
    </div>
  );
}
