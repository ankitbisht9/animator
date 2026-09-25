/**
 * How presence (enter/exit progress) and emphasis translate into visual
 * modifiers. Pure functions of their inputs.
 */
import { clamp01 } from "../math/num";

export interface PresenceEffect {
  alpha: number;
  scale: number;
  dx: number;
  dy: number;
  /** Stroke write-on progress. */
  draw: number;
  /** Text/equation reveal progress. */
  reveal: number;
  /** Fill opacity factor (fills fade in after a stroke is drawn). */
  fill: number;
}

const FULL: PresenceEffect = { alpha: 1, scale: 1, dx: 0, dy: 0, draw: 1, reveal: 1, fill: 1 };

/** `p` is presence progress (may overshoot 1 with back/spring easings). */
export function presenceEffect(style: string, p: number): PresenceEffect {
  if (p >= 1 && style !== "pop" && style !== "zoom") return FULL;
  const a = clamp01(p);
  switch (style) {
    case "pop":
      return { ...FULL, alpha: clamp01(p * 2.2), scale: Math.max(0, 0.35 + 0.65 * p) };
    case "grow":
    case "shrink":
      return { ...FULL, alpha: clamp01(p * 3), scale: Math.max(0, p) };
    case "zoom":
      return { ...FULL, alpha: a, scale: 1.5 - 0.5 * p };
    case "slide_up":
      return { ...FULL, alpha: a, dy: (1 - p) * 70 };
    case "slide_down":
      return { ...FULL, alpha: a, dy: -(1 - p) * 70 };
    case "slide_left":
      return { ...FULL, alpha: a, dx: (1 - p) * 90 };
    case "slide_right":
      return { ...FULL, alpha: a, dx: -(1 - p) * 90 };
    case "draw":
      return { ...FULL, alpha: a > 0 ? 1 : 0, draw: a, fill: clamp01((a - 0.55) / 0.45) };
    case "write":
      return { ...FULL, alpha: a > 0 ? 1 : 0, reveal: a };
    case "none":
      return a > 0 ? FULL : { ...FULL, alpha: 0 };
    default:
      return { ...FULL, alpha: a };
  }
}

export interface EmphasisEffect {
  scale: number;
  dx: number;
}

/** Transform part of an emphasis; overlays (glow, box, ...) are drawn by the renderer. */
export function emphasisEffect(style: string, amount: number, u: number): EmphasisEffect {
  switch (style) {
    case "pulse":
      return { scale: 1 + 0.12 * amount, dx: 0 };
    case "shake":
      return { scale: 1, dx: Math.sin(u * Math.PI * 10) * 10 * amount };
    default:
      return { scale: 1, dx: 0 };
  }
}
