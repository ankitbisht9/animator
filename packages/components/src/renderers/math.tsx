/** Renderers for math components. */
import {
  equationLayout,
  formatNumber,
  glyph,
  graphLayout,
  layoutText,
  readoutLayout,
  seriesPoints,
} from "@animator/engine";
import { GlyphText } from "../GlyphText";
import type { RenderProps } from "../types";
import { darken, fmt, mixHex, n, uid, withAlpha } from "../util";
import { CardShadow, col, easeOut } from "./common";

export function EquationR({ o, theme, prep }: RenderProps) {
  const l = equationLayout(o.params, prep);
  const color = col(theme, o.params.color, "text");
  const emph = o.emph?.terms ? o.emph : undefined;
  const ordered = l.parts.filter((p) => p.id !== null);
  const total = Math.max(1, ordered.length);
  return (
    <g>
      {l.box && (
        <>
          <CardShadow x={-l.width / 2} y={-l.height / 2} w={l.width} h={l.height} r={22} theme={theme} />
          <rect
            x={fmt(-l.width / 2)}
            y={fmt(-l.height / 2)}
            width={fmt(l.width)}
            height={fmt(l.height)}
            rx={22}
            fill={theme.colors.surface}
            stroke={theme.colors.grid}
            strokeWidth={2}
          />
        </>
      )}
      {emph &&
        l.parts
          .filter((p) => p.id && emph.terms?.includes(p.id))
          .map((p) => (
            <rect
              key={`hl-${p.id}`}
              x={fmt(p.bbox.x - 10)}
              y={fmt(p.bbox.y - 8)}
              width={fmt(p.bbox.w + 20)}
              height={fmt(p.bbox.h + 16)}
              rx={12}
              fill={withAlpha(emph.color, 0.42)}
              opacity={fmt(emph.amount, 3)}
            />
          ))}
      {l.parts.map((p, i) => {
        let f = 1;
        if (p.id && o.terms && p.id in o.terms) f = o.terms[p.id] ?? 1;
        if (o.reveal < 1) {
          const idx = p.id ? ordered.indexOf(p) : i;
          f = Math.min(f, Math.max(0, Math.min(1, o.reveal * (total + 1) - idx)));
        }
        if (f <= 0) return null;
        const hot = emph && p.id && emph.terms?.includes(p.id) ? emph.amount : 0;
        const fill = hot > 0 ? mixHex(color, theme.colors.accent, hot) : color;
        return (
          <path
            key={p.id ?? `base-${i}`}
            d={p.d}
            fill={fill}
            opacity={f < 1 ? fmt(f, 3) : undefined}
            transform={`translate(${fmt(l.ox)} ${fmt(l.oy + (1 - f) * 14)}) scale(${fmt(l.scale, 5)})`}
          />
        );
      })}
    </g>
  );
}

export function ReadoutR({ o, theme }: RenderProps) {
  const counting = o.params.countUp !== false && o.presence < 1;
  const l = readoutLayout(o.params, theme, counting ? easeOut(o.presence) : 1);
  const color = col(theme, o.params.role, "accent");
  const x0 = -l.width / 2;
  const y0 = -l.height / 2;
  const scale = l.valueSize / 1000;
  const valueY = y0 + l.pad + (l.label ? l.label.height + 4 : 0) + l.valueSize * 0.9;
  const valueColor = darken(color, theme.mode === "dark" ? -0.05 : 0.06);
  return (
    <g>
      {l.style === "card" && (
        <>
          <CardShadow x={x0} y={y0} w={l.width} h={l.height} r={18} theme={theme} />
          <rect
            x={fmt(x0)}
            y={fmt(y0)}
            width={fmt(l.width)}
            height={fmt(l.height)}
            rx={18}
            fill={theme.colors.surface}
            stroke={mixHex(color, theme.colors.surface, 0.65)}
            strokeWidth={2.5}
          />
          <rect x={fmt(x0 + 14)} y={fmt(y0)} width={fmt(l.width - 28)} height={5} rx={2.5} fill={color} />
        </>
      )}
      {l.style === "pill" && (
        <rect
          x={fmt(x0)}
          y={fmt(y0)}
          width={fmt(l.width)}
          height={fmt(l.height)}
          rx={fmt(l.height / 2)}
          fill={mixHex(color, theme.colors.surface, 0.84)}
        />
      )}
      {l.label && <GlyphText layout={l.label} color={theme.colors.muted} x={x0 + l.pad} y={y0 + l.pad} />}
      <g>
        {l.valueCells.map((c, i) => {
          if (c.ch === " ") return null;
          const g = glyph(c.ch, 700);
          const gx = x0 + l.pad + c.x + (c.w - g.a * scale) / 2;
          return (
            <path
              key={`${i}-${c.ch}`}
              d={g.d}
              fill={valueColor}
              transform={`translate(${fmt(gx)} ${fmt(valueY)}) scale(${fmt(scale, 5)})`}
            />
          );
        })}
      </g>
      {l.unit && (
        <GlyphText
          layout={l.unit}
          color={theme.colors.muted}
          x={x0 + l.pad + l.valueWidth + l.unitSize * 0.3}
          y={valueY - l.unit.lines[0]!.baseline}
        />
      )}
    </g>
  );
}

function niceTicks(min: number, max: number, count: number): number[] {
  if (count <= 0) return [];
  const span = max - min;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(Math.abs(raw) || 1));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

export function GraphR({ o, theme, t }: RenderProps) {
  const p = o.params;
  const l = graphLayout(p);
  const muted = theme.colors.muted;
  const axisColor = mixHex(theme.colors.text, theme.colors.surface, 0.2);
  const clipId = uid(o.id, "plot");
  const xs = niceTicks(l.x.min, l.x.max, l.x.ticks);
  const ys = niceTicks(l.y.min, l.y.max, l.y.ticks);
  const tickSize = theme.typography.sizes.small * 0.82;
  const x0 = l.toLocal(Math.max(l.x.min, Math.min(l.x.max, 0)), 0).x;
  const y0 = l.toLocal(0, Math.max(l.y.min, Math.min(l.y.max, 0))).y;
  const series = Array.isArray(p.series) ? (p.series as Parameters<typeof seriesPoints>[1][]) : [];
  const fmtTick = (v: number) => formatNumber(v, Math.abs(v) < 10 && v % 1 !== 0 ? "0.0" : "0");
  const reveal = o.draw;
  return (
    <g>
      <defs>
        <clipPath id={clipId}>
          <rect
            x={fmt(l.plot.x - 10)}
            y={fmt(l.plot.y - 10)}
            width={fmt((l.plot.w + 20) * reveal)}
            height={fmt(l.plot.h + 20)}
          />
        </clipPath>
      </defs>
      <rect
        x={fmt(l.plot.x)}
        y={fmt(l.plot.y)}
        width={fmt(l.plot.w)}
        height={fmt(l.plot.h)}
        rx={6}
        fill={withAlpha(theme.colors.surface, 0.7)}
      />
      {p.grid !== false && (
        <g stroke={theme.colors.grid} strokeWidth={1.5}>
          {xs.map((v) => {
            const x = l.toLocal(v, 0).x;
            return <line key={`gx${v}`} x1={fmt(x)} y1={fmt(l.plot.y)} x2={fmt(x)} y2={fmt(l.plot.y + l.plot.h)} />;
          })}
          {ys.map((v) => {
            const y = l.toLocal(0, v).y;
            return <line key={`gy${v}`} x1={fmt(l.plot.x)} y1={fmt(y)} x2={fmt(l.plot.x + l.plot.w)} y2={fmt(y)} />;
          })}
        </g>
      )}
      <g stroke={axisColor} strokeWidth={3} strokeLinecap="round" fill="none">
        <line x1={fmt(l.plot.x)} y1={fmt(y0)} x2={fmt(l.plot.x + l.plot.w + 14)} y2={fmt(y0)} />
        <line x1={fmt(x0)} y1={fmt(l.plot.y + l.plot.h)} x2={fmt(x0)} y2={fmt(l.plot.y - 14)} />
        <path
          d={`M${fmt(l.plot.x + l.plot.w + 4)} ${fmt(y0 - 7)}L${fmt(l.plot.x + l.plot.w + 16)} ${fmt(y0)}L${fmt(l.plot.x + l.plot.w + 4)} ${fmt(y0 + 7)}`}
        />
        <path
          d={`M${fmt(x0 - 7)} ${fmt(l.plot.y - 4)}L${fmt(x0)} ${fmt(l.plot.y - 16)}L${fmt(x0 + 7)} ${fmt(l.plot.y - 4)}`}
        />
      </g>
      {xs.map((v) => {
        const lt = layoutText(fmtTick(v), { size: tickSize, align: "center" });
        const x = l.toLocal(v, 0).x;
        return <GlyphText key={`tx${v}`} layout={lt} color={muted} x={x - lt.width / 2} y={l.plot.y + l.plot.h + 6} />;
      })}
      {ys.map((v) => {
        const lt = layoutText(fmtTick(v), { size: tickSize, align: "right" });
        const y = l.toLocal(0, v).y;
        return (
          <GlyphText key={`ty${v}`} layout={lt} color={muted} x={l.plot.x - lt.width - 10} y={y - lt.height / 2} />
        );
      })}
      {l.x.label &&
        (() => {
          const lt = layoutText(l.x.label, { size: theme.typography.sizes.small, weight: 600, align: "center" });
          return (
            <GlyphText
              layout={lt}
              color={theme.colors.text}
              x={l.plot.x + l.plot.w / 2 - lt.width / 2}
              y={l.plot.y + l.plot.h + 34}
            />
          );
        })()}
      {l.y.label &&
        (() => {
          const lt = layoutText(l.y.label, { size: theme.typography.sizes.small, weight: 600, align: "left" });
          return <GlyphText layout={lt} color={theme.colors.text} x={l.plot.x} y={l.plot.y - lt.height - 6} />;
        })()}
      <g clipPath={`url(#${clipId})`}>
        {series.map((sr) => {
          const pts = seriesPoints(p, sr, t);
          if (pts.length === 0) return null;
          const c = col(theme, sr.role, "accent");
          const d = pts.map((q, i) => `${i ? "L" : "M"}${fmt(q.x)} ${fmt(q.y)}`).join("");
          if (sr.style === "points")
            return (
              <g key={sr.id}>
                {pts.map((q, i) => (
                  <circle
                    key={`${sr.id}${i}`}
                    cx={fmt(q.x)}
                    cy={fmt(q.y)}
                    r={6}
                    fill={c}
                    stroke={theme.colors.surface}
                    strokeWidth={2}
                  />
                ))}
              </g>
            );
          if (sr.style === "bars") {
            const bw = (l.plot.w / Math.max(1, pts.length)) * 0.6;
            return (
              <g key={sr.id}>
                {pts.map((q, i) => (
                  <rect
                    key={`${sr.id}${i}`}
                    x={fmt(q.x - bw / 2)}
                    y={fmt(Math.min(q.y, y0))}
                    width={fmt(bw)}
                    height={fmt(Math.abs(y0 - q.y))}
                    rx={4}
                    fill={c}
                  />
                ))}
              </g>
            );
          }
          const first = pts[0]!;
          const last = pts[pts.length - 1]!;
          return (
            <g key={sr.id}>
              {sr.style === "area" && (
                <path d={`${d}L${fmt(last.x)} ${fmt(y0)}L${fmt(first.x)} ${fmt(y0)}Z`} fill={withAlpha(c, 0.22)} />
              )}
              <path d={d} fill="none" stroke={c} strokeWidth={5} strokeLinecap="round" strokeLinejoin="round" />
            </g>
          );
        })}
      </g>
    </g>
  );
}

export const AxesR = GraphR;

export function GridR({ o, theme }: RenderProps) {
  const w = n(o.params.width, 600);
  const h = n(o.params.height, 400);
  const sp = n(o.params.spacing, 40);
  const lines = [];
  for (let x = -w / 2; x <= w / 2 + 1e-6; x += sp)
    lines.push(<line key={`x${x}`} x1={fmt(x)} y1={-h / 2} x2={fmt(x)} y2={h / 2} />);
  for (let y = -h / 2; y <= h / 2 + 1e-6; y += sp)
    lines.push(<line key={`y${y}`} x1={-w / 2} y1={fmt(y)} x2={w / 2} y2={fmt(y)} />);
  return (
    <g stroke={theme.colors.grid} strokeWidth={1.5} opacity={0.9}>
      {lines}
    </g>
  );
}
