/**
 * Plays one answer: its scene (compiled here, continuing from the lesson moment it reuses), its voice
 * and its word-timed captions, from the same clock, exactly like a lesson scene. The lesson player is
 * not involved: an answer is a one-scene lesson of its own.
 */
import { SceneView } from "@animator/components";
import { type CompiledScene, compileScene, evaluate, lift } from "@animator/engine";
import { useEffect, useMemo, useRef, useState } from "react";
import type { LessonScene } from "../lesson/lesson";
import { PlaybackClock } from "../player/clock";
import { VoicePlayer, voiceAt, voiceUrl } from "../player/voice";
import type { DoubtAnswer } from "./api";

export function compileAnswer(answer: DoubtAnswer): CompiledScene | null {
  if (!answer.scene) return null;
  const lifted = lift(answer.scene);
  if (!lifted.ok || !lifted.scene) return null;
  const res = compileScene(lifted.scene, { prev: answer.prev_state ?? null });
  return res.ok && res.compiled ? res.compiled : null;
}

interface Props {
  answer: DoubtAnswer;
  /** Held still (the learner is asking again). */
  hold?: boolean;
  onEnd?: () => void;
  onSpeaking?: (speaking: boolean) => void;
}

export function DoubtPlayer({ answer, hold = false, onEnd, onSpeaking }: Props) {
  const compiled = useMemo(() => compileAnswer(answer), [answer]);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const onEndRef = useRef(onEnd);
  onEndRef.current = onEnd;
  const clockRef = useRef<PlaybackClock | null>(null);
  const voiceRef = useRef<VoicePlayer | null>(null);
  const ended = useRef(false);

  useEffect(() => {
    if (!compiled) return;
    ended.current = false;
    const clock = new PlaybackClock(compiled.duration, (time, isPlaying) => {
      setT(time);
      setPlaying(isPlaying);
      if (!isPlaying && time >= compiled.duration - 1e-6 && !ended.current) {
        ended.current = true;
        onEndRef.current?.();
      }
    });
    clockRef.current = clock;
    const voice = new VoicePlayer();
    voiceRef.current = voice;
    voice.preload((answer.narration?.beats ?? []).map((b) => voiceUrl(b.audio)));
    clock.play();
    return () => {
      clock.dispose();
      voice.dispose();
      clockRef.current = null;
      voiceRef.current = null;
    };
  }, [compiled, answer]);

  useEffect(() => {
    if (hold) clockRef.current?.pause();
  }, [hold]);

  const scene = useMemo(
    () => (compiled ? ({ compiled, narration: answer.narration } as unknown as LessonScene) : undefined),
    [compiled, answer.narration],
  );
  const target = scene && answer.narration ? voiceAt(scene, t) : null;
  useEffect(() => {
    voiceRef.current?.sync(target, playing, 1);
  }, [target, playing]);
  const speaking = playing && target !== null;
  useEffect(() => onSpeaking?.(speaking), [speaking, onSpeaking]);

  const frame = useMemo(() => (compiled ? evaluate(compiled, t) : null), [compiled, t]);
  if (!compiled || !frame) {
    return (
      <div className="doubt-stage doubt-stage-text">
        <div className="doubt-stage-title">{answer.title}</div>
        {answer.sentences.map((s) => (
          <p key={s}>{s.replace(/\*\*/g, "")}</p>
        ))}
      </div>
    );
  }
  const clock = clockRef.current;
  const done = t >= compiled.duration - 1e-6;
  return (
    <div className="doubt-stage">
      <SceneView compiled={compiled} frame={frame} captions narration={answer.narration} />
      <div className="doubt-stage-bar">
        <button
          type="button"
          className="doubt-mini-btn"
          aria-label={playing ? "Pause the answer" : done ? "Play the answer again" : "Play the answer"}
          onClick={() => {
            if (!clock) return;
            if (playing) clock.pause();
            else {
              if (done) {
                ended.current = false;
                clock.seek(0);
              }
              clock.play();
            }
          }}
        >
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            {playing ? (
              <path d="M6 5h4v14H6zM14 5h4v14h-4z" fill="currentColor" />
            ) : done ? (
              <path d="M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z" fill="currentColor" />
            ) : (
              <path d="M8 5v14l11-7z" fill="currentColor" />
            )}
          </svg>
        </button>
        <div className="doubt-progress">
          <div style={{ width: `${(t / Math.max(1e-6, compiled.duration)) * 100}%` }} />
        </div>
      </div>
    </div>
  );
}
