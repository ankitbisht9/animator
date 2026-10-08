/** Pieces of the lesson page that ride along with the player: progress saving, "continue from",
 * the lesson-complete card, generation progress, star ratings. */
import { CircleCheck, Compass, PenLine, Play, RotateCcw, Star, Trophy, X } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { GenerationSummary } from "../../api";
import type { PlayerControl } from "../../player/Player";
import { v1 } from "../api";
import { clock, today } from "../format";
import { invalidate } from "../hooks";
import { navigate } from "../nav";
import { Button, ProgressBar } from "../ui/core";
import { useToast } from "../ui/overlay";

const FLUSH_EVERY_S = 12;
const LOOK_EVERY_MS = 250;

/**
 * The platform's pieces on the stage (progress saving, "continue from", the lesson-complete card), kept
 * out of the player's per-frame render: they look at the playhead four times a second through a ref
 * and draw into the stage through a portal. Only the doubt add-on renders with every frame, as before.
 */
export function StageExtras({
  controlRef,
  host,
  children,
}: {
  controlRef: RefObject<PlayerControl | null>;
  host: RefObject<HTMLElement | null>;
  children: (control: PlayerControl) => ReactNode;
}) {
  const [stage, setStage] = useState<HTMLElement | null>(null);
  const [, setTick] = useState(0);
  useEffect(() => {
    const look = () => {
      const el = host.current?.querySelector<HTMLElement>(".stage") ?? null;
      setStage((s) => (s === el ? s : el));
      setTick((n) => n + 1);
    };
    look();
    const h = window.setInterval(look, LOOK_EVERY_MS);
    return () => window.clearInterval(h);
  }, [host]);
  const control = controlRef.current;
  if (!control || !stage) return null;
  return createPortal(children(control), stage);
}

/** Saves where the learner is and how long they watched (every few seconds of playback, on pause,
 * when the tab hides and when they leave). */
export function ProgressTracker({
  control,
  lessonId,
  total,
}: {
  control: PlayerControl;
  lessonId: string;
  total: number;
}) {
  const toast = useToast();
  const ctl = useRef(control);
  ctl.current = control;
  const totalRef = useRef(total);
  totalRef.current = total;

  useEffect(() => {
    let watched = 0;
    let sinceFlush = 0;
    let wasPlaying = false;
    let lastSent = -1;
    const body = () => ({
      position_s: Math.max(0, ctl.current.t),
      duration_s: Math.max(totalRef.current, ctl.current.duration, 1),
      delta_s: Math.round(watched * 10) / 10,
      day: today(),
    });
    const flush = (leaving = false) => {
      const b = body();
      if (watched < 0.5 && Math.abs(b.position_s - lastSent) < 1) return;
      watched = 0;
      sinceFlush = 0;
      lastSent = b.position_s;
      if (leaving) {
        void fetch(`/api/v1/lessons/${lessonId}/progress`, {
          method: "PUT",
          keepalive: true,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(b),
        }).catch(() => undefined);
        return;
      }
      v1.progress(lessonId, b)
        .then((r) => {
          if (r.newly_completed) {
            toast.success("Lesson complete! It is in your progress now.");
            invalidate("stats:");
            invalidate("learning");
          }
        })
        .catch(() => undefined);
    };
    const tick = window.setInterval(() => {
      const playing = ctl.current.playing;
      if (playing) {
        watched += 1;
        sinceFlush += 1;
        if (sinceFlush >= FLUSH_EVERY_S) flush();
      } else if (wasPlaying) {
        flush();
      }
      wasPlaying = playing;
    }, 1000);
    const onHide = () => {
      if (document.visibilityState === "hidden") flush(true);
    };
    document.addEventListener("visibilitychange", onHide);
    return () => {
      window.clearInterval(tick);
      document.removeEventListener("visibilitychange", onHide);
      flush(true);
    };
  }, [lessonId, toast]);
  return null;
}

/** "Continue from 2:31" when the learner had started this lesson before. */
export function ResumePrompt({ control, position }: { control: PlayerControl; position: number }) {
  const [shown, setShown] = useState(true);
  useEffect(() => {
    const h = window.setTimeout(() => setShown(false), 15000);
    return () => window.clearTimeout(h);
  }, []);
  if (!shown || control.t > 2 || control.playing || position < 5 || control.duration < position + 1) return null;
  return (
    <div className="resume-pill" role="status">
      <span>
        Welcome back. Continue from <b>{clock(position)}</b>?
      </span>
      <Button
        variant="primary"
        size="sm"
        icon={Play}
        onClick={() => {
          control.seek(position);
          control.play();
          setShown(false);
        }}
      >
        Continue
      </Button>
      <button
        type="button"
        className="ui-icon-btn"
        aria-label="Start from the beginning"
        onClick={() => setShown(false)}
      >
        <X size={16} />
      </button>
    </div>
  );
}

export function StarRating({
  value,
  onChange,
  size = 22,
  readOnly = false,
}: {
  value: number;
  onChange?: (v: number) => void;
  size?: number;
  readOnly?: boolean;
}) {
  const [hover, setHover] = useState(0);
  const shown = hover || value;
  if (readOnly)
    return (
      <span className="stars" role="img" aria-label={`${value} of 5 stars`}>
        {[1, 2, 3, 4, 5].map((n) => (
          <Star key={n} size={size} className={n <= value ? "on" : ""} />
        ))}
      </span>
    );
  return (
    <fieldset className="stars">
      <legend className="sr-only">Rate this lesson</legend>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          aria-pressed={value === n}
          aria-label={`Rate ${n} star${n > 1 ? "s" : ""}`}
          onMouseEnter={() => setHover(n)}
          onMouseLeave={() => setHover(0)}
          onClick={() => onChange?.(n)}
        >
          <Star size={size} className={n <= shown ? "on" : ""} />
        </button>
      ))}
    </fieldset>
  );
}

/** At the end of a finished lesson: well done, rate it (not one's own), and what next. */
export function CompleteCard({
  control,
  lessonId,
  myStars,
  signedIn,
  canRate,
  onNotes,
}: {
  control: PlayerControl;
  lessonId: string;
  myStars: number;
  signedIn: boolean;
  canRate: boolean;
  onNotes: () => void;
}) {
  const toast = useToast();
  const [stars, setStars] = useState(myStars);
  const [closed, setClosed] = useState(false);
  const atEnd = control.duration > 0 && control.t >= control.duration - 0.3 && !control.playing;
  useEffect(() => {
    if (!atEnd) setClosed(false);
  }, [atEnd]);
  if (!atEnd || closed) return null;
  const rate = async (n: number) => {
    setStars(n);
    try {
      await v1.rate(lessonId, n);
      invalidate(`lesson:${lessonId}`);
      toast.success("Thanks for rating!");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="complete-layer">
      <div className="complete-card" role="dialog" aria-label="Lesson complete">
        <button type="button" className="ui-icon-btn complete-close" aria-label="Close" onClick={() => setClosed(true)}>
          <X size={18} />
        </button>
        <div className="complete-badge">
          <Trophy size={30} />
        </div>
        <h2>Lesson complete</h2>
        <p>Nicely done. A minute to reflect makes it stick.</p>
        {canRate && (
          <div className="complete-rate">
            <span>How clear was it?</span>
            <StarRating value={stars} onChange={(n) => void rate(n)} />
          </div>
        )}
        <div className="complete-actions">
          <Button
            variant="secondary"
            icon={RotateCcw}
            onClick={() => {
              control.seek(0);
              control.play();
            }}
          >
            Watch again
          </Button>
          {signedIn && (
            <Button
              variant="secondary"
              icon={PenLine}
              onClick={() => {
                setClosed(true);
                onNotes();
              }}
            >
              Write a note
            </Button>
          )}
          <Button variant="primary" icon={Compass} onClick={() => navigate("/explore")}>
            Explore more
          </Button>
        </div>
      </div>
    </div>
  );
}

const STAGES = [
  { key: "plan", label: "Planning" },
  { key: "scenes", label: "Drawing scenes" },
  { key: "ready", label: "Ready" },
];

/** While a lesson is being made: the stage it is at, scenes ready, and what to do if it stopped. */
export function GenerationProgress({
  summary,
  line,
  hint,
  canControl,
  busy,
  onCancel,
  onResume,
}: {
  summary: GenerationSummary;
  line: string;
  hint: string | null;
  canControl: boolean;
  busy: boolean;
  onCancel: () => void;
  onResume: () => void;
}) {
  const total = Math.max(summary.scenes.length, 1);
  const ready = summary.scenes.filter((s) => s.status === "ready" || s.status === "degraded").length;
  const planned = summary.chapters.length
    ? summary.chapters.filter((c) => c.storyboarded).length / summary.chapters.length
    : 0;
  const stage = summary.status === "complete" ? 2 : summary.scenes.length > 0 ? 1 : 0;
  const stopped = summary.status === "failed" || summary.status === "paused" || summary.status === "cancelled";
  const value = summary.status === "complete" ? 1 : 0.08 + 0.92 * ((ready / total) * Math.max(0.5, planned));
  return (
    <div className={`gen-progress ${stopped ? "stopped" : ""}`}>
      <div className="gen-progress-top">
        <ol className="gen-steps">
          {STAGES.map((s, i) => (
            <li key={s.key} className={i < stage ? "done" : i === stage ? "on" : ""}>
              <span>{i < stage ? <CircleCheck size={15} /> : i + 1}</span>
              {s.label}
            </li>
          ))}
        </ol>
        <div className="gen-progress-actions">
          {canControl && !stopped && summary.status !== "complete" && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
              Cancel
            </Button>
          )}
          {canControl &&
            (summary.status === "paused" || (summary.status === "failed" && !/budget/i.test(summary.error ?? ""))) && (
              <Button size="sm" variant="primary" disabled={busy} onClick={onResume}>
                Resume
              </Button>
            )}
        </div>
      </div>
      <ProgressBar value={value} label="Lesson creation" />
      <div className="gen-progress-line">
        {!stopped && summary.status !== "complete" && <span className="live-dot" />}
        <span>{line}</span>
        {summary.scenes.length > 0 && (
          <span className="muted">
            {ready} of {summary.scenes.length} scenes ready
          </span>
        )}
      </div>
      {summary.error && stopped && (
        <div className="gen-progress-error">
          {summary.status === "cancelled"
            ? "This lesson was cancelled."
            : "This lesson stopped before it was finished."}
          {hint && <div className="muted">{hint}</div>}
        </div>
      )}
    </div>
  );
}
