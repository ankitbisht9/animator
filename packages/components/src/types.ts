import type { FrameObject, Theme } from "@animator/engine";
import type { ReactNode } from "react";

export interface RenderProps {
  o: FrameObject;
  theme: Theme;
  /** Scene time in seconds, for ambient motion (always a pure function of t). */
  t: number;
  /** Scene seed for stateless randomness. */
  seed: number;
  /** Compile-time prepared data for this object (e.g. typeset equation). */
  prep: unknown;
}

/** Draws an object in its local frame (the SceneView applies its matrix and opacity). */
export type Renderer = (p: RenderProps) => ReactNode;

/** One narration beat as speech (TTS on): its audio, and when each caption word is said
 * (seconds from the start of the audio, one [start, end] per caption word). */
export interface BeatSpeech {
  id: string;
  /** Beat start in the scene (seconds). */
  start: number;
  /** Audio file name (served at /api/tts/<audio>). */
  audio: string;
  duration: number;
  words: [number, number][];
  /** The provider reported the word timings (otherwise they are estimated). */
  timed?: boolean;
}

/** A scene's spoken narration. Absent when narration audio is off: captions time themselves. */
export interface SceneNarration {
  provider?: string;
  voice?: string;
  beats: BeatSpeech[];
}
