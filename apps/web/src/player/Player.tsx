/**
 * The lesson player: stage, controls, scene list, hover inspector and
 * checkpoint questions. Interactivity lives here in UI state; the frame on
 * screen is always evaluate(scene, localT), exactly what export renders.
 */
import { SceneView } from "@animator/components";
import { CANVAS, cameraMatrix, evaluate, type FrameObject, stripMarkup } from "@animator/engine";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Lesson, locate } from "../lesson/lesson";
import { formatTime, PlaybackClock } from "./clock";

interface Props {
  lesson: Lesson;
  autoPlay?: boolean;
}

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];

function prettyId(id: string): string {
  return id
    .replace(/_\d+$/, "")
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

export function Player({ lesson, autoPlay = false }: Props) {
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [captions, setCaptions] = useState(true);
  const [hover, setHover] = useState<string | null>(null);
  const [question, setQuestion] = useState<{ sceneIndex: number; id: string } | null>(null);
  const [answered, setAnswered] = useState<Record<string, number>>({});
  const stageRef = useRef<HTMLDivElement>(null);
  const questionRef = useRef(question);
  questionRef.current = question;

  // Global checkpoint times.
  const checkpoints = useMemo(
    () =>
      lesson.scenes.flatMap((s) =>
        (s.compiled?.interactions ?? []).map((q) => ({ sceneIndex: s.index, id: q.id, at: s.start + q.at })),
      ),
    [lesson],
  );
  const answeredRef = useRef(answered);
  answeredRef.current = answered;

  const clock = useMemo(
    () =>
      new PlaybackClock(
        lesson.duration,
        (time, isPlaying) => {
          setT(time);
          setPlaying(isPlaying);
        },
        (from, to) => {
          const hit = checkpoints.find((c) => c.at > from && c.at <= to && answeredRef.current[c.id] === undefined);
          if (!hit) return null;
          setQuestion({ sceneIndex: hit.sceneIndex, id: hit.id });
          return hit.at;
        },
      ),
    [lesson, checkpoints],
  );
  useEffect(() => {
    if (autoPlay) clock.play();
    return () => clock.dispose();
  }, [clock, autoPlay]);

  const loc = locate(lesson, t);
  const scene = loc?.scene;
  const frame = useMemo(
    () => (scene?.compiled ? evaluate(scene.compiled, loc?.local ?? 0) : null),
    [scene, loc?.local],
  );

  const onKey = useCallback(
    (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input, textarea, .cm-editor")) return;
      if (e.code === "Space") {
        e.preventDefault();
        if (!questionRef.current) clock.toggle();
      } else if (e.code === "ArrowRight") clock.seek(clock.t + 5);
      else if (e.code === "ArrowLeft") clock.seek(clock.t - 5);
      else if (e.key === "c") setCaptions((c) => !c);
      else if (e.key === "f") void stageRef.current?.requestFullscreen?.();
    },
    [clock],
  );
  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);

  const hovered: FrameObject | undefined = frame?.objects.find((o) => o.id === hover);
  const compiledHover = scene?.compiled?.objects.find((o) => o.id === hover);

  // Tooltip position in stage pixels (object bbox -> camera -> container scale).
  let tip: { left: number; top: number } | null = null;
  if (hovered && frame && stageRef.current) {
    const m = hovered.layer === "overlay" ? [1, 0, 0, 1, 0, 0] : cameraMatrix(frame.camera);
    const bx = hovered.bbox.x + hovered.bbox.w / 2;
    const by = hovered.bbox.y;
    const sx = (m[0] ?? 1) * bx + (m[2] ?? 0) * by + (m[4] ?? 0);
    const sy = (m[1] ?? 0) * bx + (m[3] ?? 1) * by + (m[5] ?? 0);
    const k = stageRef.current.clientWidth / CANVAS.width;
    tip = { left: sx * k, top: sy * k };
  }

  const q = question
    ? lesson.scenes[question.sceneIndex]?.compiled?.interactions.find((x) => x.id === question.id)
    : null;

  return (
    <div className="player">
      <div className="player-main">
        <div className="stage" ref={stageRef}>
          {scene?.compiled && frame ? (
            <SceneView
              compiled={scene.compiled}
              frame={frame}
              captions={captions}
              interactive={!playing}
              hoverId={playing ? null : hover}
              onHover={setHover}
            />
          ) : (
            <DegradedScene
              title={scene?.title ?? "Scene"}
              message={scene?.issues.find((i) => i.severity === "error")?.message}
            />
          )}
          {!playing && hovered && tip && (
            <div className="inspector" style={{ left: tip.left, top: tip.top }}>
              <div className="inspector-title">{hovered.name ?? prettyId(hovered.id)}</div>
              {hovered.description && <div className="inspector-desc">{hovered.description}</div>}
              {compiledHover &&
                Object.keys(compiledHover.bound).map((k) => (
                  <div className="inspector-value" key={k}>
                    <span>{k}</span>
                    <b>{String(hovered.params[k])}</b>
                  </div>
                ))}
              <div className="inspector-type">{hovered.type.replace(/_/g, " ")}</div>
            </div>
          )}
          {!playing && !q && (
            <button type="button" className="stage-play" onClick={() => clock.play()} aria-label="Play">
              <svg viewBox="0 0 24 24" width="44" height="44" aria-hidden="true">
                <path d="M8 5v14l11-7z" fill="currentColor" />
              </svg>
              <span>{t > 0 ? "Hover anything to explore, or keep watching" : "Play lesson"}</span>
            </button>
          )}
          {q && question && (
            <div className="checkpoint">
              <div className="checkpoint-card">
                <div className="checkpoint-kicker">Quick check</div>
                <div className="checkpoint-q">{q.question}</div>
                <div className="checkpoint-choices">
                  {q.choices.map((c, i) => {
                    const picked = answered[q.id];
                    const state = picked === undefined ? "" : i === q.answer ? "right" : i === picked ? "wrong" : "dim";
                    return (
                      <button
                        type="button"
                        key={c}
                        className={`choice ${state}`}
                        disabled={picked !== undefined}
                        onClick={() => setAnswered((a) => ({ ...a, [q.id]: i }))}
                      >
                        {c}
                      </button>
                    );
                  })}
                </div>
                {answered[q.id] !== undefined && (
                  <>
                    <div className={`checkpoint-feedback ${answered[q.id] === q.answer ? "right" : "wrong"}`}>
                      {answered[q.id] === q.answer ? "Yes! " : "Not quite. "}
                      {q.explain}
                    </div>
                    <button
                      type="button"
                      className="btn primary"
                      onClick={() => {
                        setQuestion(null);
                        clock.play();
                      }}
                    >
                      Continue
                    </button>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
        <Controls
          lesson={lesson}
          t={t}
          playing={playing}
          rate={rate}
          captions={captions}
          onToggle={() => !question && clock.toggle()}
          onSeek={(x) => clock.seek(x)}
          onRate={(r) => {
            clock.rate = r;
            setRate(r);
          }}
          onCaptions={() => setCaptions((c) => !c)}
          onFullscreen={() => void stageRef.current?.requestFullscreen?.()}
        />
      </div>
      <aside className="scene-list">
        <div className="scene-list-title">{lesson.title}</div>
        {lesson.scenes.map((s) => {
          const active = scene?.index === s.index;
          const done = t >= s.start + s.duration - 0.01;
          return (
            <button
              type="button"
              key={s.id}
              className={`scene-item ${active ? "active" : ""}`}
              onClick={() => clock.seek(s.start + 0.001)}
            >
              <span className={`scene-dot ${!s.ok ? "degraded" : done ? "done" : active ? "playing" : ""}`}>
                {s.index + 1}
              </span>
              <span className="scene-name">{s.title}</span>
              <span className="scene-time">{formatTime(s.duration)}</span>
            </button>
          );
        })}
        {scene?.compiled && (
          <div className="transcript">
            <div className="transcript-title">In this scene</div>
            {scene.compiled.beats.map((b) => (
              <button
                type="button"
                key={b.id}
                className={`beat ${loc && loc.local >= b.start && loc.local < b.end ? "current" : ""}`}
                onClick={() => clock.seek(scene.start + b.start + 0.001)}
              >
                {stripMarkup(b.text)}
              </button>
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}

function DegradedScene({ title, message }: { title: string; message?: string }) {
  return (
    <div className="degraded">
      <div className="degraded-badge">Scene unavailable</div>
      <div className="degraded-title">{title}</div>
      {message && <div className="degraded-msg">{message}</div>}
    </div>
  );
}

function Controls(props: {
  lesson: Lesson;
  t: number;
  playing: boolean;
  rate: number;
  captions: boolean;
  onToggle: () => void;
  onSeek: (t: number) => void;
  onRate: (r: number) => void;
  onCaptions: () => void;
  onFullscreen: () => void;
}) {
  const { lesson, t } = props;
  const barRef = useRef<HTMLDivElement>(null);
  const seekFromEvent = (clientX: number) => {
    const r = barRef.current?.getBoundingClientRect();
    if (!r) return;
    props.onSeek(((clientX - r.left) / r.width) * lesson.duration);
  };
  return (
    <div className="controls">
      <button type="button" className="icon-btn" onClick={props.onToggle} aria-label={props.playing ? "Pause" : "Play"}>
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          {props.playing ? (
            <path d="M6 5h4v14H6zM14 5h4v14h-4z" fill="currentColor" />
          ) : (
            <path d="M8 5v14l11-7z" fill="currentColor" />
          )}
        </svg>
      </button>
      <span className="time">
        {formatTime(t)} / {formatTime(lesson.duration)}
      </span>
      <div
        className="scrubber"
        ref={barRef}
        role="slider"
        aria-label="Seek"
        aria-valuemin={0}
        aria-valuemax={Math.round(lesson.duration)}
        aria-valuenow={Math.round(t)}
        tabIndex={0}
        onPointerDown={(e) => {
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
          seekFromEvent(e.clientX);
        }}
        onPointerMove={(e) => e.buttons === 1 && seekFromEvent(e.clientX)}
      >
        {lesson.scenes.map((s) => (
          <div
            key={s.id}
            className={`segment ${s.ok ? "" : "degraded"}`}
            style={{ left: `${(s.start / lesson.duration) * 100}%`, width: `${(s.duration / lesson.duration) * 100}%` }}
            title={s.title}
          />
        ))}
        <div className="progress" style={{ width: `${(t / lesson.duration) * 100}%` }} />
        <div className="knob" style={{ left: `${(t / lesson.duration) * 100}%` }} />
      </div>
      <select
        className="speed"
        value={props.rate}
        onChange={(e) => props.onRate(Number(e.target.value))}
        aria-label="Speed"
      >
        {SPEEDS.map((s) => (
          <option key={s} value={s}>
            {s}x
          </option>
        ))}
      </select>
      <button
        type="button"
        className={`icon-btn ${props.captions ? "on" : ""}`}
        onClick={props.onCaptions}
        aria-label="Captions"
      >
        CC
      </button>
      <button type="button" className="icon-btn" onClick={props.onFullscreen} aria-label="Fullscreen">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" stroke="currentColor" strokeWidth="2" fill="none" />
        </svg>
      </button>
    </div>
  );
}
