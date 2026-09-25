/** Renderers for Tier 0 primitives. */
import {
  ellipsePath,
  layoutText,
  polygonPath,
  polygonPoints,
  resolveTextSize,
  resolveWeight,
  roundedRectPath,
  type TextAlign,
} from "@animator/engine";
import { GlyphText } from "../GlyphText";
import type { RenderProps } from "../types";
import { darken, drawAttrs, fmt, lighten, n, s, shapePaint, uid } from "../util";

/** A filled, optionally stroked outline with a subtle vertical gradient for depth. */
export function Shape({ p, d, radial = false }: { p: RenderProps; d: string; radial?: boolean }) {
  const { o, theme } = p;
  const paint = shapePaint(o);
  const gradId = uid(o.id, "fill");
  const useGrad = theme.effects.gradients;
  return (
    <g>
      {useGrad && (
        <defs>
          {radial ? (
            <radialGradient id={gradId} cx="0.38" cy="0.32" r="0.75">
              <stop offset="0" stopColor={lighten(o.fill, 0.1)} />
              <stop offset="1" stopColor={darken(o.fill, 0.05)} />
            </radialGradient>
          ) : (
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={lighten(o.fill, 0.05)} />
              <stop offset="1" stopColor={darken(o.fill, 0.04)} />
            </linearGradient>
          )}
        </defs>
      )}
      <path
        d={d}
        fill={useGrad ? `url(#${gradId})` : o.fill}
        fillOpacity={paint.fillOpacity}
        stroke={o.stroke}
        strokeWidth={paint.strokeWidth}
        strokeOpacity={paint.strokeOpacity}
        strokeLinejoin="round"
        strokeDasharray={o.dashed && o.draw >= 1 ? "12 9" : undefined}
        {...paint.dash}
      />
    </g>
  );
}

const morphed = (p: RenderProps): string | null =>
  typeof p.o.params.__morphD === "string" ? p.o.params.__morphD : null;

export function CircleR(p: RenderProps) {
  const r = n(p.o.params.radius, 50);
  return <Shape p={p} d={morphed(p) ?? ellipsePath(r, r)} radial />;
}

export function EllipseR(p: RenderProps) {
  return <Shape p={p} d={morphed(p) ?? ellipsePath(n(p.o.params.rx, 80), n(p.o.params.ry, 50))} radial />;
}

export function RectangleR(p: RenderProps) {
  const w = n(p.o.params.width, 200);
  const h = n(p.o.params.height, 120);
  return <Shape p={p} d={morphed(p) ?? roundedRectPath(w, h, n(p.o.params.cornerRadius, 0))} />;
}

export function PolygonR(p: RenderProps) {
  return <Shape p={p} d={morphed(p) ?? polygonPath(polygonPoints(p.o.params))} />;
}

export function PathR(p: RenderProps) {
  const { o } = p;
  const d = morphed(p) ?? s(o.params.d, "M0 0");
  const closed = o.params.closed === true || morphed(p) !== null;
  if (closed) return <Shape p={p} d={d} />;
  return (
    <path
      d={d}
      fill="none"
      stroke={o.stroke}
      strokeWidth={o.strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeDasharray={o.dashed && o.draw >= 1 ? "12 9" : undefined}
      {...drawAttrs(o.draw)}
    />
  );
}

export function LineR({ o }: RenderProps) {
  const a = o.params.from as { x: number; y: number };
  const b = o.params.to as { x: number; y: number };
  const d = `M${fmt(a.x)} ${fmt(a.y)}L${fmt(b.x)} ${fmt(b.y)}`;
  if (o.dashed) {
    const maskId = uid(o.id, "draw");
    return (
      <g>
        {o.draw < 1 && (
          <mask id={maskId} maskUnits="userSpaceOnUse">
            <path d={d} stroke="#fff" strokeWidth={o.strokeWidth + 4} fill="none" {...drawAttrs(o.draw)} />
          </mask>
        )}
        <path
          d={d}
          stroke={o.stroke}
          strokeWidth={o.strokeWidth}
          strokeLinecap="round"
          strokeDasharray="12 10"
          mask={o.draw < 1 ? `url(#${maskId})` : undefined}
        />
      </g>
    );
  }
  return <path d={d} stroke={o.stroke} strokeWidth={o.strokeWidth} strokeLinecap="round" {...drawAttrs(o.draw)} />;
}

export function TextR({ o, theme }: RenderProps) {
  const layout = layoutText(s(o.params.text, ""), {
    size: resolveTextSize(o.params, theme),
    weight: resolveWeight(o.params),
    align: s(o.params.align, "center") as TextAlign,
    maxWidth: n(o.params.maxWidth, 0) || undefined,
    lineHeight: n(o.params.lineHeight, theme.typography.lineHeight),
  });
  return (
    <GlyphText
      layout={layout}
      color={o.text}
      emphColor={theme.colors.accent}
      reveal={o.reveal}
      x={-layout.width / 2}
      y={-layout.height / 2}
    />
  );
}

export function ImageR({ o, theme }: RenderProps) {
  const w = n(o.params.width, 240);
  const h = n(o.params.height, 160);
  const label = layoutText(s(o.params.alt, s(o.params.asset, "image")), {
    size: 20,
    maxWidth: w - 20,
    align: "center",
  });
  return (
    <g>
      <rect
        x={-w / 2}
        y={-h / 2}
        width={w}
        height={h}
        rx={14}
        fill={o.fill}
        stroke={o.stroke}
        strokeWidth={2}
        strokeDasharray="10 8"
      />
      <path
        d={`M${-w / 4} ${h / 8} l${w / 8} ${-h / 5} l${w / 10} ${h / 8} l${w / 12} ${-h / 12} l${w / 8} ${h / 6.5}Z`}
        fill={theme.colors.muted}
        opacity={0.5}
      />
      <circle cx={w / 5} cy={-h / 6} r={Math.min(w, h) / 14} fill={theme.colors.muted} opacity={0.5} />
      <GlyphText layout={label} color={theme.colors.muted} x={-label.width / 2} y={h / 2 - label.height - 8} />
    </g>
  );
}

export const GroupR = () => null;
