/**
 * Doubts in a lesson (ADR-050): the raise-hand button on the stage and, once raised, the doubt screen.
 * It talks to the player only through PlayerControl (pause, resume, the moment on screen); a lesson
 * without this add-on plays exactly as before.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { PlayerControl } from "../player/Player";
import "./doubts.css";
import { audioUrl, type DoubtConfig, doubtsApi, type Phrases } from "./api";
import { DoubtScreen } from "./DoubtScreen";
import { HandRaise } from "./parts";

interface Props {
  control: PlayerControl;
  lessonId: string;
  language: string;
  audience: string;
  durationMin: number;
}

/** Whether doubts are on (asked once per page load). */
let configOnce: Promise<DoubtConfig> | null = null;
export function doubtConfig(): Promise<DoubtConfig> {
  configOnce ??= doubtsApi.config().catch(() => ({ enabled: false }));
  return configOnce;
}

export function DoubtFeature({ control, lessonId, language, audience, durationMin }: Props) {
  const [config, setConfig] = useState<DoubtConfig | null>(null);
  const [phrases, setPhrases] = useState<Phrases>({});
  const [moment, setMoment] = useState<{ sceneIndex: number; t: number; wasPlaying: boolean } | null>(null);
  const [raising, setRaising] = useState(false);
  const controlRef = useRef(control);
  controlRef.current = control;

  useEffect(() => {
    let live = true;
    void doubtConfig().then((c) => {
      if (!live) return;
      setConfig(c);
      if (!c.enabled) return;
      // The tutor's fixed lines and their audio, ahead of time: a raised hand is greeted at once.
      doubtsApi
        .phrases(language)
        .then((p) => {
          if (!live) return;
          setPhrases(p);
          for (const lines of Object.values(p))
            for (const line of lines ?? []) {
              if (!line.audio) continue;
              const el = new Audio();
              el.preload = "auto";
              el.src = audioUrl(line.audio);
            }
        })
        .catch(() => undefined);
    });
    return () => {
      live = false;
    };
  }, [language]);

  const raise = useCallback(() => {
    const c = controlRef.current;
    if (!c.scene || moment) return;
    const wasPlaying = c.playing;
    c.pause();
    c.lock(true);
    if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => undefined);
    setRaising(true);
    // The hand goes up first, then the tutor turns to the learner.
    window.setTimeout(() => {
      setRaising(false);
      setMoment({ sceneIndex: c.scene?.index ?? 0, t: c.scene?.local ?? 0, wasPlaying });
    }, 420);
  }, [moment]);

  const close = useCallback(() => {
    const was = moment?.wasPlaying;
    setMoment(null);
    controlRef.current.lock(false);
    if (was) controlRef.current.play();
  }, [moment]);

  useEffect(() => {
    if (!config?.enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input, textarea, select, .cm-editor")) return;
      if (e.key === "d" && !e.metaKey && !e.ctrlKey && !e.altKey && !moment) {
        e.preventDefault();
        raise();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [config?.enabled, moment, raise]);

  if (!config?.enabled || !control.scene) return null;
  return (
    <>
      {!moment && <HandRaise onRaise={raise} raised={raising} />}
      {moment && (
        <DoubtScreen
          lessonId={lessonId}
          language={language}
          audience={audience}
          durationMin={durationMin}
          sceneIndex={moment.sceneIndex}
          t={moment.t}
          phrases={phrases}
          voice={config.voice !== false}
          onClose={close}
        />
      )}
    </>
  );
}
