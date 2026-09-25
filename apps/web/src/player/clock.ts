/**
 * PlaybackClock: requestAnimationFrame deltas -> global time t. Because
 * evaluate() is pure, a slow frame simply skips ahead; time never slows down.
 */
export class PlaybackClock {
  t = 0;
  playing = false;
  rate = 1;
  private last: number | null = null;
  private raf = 0;

  constructor(
    public duration: number,
    private readonly onTick: (t: number, playing: boolean) => void,
    /** Optional gate: return a clamped time if playback must stop earlier (buffering, checkpoints). */
    private readonly limit?: (from: number, to: number) => number | null,
  ) {}

  private loop = (now: number) => {
    if (!this.playing) return;
    if (this.last !== null) {
      const next = Math.min(this.duration, this.t + ((now - this.last) / 1000) * this.rate);
      const stop = this.limit?.(this.t, next) ?? null;
      this.t = stop ?? next;
      if (stop !== null || this.t >= this.duration) this.playing = false;
      this.onTick(this.t, this.playing);
    }
    this.last = now;
    if (this.playing) this.raf = requestAnimationFrame(this.loop);
  };

  play() {
    if (this.playing) return;
    if (this.t >= this.duration) this.t = 0;
    this.playing = true;
    this.last = null;
    this.raf = requestAnimationFrame(this.loop);
    this.onTick(this.t, true);
  }

  pause() {
    this.playing = false;
    cancelAnimationFrame(this.raf);
    this.onTick(this.t, false);
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  seek(t: number) {
    this.t = Math.max(0, Math.min(this.duration, t));
    this.last = null;
    this.onTick(this.t, this.playing);
  }

  dispose() {
    this.playing = false;
    cancelAnimationFrame(this.raf);
  }
}

export function formatTime(t: number): string {
  const s = Math.max(0, Math.floor(t));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
