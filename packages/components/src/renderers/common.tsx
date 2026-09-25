/** Drawing helpers shared by renderers. */
import { invert, type Mat, resolveColor, type TextLayout, type Theme } from "@animator/engine";
import { GlyphText } from "../GlyphText";
import { fmt, withAlpha } from "../util";

export const col = (theme: Theme, ref: unknown, fallback = "text"): string =>
  resolveColor(theme, typeof ref === "string" ? ref : undefined, fallback as never);

/** World point -> the object's local frame (for pointers and leader lines). */
export function toLocal(m: Mat, p: { x: number; y: number }): { x: number; y: number } {
  const inv = invert(m);
  return { x: inv[0] * p.x + inv[2] * p.y + inv[4], y: inv[1] * p.x + inv[3] * p.y + inv[5] };
}

/** Text layout centered at (cx, cy). */
export function CenteredText(props: {
  layout: TextLayout;
  cx: number;
  cy: number;
  color: string;
  emphColor?: string;
  reveal?: number;
  halo?: string;
}) {
  const { layout, cx, cy } = props;
  const x = cx - layout.width / 2;
  const y = cy - layout.height / 2;
  return (
    <g>
      {props.halo && (
        <g stroke={props.halo} strokeWidth={layout.size * 0.18} strokeLinejoin="round" opacity={0.9}>
          <GlyphText layout={layout} color={props.halo} x={x} y={y} reveal={props.reveal} />
        </g>
      )}
      <GlyphText layout={layout} color={props.color} emphColor={props.emphColor} x={x} y={y} reveal={props.reveal} />
    </g>
  );
}

export function Pill(props: {
  cx: number;
  cy: number;
  w: number;
  h: number;
  fill: string;
  stroke?: string;
  strokeWidth?: number;
  opacity?: number;
}) {
  const r = Math.min(props.h / 2, 999);
  return (
    <rect
      x={fmt(props.cx - props.w / 2)}
      y={fmt(props.cy - props.h / 2)}
      width={fmt(props.w)}
      height={fmt(props.h)}
      rx={fmt(r)}
      fill={props.fill}
      stroke={props.stroke}
      strokeWidth={props.strokeWidth}
      opacity={props.opacity}
    />
  );
}

/** A soft drop shadow drawn as a blurred shape under a card (cheaper than a filter on the whole group). */
export function CardShadow({
  x,
  y,
  w,
  h,
  r,
  theme,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  r: number;
  theme: Theme;
}) {
  return (
    <rect
      x={fmt(x + 2)}
      y={fmt(y + 8)}
      width={fmt(w - 4)}
      height={fmt(h)}
      rx={fmt(r)}
      fill={withAlpha(theme.colors.shadow, theme.mode === "dark" ? 0.4 : 0.12)}
      filter="url(#fx-soft)"
    />
  );
}

export const easeOut = (u: number) => 1 - (1 - Math.max(0, Math.min(1, u))) ** 3;
