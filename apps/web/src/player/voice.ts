/**
 * Narration audio in the player (TTS on). The beat on screen plays its speech from the player's
 * clock, like the picture: pausing, seeking, speed changes, checkpoints and buffering all come from
 * the clock, and the audio is moved back into place whenever it drifts from where the clock says it
 * should be. Scenes without narration never create an audio element.
 */
import type { LessonScene } from "../lesson/lesson";

export interface VoiceTarget {
  url: string;
  /** Seconds into the beat's audio. */
  offset: number;
}

export const voiceUrl = (audio: string) => `/api/tts/${encodeURIComponent(audio)}`;

/** The speech playing at scene-local time `local`, or null between (and after) spoken beats. A scene
 * spoken in one take has one audio for all its beats: consecutive beats continue the same audio, so
 * it plays through without being restarted. */
export function voiceAt(scene: LessonScene | undefined, local: number): VoiceTarget | null {
  const beats = scene?.narration?.beats;
  if (!scene?.compiled || !beats?.length) return null;
  for (const b of beats) {
    const start = scene.compiled.beats.find((x) => x.id === b.id)?.start ?? b.start;
    if (local >= start && local < start + b.duration) {
      return { url: voiceUrl(b.audio), offset: (b.offset ?? 0) + local - start };
    }
  }
  return null;
}

const DRIFT_S = 0.25;
const KEEP = 24;

export class VoicePlayer {
  private readonly els = new Map<string, HTMLAudioElement>();
  private current: HTMLAudioElement | null = null;
  /** The browser refused to start audio before the viewer touched the page; retried after they do. */
  private blocked = false;
  private readonly unblock = () => {
    this.blocked = false;
  };

  constructor() {
    window.addEventListener("pointerdown", this.unblock, true);
    window.addEventListener("keydown", this.unblock, true);
  }

  private element(url: string): HTMLAudioElement {
    let el = this.els.get(url);
    if (!el) {
      el = new Audio();
      el.preload = "auto";
      el.src = url;
      this.els.set(url, el);
      if (this.els.size > KEEP) {
        for (const [key, old] of this.els) {
          if (old === this.current || key === url) continue;
          old.pause();
          this.els.delete(key);
          break;
        }
      }
    }
    return el;
  }

  /** Start downloading speech that will play soon. */
  preload(urls: string[]) {
    for (const url of urls) this.element(url);
  }

  sync(target: VoiceTarget | null, playing: boolean, rate: number) {
    if (!target || !playing) {
      this.current?.pause();
      if (!target) this.current = null;
      return;
    }
    const el = this.element(target.url);
    if (this.current !== el) {
      this.current?.pause();
      this.current = el;
      el.currentTime = target.offset;
    }
    if (el.playbackRate !== rate) el.playbackRate = rate;
    if (Math.abs(el.currentTime - target.offset) > DRIFT_S * Math.max(1, rate)) el.currentTime = target.offset;
    if (el.paused && !this.blocked) {
      el.play().catch((e: unknown) => {
        if ((e as { name?: string })?.name === "NotAllowedError") this.blocked = true;
      });
    }
  }

  dispose() {
    window.removeEventListener("pointerdown", this.unblock, true);
    window.removeEventListener("keydown", this.unblock, true);
    for (const el of this.els.values()) el.pause();
    this.els.clear();
    this.current = null;
  }
}
