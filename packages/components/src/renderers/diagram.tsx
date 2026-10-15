/**
 * Diagrams (ADR-061): the engine's layout drawn as is. Lane tags, edges (curves from the layout,
 * arrowheads on the outlines), nodes (commits as filled circles, steps as tinted boxes), their labels
 * and notes. Parts appear in build order while it is drawn (`create`), or as `write` names them;
 * `highlight` with terms rings the parts it names. Changing into another diagram (`transform`
 * `into`), the nodes both share move from their old places.
 */
import {
  type Cubic,
  contrastRatio,
  type DiagramLayout,
  diagramLayoutOf,
  diagramScale,
  type LaidEdge,
  type LaidNode,
  outlinePoint,
  resolveColor,
  stagedReveal,
  type Theme,
  type Vec2,
} from "@animator/engine";
import type { ReactNode } from "react";
import type { RenderProps } from "../types";
import { darken, drawAttrs, fmt, lighten, mixHex, withAlpha } from "../util";
import { CenteredText, Pill } from "./common";

/** Prepared layout plus what a change of state adds (compile/compile.ts, `transform` `into`). */
type MorphingLayout = DiagramLayout & {
  /** This diagram replaces another: where its shared nodes and lane tags were (layout units). */
  morphFrom?: { nodes: Record<string, { cx: number; cy: number; w: number; h: number }>; lanes: Record<string, Vec2> };
  /** Another diagram replaces this one: the nodes and lanes it takes over (hidden here from then on). */
  handoff?: string[];
};

const P = (v: Vec2) => `${fmt(v.x)} ${fmt(v.y)}`;
const ease = (u: number) => {
  const t = Math.max(0, Math.min(1, u));
  return t * t * (3 - 2 * t);
};

function curveD(pieces: Cubic[]): string {
  if (!pieces.length) return "";
  const first = pieces[0] as Cubic;
  return `M${P(first.p0)}${pieces.map((c) => `C${P(c.c1)} ${P(c.c2)} ${P(c.p3)}`).join("")}`;
}

function headD(tip: Vec2, dir: Vec2, size: number): string {
  const nx = -dir.y;
  const ny = dir.x;
  const bx = tip.x - dir.x * size;
  const by = tip.y - dir.y * size;
  const w = size * 0.95;
  return `M${P(tip)}L${fmt(bx + (nx * w) / 2)} ${fmt(by + (ny * w) / 2)}L${fmt(bx - (nx * w) / 2)} ${fmt(by - (ny * w) / 2)}Z`;
}

function shapeD(n: { shape: LaidNode["shape"]; w: number; h: number }, cx: number, cy: number): string {
  const hw = n.w / 2;
  const hh = n.h / 2;
  switch (n.shape) {
    case "circle":
      return `M${fmt(cx + hw)} ${fmt(cy)}A${fmt(hw)} ${fmt(hh)} 0 1 1 ${fmt(cx - hw)} ${fmt(cy)}A${fmt(hw)} ${fmt(hh)} 0 1 1 ${fmt(cx + hw)} ${fmt(cy)}Z`;
    case "diamond":
      return `M${fmt(cx)} ${fmt(cy - hh)}L${fmt(cx + hw)} ${fmt(cy)}L${fmt(cx)} ${fmt(cy + hh)}L${fmt(cx - hw)} ${fmt(cy)}Z`;
    default: {
      const r = n.shape === "pill" ? hh : Math.min(14, hh * 0.45);
      const x = cx - hw;
      const y = cy - hh;
      return `M${fmt(x + r)} ${fmt(y)}H${fmt(x + n.w - r)}A${fmt(r)} ${fmt(r)} 0 0 1 ${fmt(x + n.w)} ${fmt(y + r)}V${fmt(y + n.h - r)}A${fmt(r)} ${fmt(r)} 0 0 1 ${fmt(x + n.w - r)} ${fmt(y + n.h)}H${fmt(x + r)}A${fmt(r)} ${fmt(r)} 0 0 1 ${fmt(x)} ${fmt(y + n.h - r)}V${fmt(y + r)}A${fmt(r)} ${fmt(r)} 0 0 1 ${fmt(x + r)} ${fmt(y)}Z`;
    }
  }
}

/** White text on a dark fill, the theme's text color on a light one. */
const labelOn = (fill: string, theme: Theme): string =>
  contrastRatio("#ffffff", fill) >= 3 ? "#ffffff" : theme.colors.text;

const paint = (theme: Theme, ref: string | null, fallback: string): string =>
  ref ? resolveColor(theme, ref, fallback as never) : fallback;

/**
 * An edge whose nodes are on their way (a change of state): its final curve with each control point
 * moved by a blend of the two nodes' offsets, itself blended from `line` (the straight segment between
 * the nodes as they are now, `s` = 0) to that curve (`s` = 1), so a long move never kinks it.
 */
function warp(pieces: Cubic[], dA: Vec2, dB: Vec2, s: number, line: [Vec2, Vec2]): Cubic[] {
  const n = pieces.length;
  const at = (p: Vec2, u: number): Vec2 => ({ x: p.x + dA.x * (1 - u) + dB.x * u, y: p.y + dA.y * (1 - u) + dB.y * u });
  const [a, b] = line;
  const along = (u: number): Vec2 => ({ x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u });
  const mix = (p: Vec2, q: Vec2): Vec2 => ({ x: q.x + (p.x - q.x) * s, y: q.y + (p.y - q.y) * s });
  return pieces.map((c, i) => ({
    p0: mix(at(c.p0, i / n), along(i / n)),
    c1: mix(at(c.c1, (i + 1 / 3) / n), along((i + 1 / 3) / n)),
    c2: mix(at(c.c2, (i + 2 / 3) / n), along((i + 2 / 3) / n)),
    p3: mix(at(c.p3, (i + 1) / n), along((i + 1) / n)),
  }));
}

const unit = (v: Vec2): Vec2 => {
  const len = Math.hypot(v.x, v.y) || 1;
  return { x: v.x / len, y: v.y / len };
};

export function DiagramR({ o, theme, prep }: RenderProps) {
  const l = diagramLayoutOf(o.params, prep) as MorphingLayout;
  if (!l.nodes.length) return null;
  const k = diagramScale(o.params, l);
  const terms = o.terms ?? {};
  const staged = stagedReveal(l, Math.min(o.draw, o.reveal));
  // Marks on parts: several may be on at once; on one part, the newest shows.
  const marks = [...(o.emph?.terms ? [o.emph] : []), ...(o.marks ?? [])];
  const markOf = (id: string | null) => (id ? marks.findLast((m) => m.terms?.includes(id)) : undefined);
  const hot = (id: string | null) => markOf(id)?.amount ?? 0;
  // A change of state: shared nodes travel from their old places, new ones fade in.
  const morphU = typeof o.params.__morph === "number" ? o.params.__morph : 1;
  const morph = l.morphFrom && morphU < 1 ? l.morphFrom : null;
  const s = ease(morphU);
  const handedOff = typeof o.params.__handoff === "number" && o.params.__handoff >= 1 ? new Set(l.handoff) : null;

  const shown = (n: LaidNode): number => {
    if (handedOff?.has(n.id)) return 0;
    let f = staged.node(n.id) * (terms[n.id] ?? 1);
    if (morph && !morph.nodes[n.id]) f *= s;
    return f;
  };
  const place = (n: LaidNode): { cx: number; cy: number; w: number; h: number } => {
    const from = morph?.nodes[n.id];
    if (!from) return { cx: n.cx, cy: n.cy, w: n.w, h: n.h };
    return {
      cx: from.cx + (n.cx - from.cx) * s,
      cy: from.cy + (n.cy - from.cy) * s,
      w: from.w + (n.w - from.w) * s,
      h: from.h + (n.h - from.h) * s,
    };
  };
  const byId = new Map(l.nodes.map((n) => [n.id, n]));
  const nodeShown = new Map(l.nodes.map((n) => [n.id, shown(n)]));
  const at = new Map(l.nodes.map((n) => [n.id, place(n)]));
  const edgeColor = o.stroke;
  const width = Math.max(1, o.strokeWidth * 0.75);

  const lanes: ReactNode[] = l.lanes.map((t) => {
    if (handedOff?.has(t.id)) return null;
    const f = staged.lane * (terms[t.id] ?? 1) * (morph && !morph.lanes[t.id] ? s : 1);
    if (f <= 0.001) return null;
    const color = paint(theme, t.color, theme.colors.accent);
    const from = morph?.lanes[t.id];
    const cx = from ? from.x + (t.tag.cx - from.x) * s : t.tag.cx;
    const cy = from ? from.y + (t.tag.cy - from.y) * s : t.tag.cy;
    const h = hot(t.id);
    return (
      <g key={`lane-${t.id}`} opacity={f < 1 ? fmt(f, 3) : undefined}>
        {h > 0 && (
          <Pill
            cx={cx}
            cy={cy}
            w={t.tag.w + 16}
            h={t.tag.h + 16}
            fill="none"
            stroke={markOf(t.id)?.color}
            strokeWidth={5}
            opacity={h}
          />
        )}
        <Pill cx={cx} cy={cy} w={t.tag.w} h={t.tag.h} fill={color} />
        <CenteredText layout={t.tag.text} cx={cx} cy={cy} color={labelOn(color, theme)} />
      </g>
    );
  });

  const edgeOf = (e: LaidEdge, i: number): ReactNode => {
    if (handedOff && (handedOff.has(e.from) || handedOff.has(e.to))) return null;
    const a = byId.get(e.from);
    const b = byId.get(e.to);
    if (!a || !b) return null;
    let f = Math.min(nodeShown.get(e.from) ?? 1, nodeShown.get(e.to) ?? 1);
    if (e.id && terms[e.id] !== undefined) f = Math.min(f, terms[e.id] ?? 1);
    if (f <= 0.001 || !e.shaft.length) return null;
    const pa = at.get(e.from) as { cx: number; cy: number; w: number; h: number };
    const pb = at.get(e.to) as { cx: number; cy: number; w: number; h: number };
    const dA = { x: pa.cx - a.cx, y: pa.cy - a.cy };
    const dB = { x: pb.cx - b.cx, y: pb.cy - b.cy };
    const moving = dA.x !== 0 || dA.y !== 0 || dB.x !== 0 || dB.y !== 0;
    let shaft = e.shaft;
    let heads = e.heads;
    if (moving) {
      // The straight segment between the nodes where they are now, outline to outline.
      const dir = unit({ x: pb.cx - pa.cx, y: pb.cy - pa.cy });
      const oa = outlinePoint({ shape: a.shape, w: pa.w, h: pa.h }, dir.x, dir.y);
      const ob = outlinePoint({ shape: b.shape, w: pb.w, h: pb.h }, -dir.x, -dir.y);
      const startPull = 4 + (e.head === "start" || e.head === "both" ? l.headSize * 0.6 : 0);
      const endPull = 4 + (e.head === "end" || e.head === "both" ? l.headSize * 0.6 : 0);
      const from = { x: pa.cx + oa.x + dir.x * startPull, y: pa.cy + oa.y + dir.y * startPull };
      const to = { x: pb.cx + ob.x - dir.x * endPull, y: pb.cy + ob.y - dir.y * endPull };
      shaft = warp(e.shaft, dA, dB, s, [from, to]);
      heads = e.heads.map((hd, j) => {
        const atEnd = j === 0 && (e.head === "end" || e.head === "both");
        const shift = atEnd ? dB : dA;
        const tip0 = atEnd
          ? { x: pb.cx + ob.x - dir.x * 4, y: pb.cy + ob.y - dir.y * 4 }
          : { x: pa.cx + oa.x + dir.x * 4, y: pa.cy + oa.y + dir.y * 4 };
        const dir0 = atEnd ? dir : { x: -dir.x, y: -dir.y };
        const tip1 = { x: hd.tip.x + shift.x, y: hd.tip.y + shift.y };
        return {
          tip: { x: tip0.x + (tip1.x - tip0.x) * s, y: tip0.y + (tip1.y - tip0.y) * s },
          dir: unit({ x: dir0.x + (hd.dir.x - dir0.x) * s, y: dir0.y + (hd.dir.y - dir0.y) * s }),
        };
      });
    }
    const color = e.color ? paint(theme, e.color, edgeColor) : edgeColor;
    const h = hot(e.id);
    const ghost = a.ghost || b.ghost;
    const headsOn = f >= 0.85 ? Math.min(1, (f - 0.85) / 0.15) : 0;
    const d = curveD(shaft);
    return (
      <g key={`edge-${i}`} opacity={ghost ? 0.5 : undefined}>
        {h > 0 && (
          <path
            d={d}
            fill="none"
            stroke={markOf(e.id)?.color}
            strokeWidth={width + 9}
            strokeLinecap="round"
            opacity={fmt(h * 0.8, 3)}
          />
        )}
        <path
          d={d}
          fill="none"
          stroke={color}
          strokeWidth={width}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={e.dashed && f >= 1 ? "12 9" : undefined}
          {...drawAttrs(f)}
        />
        {headsOn > 0 &&
          heads.map((hd, j) => {
            return (
              <path
                key={j}
                d={headD(hd.tip, hd.dir, l.headSize)}
                fill={color}
                stroke={color}
                strokeWidth={1.5}
                strokeLinejoin="round"
                opacity={headsOn < 1 ? fmt(headsOn, 3) : undefined}
              />
            );
          })}
      </g>
    );
  };

  const nodeOf = (n: LaidNode): ReactNode => {
    const f = nodeShown.get(n.id) ?? 1;
    if (f <= 0.001) return null;
    const p = at.get(n.id) as { cx: number; cy: number; w: number; h: number };
    const shape = { shape: n.shape, w: p.w, h: p.h };
    const base = paint(theme, n.color, o.fill);
    const filled = n.shape === "circle";
    const fill = n.ghost
      ? withAlpha(theme.colors.surface, 0.6)
      : filled
        ? base
        : mixHex(base, theme.colors.surface, theme.mode === "dark" ? 0.72 : 0.86);
    const stroke = n.ghost ? theme.colors.muted : filled ? darken(base, 0.12) : base;
    const text = n.ghost ? theme.colors.muted : filled ? labelOn(base, theme) : theme.colors.text;
    const h = hot(n.id);
    const pop = f < 1 ? 0.82 + 0.18 * f : 1;
    const grow = h > 0 ? 1 + 0.08 * h : 1;
    const scale = pop * grow;
    const tf =
      scale !== 1
        ? `translate(${fmt(p.cx)} ${fmt(p.cy)}) scale(${fmt(scale, 4)}) translate(${fmt(-p.cx)} ${fmt(-p.cy)})`
        : undefined;
    const gradId = `a-${o.id}-dn-${n.id}`;
    const useGrad = theme.effects.gradients && filled && !n.ghost;
    return (
      <g key={`node-${n.id}`} opacity={f < 1 ? fmt(f, 3) : n.ghost ? 0.75 : undefined} transform={tf}>
        {useGrad && (
          <defs>
            <radialGradient id={gradId} cx="0.38" cy="0.32" r="0.75">
              <stop offset="0" stopColor={lighten(base, 0.1)} />
              <stop offset="1" stopColor={darken(base, 0.05)} />
            </radialGradient>
          </defs>
        )}
        {h > 0 && (
          <path
            d={shapeD({ shape: n.shape, w: p.w + 18, h: p.h + 18 }, p.cx, p.cy)}
            fill={withAlpha(markOf(n.id)?.color ?? theme.colors.highlight, 0.28 * h)}
            stroke={markOf(n.id)?.color}
            strokeWidth={4}
            opacity={fmt(h, 3)}
          />
        )}
        <path
          d={shapeD(shape, p.cx, p.cy)}
          fill={useGrad ? `url(#${gradId})` : fill}
          stroke={stroke}
          strokeWidth={filled ? 2.5 : 3}
          strokeDasharray={n.ghost ? "8 6" : undefined}
          strokeLinejoin="round"
        />
        {n.label && <CenteredText layout={n.text} cx={p.cx} cy={p.cy} color={text} />}
        {n.note && (
          <CenteredText
            layout={n.note.text}
            cx={n.note.cx + (p.cx - n.cx)}
            cy={n.note.cy + (p.cy - n.cy)}
            color={theme.colors.muted}
          />
        )}
      </g>
    );
  };

  const edgeLabels: ReactNode[] = l.edges.map((e, i) => {
    if (!e.label) return null;
    if (handedOff && (handedOff.has(e.from) || handedOff.has(e.to))) return null;
    let f = Math.min(nodeShown.get(e.from) ?? 1, nodeShown.get(e.to) ?? 1);
    if (e.id && terms[e.id] !== undefined) f = Math.min(f, terms[e.id] ?? 1);
    if (f < 0.6) return null;
    const pa = at.get(e.from) as { cx: number; cy: number };
    const pb = at.get(e.to) as { cx: number; cy: number };
    const a = byId.get(e.from) as LaidNode;
    const b = byId.get(e.to) as LaidNode;
    const dx = (pa.cx - a.cx + pb.cx - b.cx) / 2;
    const dy = (pa.cy - a.cy + pb.cy - b.cy) / 2;
    const cx = e.label.cx + dx;
    const cy = e.label.cy + dy;
    return (
      <g key={`elabel-${i}`} opacity={f < 1 ? fmt((f - 0.6) / 0.4, 3) : undefined}>
        <Pill
          cx={cx}
          cy={cy}
          w={e.label.w}
          h={e.label.h}
          fill={theme.colors.surface}
          stroke={mixHex(edgeColor, theme.colors.surface, 0.45)}
          strokeWidth={2}
        />
        <CenteredText layout={e.label.text} cx={cx} cy={cy} color={theme.colors.text} />
      </g>
    );
  });

  return (
    <g transform={k !== 1 ? `scale(${fmt(k, 5)})` : undefined}>
      {lanes}
      {l.edges.map(edgeOf)}
      {[...l.nodes].sort((x, y) => Number(y.ghost) - Number(x.ghost)).map(nodeOf)}
      {edgeLabels}
    </g>
  );
}
