import { useMemo } from "react";
import { fixtureLesson } from "../lesson/fixtures";
import { buildLesson } from "../lesson/lesson";
import { Player } from "../player/Player";
import { Link } from "../router";

export function LessonPage({ slug }: { slug: string }) {
  const fixture = fixtureLesson(slug);
  const lesson = useMemo(() => (fixture ? buildLesson(fixture.meta.title, fixture.scenes) : null), [fixture]);
  if (!fixture || !lesson) {
    return (
      <div className="page narrow">
        <h1>Lesson not found</h1>
        <Link href="/">Back to lessons</Link>
      </div>
    );
  }
  const errors = lesson.scenes.flatMap((s) =>
    s.issues.filter((i) => i.severity === "error").map((i) => ({ scene: s.id, ...i })),
  );
  return (
    <div className="page wide">
      <div className="lesson-head">
        <div>
          <div className="kicker">{fixture.meta.audience ?? "Lesson"}</div>
          <h1>{fixture.meta.title}</h1>
          {fixture.meta.description && <p className="lead">{fixture.meta.description}</p>}
        </div>
      </div>
      <Player lesson={lesson} />
      {errors.length > 0 && (
        <details className="issues-panel" open>
          <summary>{errors.length} validation error(s): affected scenes are shown as flagged placeholders</summary>
          <ul>
            {errors.map((e) => (
              <li key={`${e.scene}${e.path}${e.code}`}>
                <b>{e.scene}</b> <code>{e.code}</code> {e.path} {e.message}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
