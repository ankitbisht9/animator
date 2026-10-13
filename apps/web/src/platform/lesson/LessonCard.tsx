/** A lesson in a grid or a list: cover, subject, title, meta, progress, and the learner's actions. */
import { Bookmark, BookmarkCheck, Clock, Eye, Globe, Link2, Star } from "lucide-react";
import type { Lesson } from "../api";
import { minutes } from "../format";
import { Link } from "../nav";
import { audienceLabel, LessonCover, StatusBadge, subject } from "../ui/brand";
import { ProgressBar } from "../ui/core";
import { TrialBadge } from "./TrialBadge";

export function lessonLength(l: Lesson): string {
  return minutes(l.length_s > 0 ? l.length_s : l.duration_min * 60);
}

export function LessonCard({
  lesson,
  onToggleSave,
  showOwner = false,
  showStatus = true,
}: {
  lesson: Lesson;
  onToggleSave?: (l: Lesson) => void;
  showOwner?: boolean;
  showStatus?: boolean;
}) {
  const s = subject(lesson.subject);
  const p = lesson.progress;
  return (
    <article className="lesson-card">
      <Link href={`/lessons/${lesson.id}`} className="lesson-card-link" aria-label={lesson.title}>
        <LessonCover lesson={lesson} />
        {showStatus && lesson.status !== "complete" && (
          <div className="lesson-card-status">
            <StatusBadge status={lesson.status} />
          </div>
        )}
        {p && (p.percent > 0 || p.completed) && (
          <div className="lesson-card-progress">
            <ProgressBar value={p.completed ? 1 : p.percent} tone={p.completed ? "success" : "brand"} label="Watched" />
          </div>
        )}
      </Link>
      <div className="lesson-card-body">
        <div className="lesson-card-top">
          <span className="lesson-card-subject" style={{ color: s.ink }}>
            <s.icon size={13} />
            {s.label}
          </span>
          <TrialBadge model={lesson.trial_model} />
          {lesson.visibility !== "private" && lesson.mine && (
            <span
              className="lesson-card-shared"
              title={lesson.visibility === "public" ? "Public in Explore" : "Shared by link"}
            >
              {lesson.visibility === "public" ? <Globe size={13} /> : <Link2 size={13} />}
            </span>
          )}
        </div>
        <h3>
          <Link href={`/lessons/${lesson.id}`}>{lesson.title}</Link>
        </h3>
        <div className="lesson-card-meta">
          <span>
            <Clock size={13} />
            {lessonLength(lesson)}
          </span>
          <span>{audienceLabel(lesson.audience)}</span>
          {lesson.language !== "English" && <span>{lesson.language}</span>}
          {lesson.rating != null && (
            <span className="lesson-card-rating">
              <Star size={13} />
              {lesson.rating.toFixed(1)}
            </span>
          )}
          {showOwner && lesson.views > 0 && (
            <span>
              <Eye size={13} />
              {lesson.views}
            </span>
          )}
        </div>
        <div className="lesson-card-foot">
          {showOwner && lesson.owner ? <span className="lesson-card-owner">by {lesson.owner.name}</span> : <span />}
          {p?.completed ? (
            <span className="lesson-card-done">Completed</span>
          ) : p && p.percent > 0 ? (
            <span className="lesson-card-done going">{Math.round(p.percent * 100)}% watched</span>
          ) : null}
          {onToggleSave && (
            <button
              type="button"
              className={`lesson-card-save ${lesson.saved ? "on" : ""}`}
              aria-pressed={lesson.saved}
              aria-label={lesson.saved ? "Remove from library" : "Save to library"}
              title={lesson.saved ? "Saved" : "Save"}
              onClick={() => onToggleSave(lesson)}
            >
              {lesson.saved ? <BookmarkCheck size={17} /> : <Bookmark size={17} />}
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

export function LessonCardSkeleton() {
  return (
    <div className="lesson-card skeleton">
      <div className="lesson-cover ui-skeleton" />
      <div className="lesson-card-body">
        <span className="ui-skeleton" style={{ width: "40%", height: 12 }} />
        <span className="ui-skeleton" style={{ width: "90%", height: 18, marginTop: 10 }} />
        <span className="ui-skeleton" style={{ width: "60%", height: 12, marginTop: 10 }} />
      </div>
    </div>
  );
}
