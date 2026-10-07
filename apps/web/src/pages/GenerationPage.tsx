/**
 * A generated lesson: live progress over SSE, and the player playing scenes as soon as they are
 * ready (progressive playback). Each scene's DSL is compiled here by the same engine the
 * worker ran; nothing is pre-rendered.
 */
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type ExportJob } from "../api";
import { DoubtFeature } from "../doubts/DoubtFeature";
import { explain, progressLine, useLessonStream } from "../lesson/useLessonStream";
import { Player } from "../player/Player";
import { Link } from "../router";

export function GenerationPage({ id }: { id: string }) {
  const { summary, lesson, pending, running, title, error, setError } = useLessonStream(id);
  const [busy, setBusy] = useState(false);
  const [exportJob, setExportJob] = useState<ExportJob | null>(null);

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
            {summary.request.audience} · {summary.request.duration_min} min
            {summary.request.language &&
              summary.request.language !== "English" &&
              ` · spoken in ${summary.request.language}`}{" "}
            · AI-generated
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
        {(summary.metrics.tts_failures ?? 0) > 0 && (
          <div className="gen-error">
            Voice: {summary.metrics.tts_failures} scene(s) have captions only.
            {summary.metrics.tts_error && <div className="gen-hint">{summary.metrics.tts_error}</div>}
          </div>
        )}
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

      <Player
        lesson={lesson}
        pending={pending}
        generating={running}
        onTime={running ? onTime : undefined}
        addon={(control) => (
          <DoubtFeature
            control={control}
            lessonId={id}
            language={summary.request.language || "English"}
            audience={summary.request.audience}
            durationMin={summary.request.duration_min}
          />
        )}
      />
    </div>
  );
}
