import { useEffect, useMemo, useState } from "react";
import { type ApiConfig, ApiError, api, type GenerationSummary } from "../api";
import { FIXTURE_LESSONS } from "../lesson/fixtures";
import { buildLesson } from "../lesson/lesson";
import { formatTime } from "../player/clock";
import { Link, navigate } from "../router";

const AUDIENCES = [
  "primary-school child",
  "middle-school student",
  "high-school student",
  "university student",
  "curious adult",
];
const DURATIONS = [1, 2, 3, 5, 8, 10, 15];
const EXAMPLES = [
  "Explain Archimedes' principle",
  "How does photosynthesis work?",
  "Explain the Pythagorean theorem",
  "Why does a pendulum swing with a steady rhythm?",
  "How does binary search find a number so fast?",
];

const STATUS_LABEL: Record<string, string> = {
  queued: "queued",
  planning: "planning",
  generating: "generating",
  complete: "ready",
  failed: "failed",
  cancelled: "cancelled",
  paused: "paused",
};

export function Home() {
  const lessons = useMemo(
    () => FIXTURE_LESSONS.map((f) => ({ f, duration: buildLesson(f.meta.title, f.scenes).duration })),
    [],
  );
  const [topic, setTopic] = useState("");
  const [audience, setAudience] = useState("high-school student");
  const [minutes, setMinutes] = useState(3);
  const [style, setStyle] = useState("");
  const [paced, setPaced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<ApiConfig | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [recent, setRecent] = useState<GenerationSummary[]>([]);

  useEffect(() => {
    api.config().then(setConfig, () => setApiDown(true));
    api.list().then(setRecent, () => undefined);
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (topic.trim().length < 3) {
      setError("Type a topic or question first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const gen = await api.create({
        topic: topic.trim(),
        audience,
        duration_min: minutes,
        style: style || null,
        paced,
      });
      navigate(`/g/${gen.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="page narrow">
      <section className="hero">
        <div className="kicker">AI-directed 2D explainers</div>
        <h1>
          Understand anything, <span className="accent">one clear picture at a time.</span>
        </h1>
        <p className="lead">
          Ask for a topic and an animated lesson is planned by an AI director and drawn by a deterministic animation
          engine: labelled diagrams, real physics, equations that build step by step, and quick questions to keep you
          thinking.
        </p>
        <form className="create-form" onSubmit={submit}>
          <input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="What do you want to understand? e.g. Explain Archimedes' principle"
            maxLength={500}
            aria-label="Topic"
          />
          <div className="create-options">
            <label>
              For
              <select value={audience} onChange={(e) => setAudience(e.target.value)}>
                {AUDIENCES.map((a) => (
                  <option key={a}>{a}</option>
                ))}
              </select>
            </label>
            <label>
              Length
              <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
                {DURATIONS.map((d) => (
                  <option key={d} value={d}>
                    {d} min
                  </option>
                ))}
              </select>
            </label>
            <label className="grow">
              Style notes
              <input
                value={style}
                onChange={(e) => setStyle(e.target.value)}
                placeholder="optional, e.g. use a football example"
                maxLength={300}
              />
            </label>
          </div>
          <div className="create-row">
            <label className="check">
              <input type="checkbox" checked={paced} onChange={(e) => setPaced(e.target.checked)} />
              Save cost: only generate a little ahead of what I watch
            </label>
            <button type="submit" className="btn primary" disabled={busy || apiDown}>
              {busy ? "Starting…" : "Create lesson"}
            </button>
          </div>
          <div className="examples">
            Try:{" "}
            {EXAMPLES.map((x) => (
              <button type="button" key={x} className="chip" onClick={() => setTopic(x)}>
                {x}
              </button>
            ))}
          </div>
        </form>
        {error && <p className="note bad">{error}</p>}
        {apiDown && <p className="note bad">The API server is not running. Start everything with `make dev`.</p>}
        {config && !config.openai_key_configured && (
          <p className="note bad">
            OPENAI_API_KEY is not set in .env: lesson generation needs it. Restart the API after adding it.
          </p>
        )}
        {config?.openai_key_configured && (
          <p className="note">
            Scenes appear as soon as they are ready; the first one takes a minute or two. Planner model:{" "}
            <code>{config.stages.animation_planner?.models.join(" → ")}</code>. Budget $
            {config.budget_usd_per_generation} per lesson.
          </p>
        )}
      </section>

      {recent.length > 0 && (
        <section>
          <h2>Your lessons</h2>
          <div className="cards">
            {recent.map((g) => (
              <Link key={g.id} href={`/g/${g.id}`} className="card">
                <div className="card-kicker">
                  <span className={`status-chip ${g.status}`}>{STATUS_LABEL[g.status] ?? g.status}</span>{" "}
                  {g.request.audience} · {g.request.duration_min} min · {g.scenes.length} scenes
                </div>
                <div className="card-title">{g.title ?? g.request.topic}</div>
                <div className="card-desc">{g.error ?? g.subtitle ?? g.request.topic}</div>
              </Link>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2>Hand-authored lessons</h2>
        <div className="cards">
          {lessons.map(({ f, duration }) => (
            <Link key={f.slug} href={`/lesson/${f.slug}`} className="card">
              <div className="card-kicker">
                {f.meta.audience ?? "Lesson"} · {formatTime(duration)} · {f.scenes.length} scenes
              </div>
              <div className="card-title">{f.meta.title}</div>
              {f.meta.description && <div className="card-desc">{f.meta.description}</div>}
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
