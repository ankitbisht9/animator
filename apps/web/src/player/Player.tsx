/**
 * The lesson player: stage, controls, scene list, hover inspector and
 * checkpoint questions. Interactivity lives here in UI state; the frame on
 * screen is always evaluate(scene, localT), exactly what export renders.
 *
 * Progressive playback: while a lesson is still being generated, upcoming scenes are shown
 * as pending. Reaching the end of what is ready holds the last frame with a "preparing the
 * next scene" overlay and resumes by itself when the next scene arrives (spec Section 13).
 */
import { SceneView } from "@animator/components";
import { CANVAS, cameraMatrix, evaluate, type FrameObject, stripMarkup } from "@animator/engine";
import { Component, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Lesson, locate } from "../lesson/lesson";
import { formatTime, PlaybackClock } from "./clock";
import { VoicePlayer, voiceAt, voiceUrl } from "./voice";

export interface PendingScene {
  /** Stable identity (scene id, or chapter id before it is storyboarded). */
  key: string;
  title: string;
  duration: number;
  status: string;
}

/** What an add-on on the stage (doubts, ADR-050) may see and do with the player. */
export interface PlayerControl {
  /** The global playhead (seconds). */
  t: number;
  playing: boolean;
  /** The scene on screen and how far into it (seconds). */
  scene: { index: number; local: number } | null;
  pause: () => void;
  play: () => void;
  /** While locked (a dialog is open) the player's keyboard shortcuts and its play button are off. */
  lock: (on: boolean) => void;
  /** Jump to a global time (seconds), within what is ready. */
  seek: (t: number) => void;
  /** The ready part of the lesson (seconds). */
  duration: number;
}

interface Props {
  lesson: Lesson;
  autoPlay?: boolean;
  /** Scenes planned but not playable yet (in order, after the lesson's scenes). */
  pending?: PendingScene[];
  /** More scenes are coming: reaching the end means buffering, not the end. */
  generating?: boolean;
  /** Called with the playhead about every 2 s while playing (pacing). */
  onTime?: (t: number) => void;
  /** Drawn on the stage, with control of the player (optional: without it nothing changes). */
  addon?: (control: PlayerControl) => ReactNode;
  /** How playback starts (the learner's preferences); defaults: normal speed, captions and voice on. */
  initial?: { rate?: number; captions?: boolean; voice?: boolean };
}

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];

function prettyId(id: string): string {
  return id
    .replace(/_\d+$/, "")
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * A render error in one scene must not break the page (spec Section 17): the boundary logs it and
 * shows `fallback` (the last good frame); `onOk` reports every successful render so the player
 * knows which frame that is.
 */
class SceneBoundary extends Component<
  { sceneId: string; children: ReactNode; fallback: ReactNode; onOk: () => void },
  { failed: boolean }
> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override componentDidCatch(error: unknown) {
    console.error("scene render error", this.props.sceneId, error);
  }
  override componentDidMount() {
    if (!this.state.failed) this.props.onOk();
  }
  override componentDidUpdate(prev: { sceneId: string }) {
    if (prev.sceneId !== this.props.sceneId && this.state.failed) this.setState({ failed: false });
    else if (!this.state.failed) this.props.onOk();
  }
  override render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export function Player({ lesson, autoPlay = false, pending = [], generating = false, onTime, addon, initial }: Props) {
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(initial?.rate ?? 1);
  const [captions, setCaptions] = useState(initial?.captions ?? true);
  const [voiceOn, setVoiceOn] = useState(initial?.voice ?? true);
  const [hover, setHover] = useState<string | null>(null);
  const [question, setQuestion] = useState<{ sceneIndex: number; id: string } | null>(null);
  const [answered, setAnswered] = useState<Record<string, number>>({});
  const [buffering, setBuffering] = useState(false);
  const [locked, setLocked] = useState(false);
  const lockedRef = useRef(locked);
  lockedRef.current = locked;
  const stageRef = useRef<HTMLDivElement>(null);
  const questionRef = useRef(question);
  questionRef.current = question;
  const generatingRef = useRef(generating);
  generatingRef.current = generating;
  const tRef = useRef(t);
  tRef.current = t;

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
  const limitRef = useRef<(from: number, to: number) => number | null>(() => null);
  limitRef.current = (from, to) => {
    const hit = checkpoints.find((c) => c.at > from && c.at <= to && answeredRef.current[c.id] === undefined);
    if (!hit) return null;
    setQuestion({ sceneIndex: hit.sceneIndex, id: hit.id });
    return hit.at;
  };

  // One clock for the life of the player: new scenes extend it without resetting the playhead.
  const clockRef = useRef<PlaybackClock | null>(null);
  if (clockRef.current === null) {
    clockRef.current = new PlaybackClock(
      lesson.duration,
      (time, isPlaying) => {
        setT(time);
        setPlaying(isPlaying);
        const c = clockRef.current;
        if (!isPlaying && c && generatingRef.current && time >= c.duration - 1e-6 && c.duration > 0) {
          setBuffering(true);
        }
      },
      (from, to) => limitRef.current(from, to),
    );
  }
  const clock = clockRef.current;
  useEffect(() => () => clock.dispose(), [clock]);
  useEffect(() => {
    if (initial?.rate && initial.rate !== 1) clock.rate = initial.rate;
  }, [clock, initial?.rate]);
  useEffect(() => {
    if (autoPlay) clock.play();
  }, [clock, autoPlay]);

  // The lesson grew (or was replaced): extend the clock; leave buffering when there is more to play.
  const lastDuration = useRef(lesson.duration);
  useEffect(() => {
    const grew = lesson.duration > lastDuration.current + 1e-6;
    lastDuration.current = lesson.duration;
    clock.duration = lesson.duration;
    if (clock.t > lesson.duration) clock.seek(lesson.duration);
    if (grew && buffering) {
      setBuffering(false);
      clock.play();
    }
  }, [lesson, clock, buffering]);
  useEffect(() => {
    if (!generating && buffering) setBuffering(false);
  }, [generating, buffering]);

  // Report the playhead (pacing) while playing or buffering.
  useEffect(() => {
    if (!onTime) return;
    onTime(tRef.current);
    const id = window.setInterval(() => onTime(tRef.current), 2000);
    return () => window.clearInterval(id);
  }, [onTime]);

  const loc = locate(lesson, t);
  const scene = loc?.scene;

  // Narration audio (only lessons with a voice): the beat on screen speaks, from the same clock.
  const hasVoice = useMemo(() => lesson.scenes.some((s) => (s.narration?.beats.length ?? 0) > 0), [lesson]);
  const voiceRef = useRef<VoicePlayer | null>(null);
  useEffect(() => () => voiceRef.current?.dispose(), []);
  const voice = hasVoice && voiceOn ? voiceAt(scene, loc?.local ?? 0) : null;
  useEffect(() => {
    if (!hasVoice && !voiceRef.current) return;
    if (!voiceRef.current) voiceRef.current = new VoicePlayer();
    voiceRef.current.sync(voice, playing, rate);
  }, [hasVoice, voice, playing, rate]);
  useEffect(() => {
    // Fetch this scene's speech and the start of the next scene's ahead of time.
    if (!hasVoice || !scene) return;
    const next = lesson.scenes[scene.index + 1];
    const urls = [...(scene.narration?.beats ?? []), ...(next?.narration?.beats.slice(0, 1) ?? [])].map((b) =>
      voiceUrl(b.audio),
    );
    if (!voiceRef.current) voiceRef.current = new VoicePlayer();
    voiceRef.current.preload(urls);
  }, [hasVoice, scene, lesson]);
  const frame = useMemo(
    () => (scene?.compiled ? evaluate(scene.compiled, loc?.local ?? 0) : null),
    [scene, loc?.local],
  );
  // The last frame that rendered without error (held on screen if a later frame fails).
  const lastGood = useRef<{ compiled: NonNullable<typeof scene>["compiled"]; frame: typeof frame } | null>(null);
  const markGood = () => {
    if (scene?.compiled && frame) lastGood.current = { compiled: scene.compiled, frame };
  };

  const onKey = useCallback(
    (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input, textarea, select, .cm-editor")) return;
      if (lockedRef.current) return;
      if (e.code === "Space") {
        e.preventDefault();
        if (!questionRef.current) clock.toggle();
      } else if (e.code === "ArrowRight") clock.seek(clock.t + 5);
      else if (e.code === "ArrowLeft") clock.seek(clock.t - 5);
      else if (e.key === "c") setCaptions((c) => !c);
      else if (e.key === "m") setVoiceOn((v) => !v);
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
  const nextPending = pending[0];
  const empty = lesson.scenes.length === 0;

  return (
    <div className="player">
      <div className="player-main">
        <div className="stage" ref={stageRef}>
          {scene?.compiled && frame ? (
            <SceneBoundary
              sceneId={scene.id}
              onOk={markGood}
              fallback={
                lastGood.current?.compiled && lastGood.current.frame ? (
                  <>
                    <SceneView
                      compiled={lastGood.current.compiled}
                      frame={lastGood.current.frame}
                      captions={captions}
                      narration={scene.narration}
                    />
                    <div className="render-warning">This part could not be drawn; showing the last good frame.</div>
                  </>
                ) : (
                  <DegradedScene title={scene.title} message="This scene could not be drawn." />
                )
              }
            >
              <SceneView
                compiled={scene.compiled}
                frame={frame}
                captions={captions}
                narration={scene.narration}
                interactive={!playing}
                hoverId={playing ? null : hover}
                onHover={setHover}
              />
            </SceneBoundary>
          ) : empty ? (
            <Preparing title={nextPending?.title} first />
          ) : (
            <DegradedScene
              title={scene?.title ?? "Scene"}
              message={scene?.issues.find((i) => i.severity === "error")?.message}
            />
          )}
          {buffering && !empty && <Preparing title={nextPending?.title} />}
          {!playing && hovered && tip && !buffering && (
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
          {!playing && !q && !buffering && !empty && !locked && (
            <button
              type="button"
              className={`stage-play ${t > 0 ? "paused" : ""}`}
              onClick={() => clock.play()}
              aria-label="Play"
            >
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
          {addon?.({
            t,
            playing,
            scene: loc ? { index: loc.scene.index, local: loc.local } : null,
            pause: () => clock.pause(),
            play: () => clock.play(),
            lock: setLocked,
            seek: (x) => {
              setBuffering(false);
              clock.seek(x);
            },
            duration: lesson.duration,
          })}
        </div>
        <Controls
          lesson={lesson}
          pending={pending}
          t={t}
          playing={playing}
          rate={rate}
          captions={captions}
          voice={hasVoice ? voiceOn : undefined}
          onVoice={() => setVoiceOn((v) => !v)}
          onToggle={() => !question && !empty && clock.toggle()}
          onSeek={(x) => {
            setBuffering(false);
            clock.seek(x);
          }}
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
              onClick={() => {
                setBuffering(false);
                clock.seek(s.start + 0.001);
              }}
            >
              <span className={`scene-dot ${!s.ok ? "degraded" : done ? "done" : active ? "playing" : ""}`}>
                {s.index + 1}
              </span>
              <span className="scene-name">{s.title}</span>
              <span className="scene-time">{formatTime(s.duration)}</span>
            </button>
          );
        })}
        {pending.map((p, i) => (
          <div className="scene-item pending" key={p.key}>
            <span className={`scene-dot pending ${p.status === "planned" || p.status === "chapter" ? "" : "busy"}`}>
              {p.status === "chapter" ? "…" : lesson.scenes.length + i + 1}
            </span>
            <span className="scene-name">{p.title}</span>
            <span className="scene-time">
              {p.status === "chapter" ? "later" : p.status === "planned" ? "queued" : "making…"}
            </span>
          </div>
        ))}
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

function Preparing({ title, first = false }: { title?: string; first?: boolean }) {
  return (
    <div className="preparing">
      <div className="preparing-card">
        <div className="spinner" aria-hidden="true" />
        <div className="preparing-title">{first ? "Creating your lesson…" : "Preparing the next scene…"}</div>
        {title && <div className="preparing-next">Up next: {title}</div>}
      </div>
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
  pending: PendingScene[];
  t: number;
  playing: boolean;
  rate: number;
  captions: boolean;
  /** Narration audio on/off; undefined when the lesson has no voice (no button). */
  voice?: boolean;
  onVoice: () => void;
  onToggle: () => void;
  onSeek: (t: number) => void;
  onRate: (r: number) => void;
  onCaptions: () => void;
  onFullscreen: () => void;
}) {
  const { lesson, t, pending } = props;
  const total = Math.max(1e-6, lesson.duration + pending.reduce((a, p) => a + p.duration, 0));
  const barRef = useRef<HTMLDivElement>(null);
  const seekFromEvent = (clientX: number) => {
    const r = barRef.current?.getBoundingClientRect();
    if (!r) return;
    // Seeking is limited to the ready part (spec Section 13).
    props.onSeek(Math.min(lesson.duration, ((clientX - r.left) / r.width) * total));
  };
  let pendingStart = lesson.duration;
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
        {formatTime(t)} / {formatTime(total)}
      </span>
      <div
        className="scrubber"
        ref={barRef}
        role="slider"
        aria-label="Seek"
        aria-valuemin={0}
        aria-valuemax={Math.round(total)}
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
            style={{ left: `${(s.start / total) * 100}%`, width: `${(s.duration / total) * 100}%` }}
            title={s.title}
          />
        ))}
        {pending.map((p) => {
          const left = pendingStart;
          pendingStart += p.duration;
          return (
            <div
              key={p.key}
              className="segment pending"
              style={{ left: `${(left / total) * 100}%`, width: `${(p.duration / total) * 100}%` }}
              title={`${p.title} (not ready yet)`}
            />
          );
        })}
        <div className="progress" style={{ width: `${(t / total) * 100}%` }} />
        <div className="knob" style={{ left: `${(t / total) * 100}%` }} />
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
      {props.voice !== undefined && (
        <button
          type="button"
          className={`icon-btn ${props.voice ? "on" : ""}`}
          onClick={props.onVoice}
          aria-label={props.voice ? "Mute voice" : "Unmute voice"}
          title="Voice (m)"
        >
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path d="M4 9v6h4l5 4V5L8 9z" fill="currentColor" />
            {props.voice ? (
              <path
                d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12"
                stroke="currentColor"
                strokeWidth="2"
                fill="none"
                strokeLinecap="round"
              />
            ) : (
              <path d="M16 9l5 6M21 9l-5 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            )}
          </svg>
        </button>
      )}
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
