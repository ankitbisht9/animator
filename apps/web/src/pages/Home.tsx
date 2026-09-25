import { useMemo } from "react";
import { FIXTURE_LESSONS } from "../lesson/fixtures";
import { buildLesson } from "../lesson/lesson";
import { formatTime } from "../player/clock";
import { Link } from "../router";

export function Home() {
  const lessons = useMemo(
    () => FIXTURE_LESSONS.map((f) => ({ f, duration: buildLesson(f.meta.title, f.scenes).duration })),
    [],
  );
  return (
    <div className="page narrow">
      <section className="hero">
        <div className="kicker">AI-directed 2D explainers</div>
        <h1>
          Understand anything, <span className="accent">one clear picture at a time.</span>
        </h1>
        <p className="lead">
          Ask for a topic and an animated, narrated-style lesson is planned by an AI director and drawn by a
          deterministic animation engine: labelled diagrams, real physics, equations that build step by step.
        </p>
        <form className="create" onSubmit={(e) => e.preventDefault()}>
          <input disabled placeholder="Explain Archimedes' principle to a high-school student in 5 minutes" />
          <button type="submit" className="btn primary" disabled title="AI generation is built in Phase 7">
            Create lesson
          </button>
        </form>
        <p className="note">
          AI lesson generation is not connected yet (build Phase 7). The lessons below are hand-authored in the scene
          DSL and rendered by the same engine the AI will drive.
        </p>
      </section>
      <section>
        <h2>Lessons</h2>
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
