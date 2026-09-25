/** Renderers for annotation components. Geometry comes from the engine's layout functions. */
import {
  arrowLayout,
  bracketLayout,
  calloutLayout,
  dimensionLayout,
  labelLayout,
  layoutText,
  noteLayout,
  objectRefOf,
  resolveTextSize,
  type Vec2,
  vectorLayout,
} from "@animator/engine";
import { GlyphText } from "../GlyphText";
import type { RenderProps } from "../types";
import { darken, drawAttrs, fmt, lighten, mixHex, n, s, withAlpha } from "../util";
import { CardShadow, CenteredText, col, easeOut, Pill, toLocal } from "./common";
import { IconGlyph } from "./icons";

const P = (v: Vec2) => `${fmt(v.x)} ${fmt(v.y)}`;

function head(tip: Vec2, dir: Vec2, length: number, width: number): string {
  const nx = -dir.y;
  const ny = dir.x;
  const bx = tip.x - dir.x * length;
  const by = tip.y - dir.y * length;
  return `M${P(tip)}L${fmt(bx + (nx * width) / 2)} ${fmt(by + (ny * width) / 2)}L${fmt(bx - (nx * width) / 2)} ${fmt(by - (ny * width) / 2)}Z`;
}

export function ArrowR({ o }: RenderProps) {
  const l = arrowLayout(o.params);
  const mode = s(o.params.head, "end");
  const h = l.head;
  // Pull the shaft back under the head so the tip stays crisp.
  const end =
    mode === "end" || mode === "both"
      ? { x: l.end.x - l.endDir.x * h * 0.6, y: l.end.y - l.endDir.y * h * 0.6 }
      : l.end;
  const start =
    mode === "start" || mode === "both"
      ? { x: l.start.x + l.startDir.x * h * 0.6, y: l.start.y + l.startDir.y * h * 0.6 }
      : l.start;
  const d = l.control ? `M${P(start)}Q${P(l.control)} ${P(end)}` : `M${P(start)}L${P(end)}`;
  const headOpacity = o.draw >= 1 ? 1 : Math.max(0, (o.draw - 0.85) / 0.15);
  return (
    <g>
      <path
        d={d}
        fill="none"
        stroke={o.stroke}
        strokeWidth={o.strokeWidth}
        strokeLinecap="round"
        strokeDasharray={o.dashed && o.draw >= 1 ? "14 10" : undefined}
        {...drawAttrs(o.draw)}
      />
      {(mode === "end" || mode === "both") && (
        <path
          d={head(l.end, l.endDir, h, h * 0.95)}
          fill={o.stroke}
          stroke={o.stroke}
          strokeWidth={2}
          strokeLinejoin="round"
          opacity={headOpacity}
        />
      )}
      {(mode === "start" || mode === "both") && (
        <path
          d={head(l.start, { x: -l.startDir.x, y: -l.startDir.y }, h, h * 0.95)}
          fill={o.stroke}
          stroke={o.stroke}
          strokeWidth={2}
          strokeLinejoin="round"
          opacity={headOpacity}
        />
      )}
    </g>
  );
}

export function VectorArrowR({ o, theme }: RenderProps) {
  const l = vectorLayout(o.params, theme);
  const color = col(theme, o.params.role, "force_applied");
  const grow = easeOut(o.draw);
  const len = l.length * grow;
  if (len <= 0.5) return null;
  const tip = { x: l.tail.x + l.dir.x * len, y: l.tail.y + l.dir.y * len };
  const hl = Math.min(l.headLength, len * 0.6);
  const shaftEnd = { x: tip.x - l.dir.x * hl * 0.85, y: tip.y - l.dir.y * hl * 0.85 };
  const nx = -l.dir.y;
  const ny = l.dir.x;
  const off = l.thickness * 0.16;
  const labelAlpha = o.draw >= 1 ? 1 : Math.max(0, (o.draw - 0.6) / 0.4);
  return (
    <g>
      <line
        x1={fmt(l.tail.x)}
        y1={fmt(l.tail.y)}
        x2={fmt(shaftEnd.x)}
        y2={fmt(shaftEnd.y)}
        stroke={darken(color, 0.06)}
        strokeWidth={l.thickness}
        strokeLinecap="round"
      />
      <line
        x1={fmt(l.tail.x + nx * off)}
        y1={fmt(l.tail.y + ny * off)}
        x2={fmt(shaftEnd.x + nx * off)}
        y2={fmt(shaftEnd.y + ny * off)}
        stroke={lighten(color, 0.12)}
        strokeWidth={fmt(l.thickness * 0.34)}
        strokeLinecap="round"
        opacity={0.8}
      />
      <path
        d={head(tip, l.dir, hl, l.headWidth)}
        fill={color}
        stroke={darken(color, 0.08)}
        strokeWidth={2.5}
        strokeLinejoin="round"
      />
      <circle
        cx={fmt(l.tail.x)}
        cy={fmt(l.tail.y)}
        r={fmt(l.thickness * 0.72)}
        fill={theme.colors.surface}
        stroke={darken(color, 0.06)}
        strokeWidth={3}
      />
      {l.label && labelAlpha > 0 && (
        <g opacity={fmt(labelAlpha, 3)}>
          <Pill
            cx={l.label.center.x}
            cy={l.label.center.y}
            w={l.label.w}
            h={l.label.h}
            fill={theme.colors.surface}
            stroke={color}
            strokeWidth={2.5}
          />
          <CenteredText layout={l.label.text} cx={l.label.center.x} cy={l.label.center.y} color={darken(color, 0.12)} />
        </g>
      )}
    </g>
  );
}

export function DimensionR({ o, theme }: RenderProps) {
  const l = dimensionLayout(o.params, theme);
  const color = col(theme, o.params.role, "muted");
  const u = { x: l.b.x - l.a.x, y: l.b.y - l.a.y };
  const len = Math.hypot(u.x, u.y) || 1;
  const d = { x: u.x / len, y: u.y / len };
  const tick = (p: Vec2) =>
    `M${fmt(p.x + l.normal.x * 11)} ${fmt(p.y + l.normal.y * 11)}L${fmt(p.x - l.normal.x * 11)} ${fmt(p.y - l.normal.y * 11)}`;
  const chevron = (p: Vec2, dir: Vec2) =>
    `M${fmt(p.x + dir.x * 12 + l.normal.x * 7)} ${fmt(p.y + dir.y * 12 + l.normal.y * 7)}L${P(p)}L${fmt(p.x + dir.x * 12 - l.normal.x * 7)} ${fmt(p.y + dir.y * 12 - l.normal.y * 7)}`;
  return (
    <g stroke={color} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" fill="none">
      <path d={`M${P(l.a)}L${P(l.b)}`} {...drawAttrs(o.draw)} />
      <g opacity={o.draw >= 1 ? 1 : Math.max(0, (o.draw - 0.7) / 0.3)}>
        <path d={tick(l.a)} />
        <path d={tick(l.b)} />
        <path d={chevron(l.a, d)} />
        <path d={chevron(l.b, { x: -d.x, y: -d.y })} />
        {l.label && (
          <g stroke="none">
            <Pill
              cx={l.label.center.x}
              cy={l.label.center.y}
              w={l.label.w}
              h={l.label.h}
              fill={theme.colors.surface}
              stroke={withAlpha(color, 0.5)}
              strokeWidth={2}
            />
            <CenteredText
              layout={l.label.text}
              cx={l.label.center.x}
              cy={l.label.center.y}
              color={darken(color, 0.1)}
              emphColor={theme.colors.accent}
            />
          </g>
        )}
      </g>
    </g>
  );
}

export function BracketR({ o, theme }: RenderProps) {
  const l = bracketLayout(o.params, theme);
  const color = col(theme, o.params.role, "muted");
  const len = Math.hypot(l.b.x - l.a.x, l.b.y - l.a.y);
  const r = Math.min(l.depth * 0.8, len / 4);
  const half = { x: l.n.x * (l.depth / 2), y: l.n.y * (l.depth / 2) };
  const at = (p: Vec2, du: number) => ({ x: p.x + half.x + l.u.x * du, y: p.y + half.y + l.u.y * du });
  const m = { x: (l.a.x + l.b.x) / 2, y: (l.a.y + l.b.y) / 2 };
  const d = `M${P(l.a)}Q${P({ x: l.a.x + half.x, y: l.a.y + half.y })} ${P(at(l.a, r))}L${P(at(m, -r))}Q${P({ x: m.x + half.x, y: m.y + half.y })} ${P(l.tip)}Q${P({ x: m.x + half.x, y: m.y + half.y })} ${P(at(m, r))}L${P(at(l.b, -r))}Q${P({ x: l.b.x + half.x, y: l.b.y + half.y })} ${P(l.b)}`;
  return (
    <g>
      <path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth={4}
        strokeLinecap="round"
        strokeLinejoin="round"
        {...drawAttrs(o.draw)}
      />
      {l.label && o.draw > 0.7 && (
        <CenteredText
          layout={l.label.text}
          cx={l.label.center.x}
          cy={l.label.center.y}
          color={darken(color, 0.1)}
          emphColor={theme.colors.accent}
        />
      )}
    </g>
  );
}

export function LabelR({ o, theme }: RenderProps) {
  const l = labelLayout(o.params, theme);
  const color = col(theme, o.params.color, "text");
  const leader = o.params.pointTo as Vec2 | undefined;
  let line = null;
  if (leader && typeof leader.x === "number") {
    const t = toLocal(o.matrix, leader);
    // Leave the pill from the edge facing the target.
    const k = Math.min(
      Math.abs(t.x) > 1e-6 ? l.w / 2 / Math.abs(t.x) : 1e9,
      Math.abs(t.y) > 1e-6 ? l.h / 2 / Math.abs(t.y) : 1e9,
      1,
    );
    const start = { x: t.x * k, y: t.y * k };
    line = (
      <g>
        <line
          x1={fmt(start.x)}
          y1={fmt(start.y)}
          x2={fmt(t.x)}
          y2={fmt(t.y)}
          stroke={mixHex(color, theme.colors.surface, 0.3)}
          strokeWidth={2.5}
          strokeDasharray="2 6"
          strokeLinecap="round"
        />
        <circle cx={fmt(t.x)} cy={fmt(t.y)} r={5} fill={color} stroke={theme.colors.surface} strokeWidth={2} />
      </g>
    );
  }
  const style = l.style;
  return (
    <g>
      {line}
      {style === "pill" && (
        <Pill
          cx={0}
          cy={0}
          w={l.w}
          h={l.h}
          fill={withAlpha(theme.colors.surface, 0.96)}
          stroke={mixHex(color, theme.colors.surface, 0.55)}
          strokeWidth={2.5}
        />
      )}
      {style === "tag" && <Pill cx={0} cy={0} w={l.w} h={l.h} fill={color} />}
      <CenteredText
        layout={l.text}
        cx={0}
        cy={0}
        color={style === "tag" ? theme.colors.surface : style === "plain" ? color : darken(color, 0.02)}
        emphColor={style === "tag" ? theme.colors.surface : theme.colors.accent}
        halo={style === "plain" ? withAlpha(theme.colors.background, 0.85) : undefined}
      />
    </g>
  );
}

export function CalloutR({ o, theme }: RenderProps) {
  const l = calloutLayout(o.params, theme);
  const color = col(theme, o.params.role, "accent");
  const target = o.params.target as Vec2 | undefined;
  let pointer = null;
  if (target && typeof target.x === "number") {
    const t = toLocal(o.matrix, target);
    const k = Math.min(
      Math.abs(t.x) > 1e-6 ? l.w / 2 / Math.abs(t.x) : 1e9,
      Math.abs(t.y) > 1e-6 ? l.h / 2 / Math.abs(t.y) : 1e9,
      1,
    );
    const edge = { x: t.x * k, y: t.y * k };
    const dist = Math.hypot(t.x - edge.x, t.y - edge.y);
    if (dist > 8) {
      const ux = (t.x - edge.x) / dist;
      const uy = (t.y - edge.y) / dist;
      pointer = (
        <g>
          <path
            d={`M${fmt(edge.x - uy * 12)} ${fmt(edge.y + ux * 12)}L${fmt(edge.x + ux * Math.min(28, dist))} ${fmt(edge.y + uy * Math.min(28, dist))}L${fmt(edge.x + uy * 12)} ${fmt(edge.y - ux * 12)}Z`}
            fill={theme.colors.surface}
            stroke={color}
            strokeWidth={2.5}
            strokeLinejoin="round"
          />
          <line
            x1={fmt(edge.x + ux * 28)}
            y1={fmt(edge.y + uy * 28)}
            x2={fmt(t.x)}
            y2={fmt(t.y)}
            stroke={color}
            strokeWidth={2.5}
            strokeDasharray="3 6"
            strokeLinecap="round"
          />
          <circle cx={fmt(t.x)} cy={fmt(t.y)} r={6} fill={color} stroke={theme.colors.surface} strokeWidth={2.5} />
        </g>
      );
    }
  }
  return (
    <g>
      <CardShadow x={-l.w / 2} y={-l.h / 2} w={l.w} h={l.h} r={16} theme={theme} />
      {pointer}
      <rect
        x={fmt(-l.w / 2)}
        y={fmt(-l.h / 2)}
        width={fmt(l.w)}
        height={fmt(l.h)}
        rx={16}
        fill={theme.colors.surface}
        stroke={color}
        strokeWidth={2.5}
      />
      <GlyphText
        layout={l.text}
        color={theme.colors.text}
        emphColor={color}
        x={-l.text.width / 2}
        y={-l.text.height / 2}
        reveal={o.reveal}
      />
    </g>
  );
}

export function NoteR({ o, theme }: RenderProps) {
  const l = noteLayout(o.params, theme);
  const color = col(theme, o.params.role, "accent");
  const x0 = -l.width / 2;
  const y0 = -l.height / 2;
  const tx = x0 + l.pad + (l.iconSize ? l.iconSize + 14 : 0);
  return (
    <g>
      <CardShadow x={x0} y={y0} w={l.width} h={l.height} r={18} theme={theme} />
      <rect
        x={fmt(x0)}
        y={fmt(y0)}
        width={fmt(l.width)}
        height={fmt(l.height)}
        rx={18}
        fill={theme.colors.surface}
        stroke={mixHex(color, theme.colors.surface, 0.7)}
        strokeWidth={2}
      />
      <rect x={fmt(x0)} y={fmt(y0 + 14)} width={6} height={fmt(l.height - 28)} rx={3} fill={color} />
      {l.iconSize > 0 && (
        <g transform={`translate(${fmt(x0 + l.pad + l.iconSize / 2)} ${fmt(y0 + l.pad + l.iconSize / 2)})`}>
          <circle r={l.iconSize / 2} fill={mixHex(color, theme.colors.surface, 0.82)} />
          <IconGlyph name={s(o.params.icon, "info")} size={l.iconSize * 0.62} color={color} />
        </g>
      )}
      {l.title && <GlyphText layout={l.title} color={darken(color, 0.08)} x={tx} y={y0 + l.pad} />}
      <GlyphText
        layout={l.body}
        color={theme.colors.text}
        emphColor={color}
        x={tx}
        y={y0 + l.pad + (l.title ? l.title.height + 6 : 0)}
        reveal={o.reveal}
      />
    </g>
  );
}

export function MarkerR({ o, theme }: RenderProps) {
  const ref = objectRefOf(o.params, "target");
  if (!ref) return null;
  const pad = n(o.params.padding, 14);
  const b = ref.box;
  const color = col(theme, o.params.role, "highlight");
  const shape = s(o.params.shape, "box");
  const dash = drawAttrs(o.draw);
  if (shape === "glow") {
    return (
      <rect
        x={fmt(b.x - pad)}
        y={fmt(b.y - pad)}
        width={fmt(b.w + 2 * pad)}
        height={fmt(b.h + 2 * pad)}
        rx={20}
        fill={color}
        opacity={0.4 * o.draw}
        filter="url(#fx-glow)"
      />
    );
  }
  if (shape === "circle") {
    return (
      <ellipse
        cx={fmt(b.x + b.w / 2)}
        cy={fmt(b.y + b.h / 2)}
        rx={fmt(b.w / 2 + pad * 1.6)}
        ry={fmt(b.h / 2 + pad * 1.2)}
        fill="none"
        stroke={color}
        strokeWidth={5}
        {...dash}
      />
    );
  }
  if (shape === "underline") {
    const y = b.y + b.h + pad * 0.6;
    return (
      <path
        d={`M${fmt(b.x)} ${fmt(y)}Q${fmt(b.x + b.w / 2)} ${fmt(y + 10)} ${fmt(b.x + b.w)} ${fmt(y)}`}
        fill="none"
        stroke={color}
        strokeWidth={7}
        strokeLinecap="round"
        {...dash}
      />
    );
  }
  return (
    <rect
      x={fmt(b.x - pad)}
      y={fmt(b.y - pad)}
      width={fmt(b.w + 2 * pad)}
      height={fmt(b.h + 2 * pad)}
      rx={16}
      fill="none"
      stroke={color}
      strokeWidth={5}
      {...dash}
    />
  );
}

export function CaptionBlockR({ o, theme }: RenderProps) {
  const layout = layoutText(s(o.params.text, ""), {
    size: resolveTextSize(o.params, theme),
    maxWidth: n(o.params.maxWidth, 900),
    align: "center",
  });
  const w = layout.width + 48;
  const h = layout.height + 28;
  return (
    <g>
      <rect
        x={-w / 2}
        y={-h / 2}
        width={w}
        height={h}
        rx={16}
        fill={withAlpha(theme.colors.surface, 0.9)}
        stroke={theme.colors.grid}
        strokeWidth={1.5}
      />
      <CenteredText
        layout={layout}
        cx={0}
        cy={0}
        color={theme.colors.text}
        emphColor={theme.colors.accent}
        reveal={o.reveal}
      />
    </g>
  );
}
