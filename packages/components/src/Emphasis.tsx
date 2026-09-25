/** Emphasis overlays drawn around an object's world bounds (behind or on top of it). */
import type { FrameObject } from "@animator/engine";
import { fmt, withAlpha } from "./util";

const PAD = 14;

export function EmphasisBehind({ o }: { o: FrameObject }) {
  const e = o.emph;
  if (!e || e.terms) return null;
  const b = o.bbox;
  const a = e.amount;
  switch (e.style) {
    case "glow":
      return (
        <rect
          x={fmt(b.x - PAD * 1.6)}
          y={fmt(b.y - PAD * 1.6)}
          width={fmt(b.w + PAD * 3.2)}
          height={fmt(b.h + PAD * 3.2)}
          rx={fmt(Math.min(b.w, b.h) * 0.3 + 18)}
          fill={e.color}
          opacity={fmt(0.55 * a, 3)}
          filter="url(#fx-glow)"
        />
      );
    case "color":
      return (
        <rect
          x={fmt(b.x - PAD)}
          y={fmt(b.y - PAD * 0.6)}
          width={fmt(b.w + PAD * 2)}
          height={fmt(b.h + PAD * 1.2)}
          rx={12}
          fill={withAlpha(e.color, 0.45)}
          opacity={fmt(a, 3)}
        />
      );
    case "pulse": {
      const r = Math.hypot(b.w, b.h) / 2;
      return (
        <circle
          cx={fmt(b.x + b.w / 2)}
          cy={fmt(b.y + b.h / 2)}
          r={fmt(r + 10 + 40 * e.u)}
          fill="none"
          stroke={e.color}
          strokeWidth={fmt(6 * (1 - e.u) + 1)}
          opacity={fmt(0.8 * (1 - e.u), 3)}
        />
      );
    }
    default:
      return null;
  }
}

export function EmphasisFront({ o }: { o: FrameObject }) {
  const e = o.emph;
  if (!e || e.terms) return null;
  const b = o.bbox;
  const a = e.amount;
  const draw = Math.min(1, e.u / 0.25);
  const dash = draw < 1 ? { pathLength: 1, strokeDasharray: "1 1", strokeDashoffset: fmt(1 - draw, 4) } : {};
  switch (e.style) {
    case "box":
      return (
        <rect
          x={fmt(b.x - PAD)}
          y={fmt(b.y - PAD)}
          width={fmt(b.w + 2 * PAD)}
          height={fmt(b.h + 2 * PAD)}
          rx={14}
          fill="none"
          stroke={e.color}
          strokeWidth={5}
          strokeLinecap="round"
          opacity={fmt(a, 3)}
          {...dash}
        />
      );
    case "circle":
      return (
        <ellipse
          cx={fmt(b.x + b.w / 2)}
          cy={fmt(b.y + b.h / 2)}
          rx={fmt(b.w / 2 + PAD * 1.8)}
          ry={fmt(b.h / 2 + PAD * 1.4)}
          fill="none"
          stroke={e.color}
          strokeWidth={5}
          strokeLinecap="round"
          opacity={fmt(a, 3)}
          {...dash}
        />
      );
    case "underline": {
      const y = b.y + b.h + 10;
      return (
        <path
          d={`M${fmt(b.x - 4)} ${fmt(y)} Q ${fmt(b.x + b.w / 2)} ${fmt(y + 10)} ${fmt(b.x + b.w + 4)} ${fmt(y)}`}
          fill="none"
          stroke={e.color}
          strokeWidth={7}
          strokeLinecap="round"
          opacity={fmt(a, 3)}
          {...dash}
        />
      );
    }
    case "flash":
      return (
        <rect
          x={fmt(b.x - 6)}
          y={fmt(b.y - 6)}
          width={fmt(b.w + 12)}
          height={fmt(b.h + 12)}
          rx={12}
          fill="#ffffff"
          opacity={fmt(0.7 * a, 3)}
        />
      );
    default:
      return null;
  }
}
