/** Renderers for engagement components. */
import {
  bubbleLayout,
  cardLayout,
  hashString,
  layoutText,
  lightness,
  pillLayout,
  rand01,
  resolveTextSize,
  titleCardLayout,
} from "@animator/engine";
import type { ReactNode } from "react";
import { GlyphText } from "../GlyphText";
import type { RenderProps } from "../types";
import { darken, fmt, mixHex, n, s, withAlpha } from "../util";
import { CardShadow, CenteredText, col, easeOut, toLocal } from "./common";
import { IconGlyph } from "./icons";

export function TitleCardR({ o, theme }: RenderProps) {
  const l = titleCardLayout(o.params, theme);
  const color = col(theme, o.params.role, "accent");
  const soft = mixHex(color, theme.colors.surface, 0.85);
  let y = -l.height / 2;
  const parts: ReactNode[] = [];
  const reveal = o.reveal;
  // Stage the reveal: icon, kicker, title (write-on), underline, subtitle.
  const stage = (from: number, to: number) => Math.max(0, Math.min(1, (reveal - from) / (to - from)));
  if (l.icon) {
    const a = easeOut(stage(0, 0.2));
    parts.push(
      <g key="icon" transform={`translate(0 ${fmt(y + l.icon / 2)}) scale(${fmt(0.6 + 0.4 * a)})`} opacity={fmt(a, 3)}>
        <circle r={l.icon / 2} fill={soft} />
        <circle r={l.icon / 2 - 8} fill="none" stroke={color} strokeWidth={2} opacity={0.35} />
        <IconGlyph name={s(o.params.icon, "sparkle")} size={l.icon * 0.55} color={color} strokeWidth={2.2} />
      </g>,
    );
    y += l.icon + l.gap;
  }
  if (l.kicker) {
    parts.push(
      <GlyphText key="kicker" layout={l.kicker} color={color} x={-l.kicker.width / 2} y={y} reveal={stage(0.1, 0.3)} />,
    );
    y += l.kicker.height + 10;
  }
  parts.push(
    <GlyphText
      key="title"
      layout={l.title}
      color={theme.colors.text}
      emphColor={color}
      x={-l.title.width / 2}
      y={y}
      reveal={stage(0.15, 0.75)}
    />,
  );
  y += l.title.height + 8;
  const uw = Math.min(l.title.width * 0.6, 420);
  const u = stage(0.6, 0.9);
  parts.push(
    <path
      key="swoosh"
      d={`M${fmt(-uw / 2)} ${fmt(y + 10)}Q${fmt(0)} ${fmt(y + 24)} ${fmt(uw / 2)} ${fmt(y + 6)}`}
      fill="none"
      stroke={color}
      strokeWidth={8}
      strokeLinecap="round"
      pathLength={1}
      strokeDasharray="1 1"
      strokeDashoffset={fmt(1 - u, 4)}
      opacity={u > 0 ? 1 : 0}
    />,
  );
  y += 34;
  if (l.subtitle) {
    const a = stage(0.75, 1);
    parts.push(
      <GlyphText
        key="sub"
        layout={l.subtitle}
        color={theme.colors.muted}
        emphColor={color}
        x={-l.subtitle.width / 2}
        y={y + l.gap + (1 - a) * 12}
        reveal={a >= 1 ? 1 : a}
      />,
    );
  }
  const w = Math.max(l.title.width, 400);
  return (
    <g>
      <circle cx={fmt(-w * 0.62)} cy={fmt(-l.height * 0.2)} r={70} fill={soft} opacity={0.8} />
      <circle
        cx={fmt(w * 0.64)}
        cy={fmt(l.height * 0.3)}
        r={46}
        fill={mixHex(theme.colors.highlight, theme.colors.surface, 0.7)}
        opacity={0.8}
      />
      <circle cx={fmt(w * 0.56)} cy={fmt(-l.height * 0.42)} r={14} fill={color} opacity={0.35} />
      <circle cx={fmt(-w * 0.5)} cy={fmt(l.height * 0.46)} r={10} fill={theme.colors.highlight} opacity={0.7} />
      {parts}
    </g>
  );
}

const MARKERS: Record<string, (i: number, size: number, color: string, surface: string) => ReactNode> = {
  check: (_i, size, color, surface) => (
    <g>
      <circle r={size / 2} fill={color} />
      <path
        d={`M${fmt(-size * 0.22)} 0L${fmt(-size * 0.05)} ${fmt(size * 0.17)}L${fmt(size * 0.24)} ${fmt(-size * 0.17)}`}
        fill="none"
        stroke={surface}
        strokeWidth={fmt(size * 0.12)}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </g>
  ),
  dot: (_i, size, color) => <circle r={size * 0.22} fill={color} />,
  star: (_i, size, color) => <IconGlyph name="star" size={size} color={color} />,
  arrow: (_i, size, color) => (
    <path
      d={`M${fmt(-size * 0.25)} ${fmt(-size * 0.25)}L${fmt(size * 0.2)} 0L${fmt(-size * 0.25)} ${fmt(size * 0.25)}`}
      fill="none"
      stroke={color}
      strokeWidth={fmt(size * 0.14)}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ),
};

export function CardR({ o, theme }: RenderProps) {
  const l = cardLayout(o.params, theme);
  const color = col(theme, o.params.role, "accent");
  const x0 = -l.width / 2;
  const y0 = -l.height / 2;
  const marker = s(o.params.marker, "check");
  const total = l.items.length;
  return (
    <g>
      <CardShadow x={x0} y={y0} w={l.width} h={l.height} r={24} theme={theme} />
      <rect
        x={fmt(x0)}
        y={fmt(y0)}
        width={fmt(l.width)}
        height={fmt(l.height)}
        rx={24}
        fill={theme.colors.surface}
        stroke={theme.colors.grid}
        strokeWidth={2}
      />
      <rect x={fmt(x0 + 24)} y={fmt(y0)} width={fmt(l.width - 48)} height={6} rx={3} fill={color} />
      {l.title && (
        <GlyphText layout={l.title} color={theme.colors.text} emphColor={color} x={x0 + l.pad} y={y0 + l.pad} />
      )}
      {l.items.map((item, i) => {
        const f = easeOut(Math.max(0, Math.min(1, o.reveal * (total + 0.6) - i)));
        if (f <= 0) return null;
        const size = l.bullet;
        const lineH = item.text.lines[0]?.baseline ?? size;
        const markerNode =
          marker === "number" ? (
            <g>
              <circle r={size / 2} fill={mixHex(color, theme.colors.surface, 0.8)} />
              <CenteredText
                layout={layoutText(String(i + 1), { size: size * 0.55, weight: 700 })}
                cx={0}
                cy={0}
                color={darken(color, 0.1)}
              />
            </g>
          ) : (
            (MARKERS[marker] ?? MARKERS.check)?.(i, size * 0.9, color, theme.colors.surface)
          );
        return (
          <g key={`item-${i}`} opacity={fmt(f, 3)} transform={`translate(${fmt((1 - f) * -24)} 0)`}>
            <g transform={`translate(${fmt(x0 + l.pad + size / 2)} ${fmt(y0 + item.y + lineH - size * 0.32)})`}>
              {markerNode}
            </g>
            <GlyphText
              layout={item.text}
              color={theme.colors.text}
              emphColor={color}
              x={x0 + l.pad + size + 12}
              y={y0 + item.y}
            />
          </g>
        );
      })}
    </g>
  );
}

export function IconR({ o, theme }: RenderProps) {
  const size = n(o.params.size, 72);
  const color = col(theme, o.params.role, "accent");
  return (
    <g>
      {o.params.badge !== false && (
        <>
          <circle r={size * 0.75} fill={mixHex(color, theme.colors.surface, 0.84)} />
          <circle r={size * 0.75 - 6} fill="none" stroke={color} strokeWidth={2} opacity={0.3} />
        </>
      )}
      <IconGlyph name={s(o.params.name, "info")} size={size} color={color} strokeWidth={2.1} />
    </g>
  );
}

export function BadgeR({ o, theme }: RenderProps) {
  const size = resolveTextSize(o.params, theme, "size", "small");
  const l = pillLayout(s(o.params.text, ""), size, 700);
  const color = col(theme, o.params.role, "accent");
  const iconW = o.params.icon ? size * 1.3 : 0;
  const w = l.w + iconW;
  const ink = lightness(color) > 0.72 ? theme.colors.text : "#ffffff";
  return (
    <g>
      <rect x={fmt(-w / 2)} y={fmt(-l.h / 2)} width={fmt(w)} height={fmt(l.h)} rx={fmt(l.h / 2)} fill={color} />
      {typeof o.params.icon === "string" && (
        <g transform={`translate(${fmt(-w / 2 + l.padX * 0.6 + size * 0.5)} 0)`}>
          <IconGlyph name={o.params.icon} size={size * 0.95} color={ink} strokeWidth={2.6} />
        </g>
      )}
      <CenteredText layout={l.text} cx={iconW / 2} cy={0} color={ink} />
    </g>
  );
}

export function SpeechBubbleR({ o, theme }: RenderProps) {
  const l = bubbleLayout(o.params, theme);
  const fill = col(theme, o.params.role, "surface");
  const stroke = mixHex(theme.colors.text, fill, 0.75);
  const x0 = -l.w / 2;
  const y0 = -l.h / 2;
  const target = o.params.pointTo as { x: number; y: number } | undefined;
  const t = target && typeof target.x === "number" ? toLocal(o.matrix, target) : { x: -l.w * 0.2, y: l.h / 2 + 50 };
  const len = Math.hypot(t.x, t.y) || 1;
  const k = Math.min(
    Math.abs(t.x) > 1e-6 ? l.w / 2 / Math.abs(t.x) : 1e9,
    Math.abs(t.y) > 1e-6 ? l.h / 2 / Math.abs(t.y) : 1e9,
    1,
  );
  const edge = { x: t.x * k, y: t.y * k };
  const ux = t.x / len;
  const uy = t.y / len;
  const tipDist = Math.min(56, Math.max(24, Math.hypot(t.x - edge.x, t.y - edge.y) * 0.6));
  const thought = s(o.params.kind, "speech") === "thought";
  return (
    <g>
      <CardShadow x={x0} y={y0} w={l.w} h={l.h} r={l.h / 2.2} theme={theme} />
      {thought ? (
        <>
          <circle
            cx={fmt(edge.x + ux * tipDist * 0.45)}
            cy={fmt(edge.y + uy * tipDist * 0.45)}
            r={11}
            fill={fill}
            stroke={stroke}
            strokeWidth={2.5}
          />
          <circle
            cx={fmt(edge.x + ux * tipDist)}
            cy={fmt(edge.y + uy * tipDist)}
            r={6}
            fill={fill}
            stroke={stroke}
            strokeWidth={2.5}
          />
        </>
      ) : (
        <path
          d={`M${fmt(edge.x - uy * 16 - ux * 6)} ${fmt(edge.y + ux * 16 - uy * 6)}L${fmt(edge.x + ux * tipDist)} ${fmt(edge.y + uy * tipDist)}L${fmt(edge.x + uy * 16 - ux * 6)} ${fmt(edge.y - ux * 16 - uy * 6)}Z`}
          fill={fill}
          stroke={stroke}
          strokeWidth={2.5}
          strokeLinejoin="round"
        />
      )}
      <rect
        x={fmt(x0)}
        y={fmt(y0)}
        width={fmt(l.w)}
        height={fmt(l.h)}
        rx={fmt(Math.min(28, l.h / 2.2))}
        fill={fill}
        stroke={stroke}
        strokeWidth={2.5}
      />
      {!thought && (
        <path
          d={`M${fmt(edge.x - uy * 14 - ux * 8)} ${fmt(edge.y + ux * 14 - uy * 8)}L${fmt(edge.x + uy * 14 - ux * 8)} ${fmt(edge.y - ux * 14 - uy * 8)}`}
          stroke={fill}
          strokeWidth={6}
        />
      )}
      <GlyphText
        layout={l.text}
        color={theme.colors.text}
        emphColor={theme.colors.accent}
        x={-l.text.width / 2}
        y={-l.text.height / 2}
        reveal={o.reveal}
      />
    </g>
  );
}

export function BurstR({ o, theme, seed }: RenderProps) {
  const u = o.reveal;
  if (u >= 1) return null;
  const count = Math.round(n(o.params.count, 26));
  const radius = n(o.params.radius, 170);
  const kind = s(o.params.kind, "confetti");
  const main = col(theme, o.params.role, "highlight");
  const palette = [
    main,
    theme.colors.accent,
    theme.colors.positive,
    theme.colors.object_b,
    theme.colors.fluid,
    theme.colors.object_a,
  ];
  const sd = (seed ^ hashString(o.id)) >>> 0;
  const out: ReactNode[] = [];
  const e = easeOut(u);
  const fade = 1 - u * u;
  for (let i = 0; i < count; i++) {
    const a = (i / count) * Math.PI * 2 + (rand01(sd, i, 1) - 0.5) * 0.4;
    const d = radius * e * (0.55 + 0.45 * rand01(sd, i, 2));
    const x = Math.cos(a) * d;
    const y = Math.sin(a) * d + u * u * 40;
    const c = palette[i % palette.length] ?? main;
    if (kind === "splash") {
      // Droplets thrown up and outwards, falling back under gravity.
      const aUp = Math.PI + (0.12 + 0.76 * (i / Math.max(1, count - 1))) * Math.PI;
      const speed = radius * (0.65 + 0.5 * rand01(sd, i, 2));
      const sx = Math.cos(aUp) * speed * u;
      const sy2 = Math.sin(aUp) * speed * u + 2.2 * radius * u * u;
      const r = 3 + rand01(sd, i, 3) * 5;
      out.push(
        <circle
          key={i}
          cx={fmt(sx)}
          cy={fmt(sy2)}
          r={fmt(r * (1 - 0.4 * u))}
          fill={main}
          opacity={fmt(0.9 * fade, 3)}
        />,
      );
    } else if (kind === "rays") {
      const r0 = radius * 0.25 * e;
      out.push(
        <line
          key={i}
          x1={fmt(Math.cos(a) * r0)}
          y1={fmt(Math.sin(a) * r0)}
          x2={fmt(Math.cos(a) * d)}
          y2={fmt(Math.sin(a) * d)}
          stroke={main}
          strokeWidth={5}
          strokeLinecap="round"
          opacity={fmt(fade, 3)}
        />,
      );
    } else if (kind === "stars") {
      out.push(
        <g key={i} transform={`translate(${fmt(x)} ${fmt(y)}) rotate(${fmt(u * 180 + i * 17)})`} opacity={fmt(fade, 3)}>
          <IconGlyph name="star" size={16 + rand01(sd, i, 3) * 14} color={c} strokeWidth={1} />
        </g>,
      );
    } else {
      const w = 8 + rand01(sd, i, 3) * 8;
      out.push(
        <rect
          key={i}
          x={fmt(x - w / 2)}
          y={fmt(y - 3)}
          width={fmt(w)}
          height={6}
          rx={2}
          fill={c}
          opacity={fmt(fade, 3)}
          transform={`rotate(${fmt(u * 540 * (rand01(sd, i, 4) - 0.5) + i * 23)} ${fmt(x)} ${fmt(y)})`}
        />,
      );
    }
  }
  return <g>{out}</g>;
}

export { withAlpha };
