/** Renderers for physics components. Ambient motion (waves, bubbles, blinking) is a pure function of t. */
import {
  balanceLayout,
  containerLayout,
  flowTable,
  formatNumber,
  gaugeLayout,
  hashString,
  layoutText,
  lightness,
  manometerLayout,
  materialOf,
  objectRefOf,
  type Params,
  pendulumBob,
  pipeLayout,
  rand01,
  ropePath,
  springPoints,
  springScaleLayout,
} from "@animator/engine";
import type { ReactNode } from "react";
import { GlyphText } from "../GlyphText";
import type { RenderProps } from "../types";
import { darken, drawAttrs, fmt, lighten, matrixAttr, mixHex, n, s, uid, withAlpha } from "../util";
import { CenteredText, col, Pill } from "./common";

const TAU = Math.PI * 2;

// ------------------------------------------------------------------ container

export function ContainerR({ o, theme, t, seed }: RenderProps) {
  const p = o.params;
  const l = containerLayout(p);
  const W = l.width;
  const H = l.height;
  const w = l.wall;
  const R = Math.min(26, W * 0.08, H * 0.2);
  const Ri = Math.max(4, R - w);
  const glass = col(theme, p.glass, "glass");
  const fluid = col(theme, p.fluid, "fluid");
  const { x0, x1, floor } = l.inner;
  const top = -H / 2;
  const sy = l.surfaceY;
  const sd = (seed ^ hashString(o.id)) >>> 0;
  const ids = { clip: uid(o.id, "inner"), liquid: uid(o.id, "liq"), wall: uid(o.id, "wall") };

  const innerPath = `M${x0} ${top}L${x0} ${floor - Ri}Q${x0} ${floor} ${x0 + Ri} ${floor}L${x1 - Ri} ${floor}Q${x1} ${floor} ${x1} ${floor - Ri}L${x1} ${top}Z`;
  const wallPath =
    `M${-W / 2} ${top}L${-W / 2} ${H / 2 - R}Q${-W / 2} ${H / 2} ${-W / 2 + R} ${H / 2}L${W / 2 - R} ${H / 2}Q${W / 2} ${H / 2} ${W / 2} ${H / 2 - R}L${W / 2} ${top}` +
    `L${x1} ${top}L${x1} ${floor - Ri}Q${x1} ${floor} ${x1 - Ri} ${floor}L${x0 + Ri} ${floor}Q${x0} ${floor} ${x0} ${floor - Ri}L${x0} ${top}Z`;

  // Surface: gentle ambient wave plus a decaying ripple from the solver.
  const ripple = n(p.ripple, 0);
  const rx = n(p.rippleX, 0);
  const pts: string[] = [];
  const hasLiquid = l.level > 0.001;
  for (let x = x0 - 2; x <= x1 + 2; x += 8) {
    const d = Math.abs(x - rx);
    const y =
      sy +
      2.2 * Math.sin((TAU * x) / 150 + 1.7 * t) +
      (ripple ? ripple * Math.cos(d / 16 - 8 * t) * Math.exp(-d / 200) : 0);
    pts.push(`${fmt(x)} ${fmt(y)}`);
  }
  const wave = `M${pts.join("L")}`;
  const liquid = `${wave}L${x1 + 2} ${floor + 4}L${x0 - 2} ${floor + 4}Z`;

  const bubbles: ReactNode[] = [];
  const depth = floor - sy;
  if (p.bubbles !== false && hasLiquid && depth > 36) {
    for (let i = 0; i < 9; i++) {
      const period = 3.2 + rand01(sd, i, 1) * 3.5;
      const u = ((t + rand01(sd, i, 2) * period) % period) / period;
      const r = 2.5 + rand01(sd, i, 3) * 5;
      const bx = x0 + 18 + rand01(sd, i, 4) * (x1 - x0 - 36) + 6 * Math.sin(t * 2.1 + i);
      const by = floor - 10 - u * (depth - 20);
      bubbles.push(
        <circle
          key={i}
          cx={fmt(bx)}
          cy={fmt(by)}
          r={fmt(r)}
          fill={withAlpha("#ffffff", 0.2)}
          stroke={withAlpha("#ffffff", 0.7)}
          strokeWidth={1.5}
          opacity={fmt(Math.min(1, u * 6, (1 - u) * 6), 3)}
        />,
      );
    }
  }

  const bands: ReactNode[] = [];
  if (hasLiquid) {
    for (let i = 0; i < 2; i++) {
      const span = x1 - x0 + 240;
      const bx = x0 - 120 + ((t * 16 + i * span * 0.5 + rand01(sd, 9, i) * 80) % span);
      bands.push(
        <path
          key={`b${i}`}
          d={`M${fmt(bx)} ${fmt(sy)}L${fmt(bx + 38)} ${fmt(sy)}L${fmt(bx - 22)} ${fmt(floor)}L${fmt(bx - 70)} ${fmt(floor)}Z`}
          fill={withAlpha("#ffffff", 0.07)}
        />,
      );
    }
  }

  const ticks: ReactNode[] = [];
  if (p.ticks === true) {
    const innerH = floor - top;
    for (let i = 1; i <= 9; i++) {
      const y = floor - (i / 10) * innerH;
      const len = i === 5 ? 26 : 14;
      ticks.push(
        <line
          key={`t${i}`}
          x1={fmt(x1 - len)}
          y1={fmt(y)}
          x2={fmt(x1 - 2)}
          y2={fmt(y)}
          stroke={darken(glass, 0.28)}
          strokeWidth={2.5}
          strokeLinecap="round"
          opacity={0.75}
        />,
      );
    }
  }

  let spout: ReactNode = null;
  let stream: ReactNode = null;
  if (l.spout) {
    const sp = l.spout;
    const tube = `M${W / 2 - 2} ${sp.y}L${sp.x0 + 58} ${sp.y + 5}Q${sp.x1 - 6} ${sp.y + 10} ${sp.x1} ${sp.tipY}`;
    const full = l.level >= n(p.spoutLevel, 0.75) - 0.01;
    spout = (
      <g>
        <path
          d={tube}
          fill="none"
          stroke={withAlpha(lighten(glass, 0.1), 0.85)}
          strokeWidth={22}
          strokeLinecap="round"
        />
        <path d={tube} fill="none" stroke={darken(glass, 0.2)} strokeWidth={24} strokeLinecap="round" opacity={0.25} />
        {full && <path d={tube} fill="none" stroke={withAlpha(fluid, 0.85)} strokeWidth={11} strokeLinecap="round" />}
      </g>
    );
    const out = n(p.outflow, 0);
    if (out > 0.01) {
      const d = `M${sp.x1} ${sp.tipY + 4}Q${sp.x1 + 8} ${sp.tipY + 60} ${sp.x1 + 4} ${sp.tipY + 150}`;
      stream = (
        <g opacity={fmt(Math.min(1, out * 1.5), 3)}>
          <path
            d={d}
            fill="none"
            stroke={withAlpha(fluid, 0.85)}
            strokeWidth={fmt(4 + 8 * out)}
            strokeLinecap="round"
          />
          <path
            d={d}
            fill="none"
            stroke={withAlpha("#ffffff", 0.55)}
            strokeWidth={2.5}
            strokeDasharray="14 18"
            strokeDashoffset={fmt(-t * 140)}
            strokeLinecap="round"
          />
        </g>
      );
    }
  }

  return (
    <g>
      <defs>
        <clipPath id={ids.clip}>
          <path d={innerPath} />
        </clipPath>
        <linearGradient id={ids.liquid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={lighten(fluid, 0.1)} stopOpacity={0.82} />
          <stop offset="1" stopColor={darken(fluid, 0.1)} stopOpacity={0.95} />
        </linearGradient>
        <linearGradient id={ids.wall} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor={lighten(glass, 0.14)} stopOpacity={0.8} />
          <stop offset="0.5" stopColor={lighten(glass, 0.22)} stopOpacity={0.55} />
          <stop offset="1" stopColor={glass} stopOpacity={0.8} />
        </linearGradient>
      </defs>
      {l.shape === "cylinder" && (
        <rect
          x={fmt(-W / 2 - 22)}
          y={fmt(H / 2 - 2)}
          width={fmt(W + 44)}
          height={14}
          rx={7}
          fill={withAlpha(glass, 0.7)}
          stroke={darken(glass, 0.2)}
          strokeWidth={2}
        />
      )}
      <path d={innerPath} fill={withAlpha(glass, 0.12)} />
      <g clipPath={`url(#${ids.clip})`}>
        {hasLiquid && <path d={liquid} fill={`url(#${ids.liquid})`} />}
        {bands}
        {bubbles}
        {hasLiquid && (
          <path
            d={wave}
            fill="none"
            stroke={lighten(fluid, 0.3)}
            strokeWidth={3.5}
            opacity={0.9}
            strokeLinecap="round"
          />
        )}
      </g>
      {spout}
      {stream}
      <path
        d={wallPath}
        fill={`url(#${ids.wall})`}
        stroke={darken(glass, 0.22)}
        strokeWidth={2}
        strokeLinejoin="round"
      />
      <rect
        x={fmt(-W / 2 - 3)}
        y={fmt(top - 4)}
        width={fmt(w + 6)}
        height={8}
        rx={4}
        fill={lighten(glass, 0.1)}
        stroke={darken(glass, 0.22)}
        strokeWidth={1.5}
      />
      <rect
        x={fmt(W / 2 - w - 3)}
        y={fmt(top - 4)}
        width={fmt(w + 6)}
        height={8}
        rx={4}
        fill={lighten(glass, 0.1)}
        stroke={darken(glass, 0.22)}
        strokeWidth={1.5}
      />
      {l.shape === "beaker" && (
        <path
          d={`M${-W / 2} ${top + 14}Q${-W / 2 - 2} ${top} ${-W / 2 - 18} ${top - 8}`}
          fill="none"
          stroke={darken(glass, 0.15)}
          strokeWidth={w * 0.9}
          strokeLinecap="round"
        />
      )}
      <rect
        x={fmt(x0 + 10)}
        y={fmt(top + H * 0.1)}
        width={7}
        height={fmt(H * 0.62)}
        rx={3.5}
        fill="#ffffff"
        opacity={0.42}
      />
      <rect
        x={fmt(x0 + 22)}
        y={fmt(top + H * 0.12)}
        width={3}
        height={fmt(H * 0.3)}
        rx={1.5}
        fill="#ffffff"
        opacity={0.3}
      />
      {ticks}
      {typeof p.mark === "number" && (
        <line
          x1={fmt(x0 + 4)}
          y1={fmt(floor - p.mark * (floor - top))}
          x2={fmt(x1 - 4)}
          y2={fmt(floor - p.mark * (floor - top))}
          stroke={darken(glass, 0.35)}
          strokeWidth={2.5}
          strokeDasharray="10 8"
          strokeLinecap="round"
          opacity={0.9}
        />
      )}
    </g>
  );
}

// ------------------------------------------------------------------ bodies

function Face({
  p,
  size,
  t,
  sd,
  cx = 0,
  cy = 0,
}: {
  p: Params;
  size: number;
  t: number;
  sd: number;
  cx?: number;
  cy?: number;
}) {
  const face = s(p.face, "none");
  if (face === "none") return null;
  const er = size * 0.075;
  const ex = size * 0.17;
  const ey = cy - size * 0.06;
  const period = 3.8 + rand01(sd, 77) * 1.6;
  const blink = (t + rand01(sd, 78) * period) % period < 0.14;
  const ink = "#1c2541";
  const eye = (x: number, closed: boolean) =>
    closed ? (
      <path
        d={`M${fmt(x - er)} ${fmt(ey)}Q${fmt(x)} ${fmt(ey + er * 0.9)} ${fmt(x + er)} ${fmt(ey)}`}
        fill="none"
        stroke={ink}
        strokeWidth={size * 0.028}
        strokeLinecap="round"
      />
    ) : (
      <g>
        <ellipse cx={fmt(x)} cy={fmt(ey)} rx={fmt(er)} ry={fmt(er * 1.2)} fill={ink} />
        <circle cx={fmt(x + er * 0.35)} cy={fmt(ey - er * 0.45)} r={fmt(er * 0.35)} fill="#ffffff" />
      </g>
    );
  const my = cy + size * 0.12;
  const mw = size * 0.14;
  let mouth: ReactNode;
  switch (face) {
    case "surprised":
      mouth = <ellipse cx={cx} cy={fmt(my + 2)} rx={fmt(mw * 0.4)} ry={fmt(mw * 0.55)} fill={ink} />;
      break;
    case "worried":
      mouth = (
        <path
          d={`M${fmt(cx - mw)} ${fmt(my + 6)}Q${cx} ${fmt(my - 6)} ${fmt(cx + mw)} ${fmt(my + 6)}`}
          fill="none"
          stroke={ink}
          strokeWidth={size * 0.03}
          strokeLinecap="round"
        />
      );
      break;
    case "calm":
      mouth = (
        <path
          d={`M${fmt(cx - mw * 0.6)} ${fmt(my)}Q${cx} ${fmt(my + 5)} ${fmt(cx + mw * 0.6)} ${fmt(my)}`}
          fill="none"
          stroke={ink}
          strokeWidth={size * 0.028}
          strokeLinecap="round"
        />
      );
      break;
    default:
      mouth = (
        <path
          d={`M${fmt(cx - mw)} ${fmt(my - 2)}Q${cx} ${fmt(my + mw * 0.9)} ${fmt(cx + mw)} ${fmt(my - 2)}`}
          fill={withAlpha(ink, 0.9)}
          stroke={ink}
          strokeWidth={size * 0.02}
          strokeLinejoin="round"
        />
      );
  }
  return (
    <g>
      {eye(cx - ex, blink)}
      {eye(cx + ex, blink || face === "wink")}
      {(face === "happy" || face === "wink") && (
        <>
          <circle cx={fmt(cx - ex * 1.55)} cy={fmt(my - 4)} r={fmt(er * 1.1)} fill="#ff7aa2" opacity={0.35} />
          <circle cx={fmt(cx + ex * 1.55)} cy={fmt(my - 4)} r={fmt(er * 1.1)} fill="#ff7aa2" opacity={0.35} />
        </>
      )}
      {mouth}
    </g>
  );
}

function texture(material: string, w: number, h: number, base: string, sd: number, t: number): ReactNode[] {
  const out: ReactNode[] = [];
  const x0 = -w / 2;
  const y0 = -h / 2;
  switch (material) {
    case "wood": {
      for (let i = 0; i < 5; i++) {
        const y = y0 + ((i + 0.7) * h) / 5.4 + (rand01(sd, i, 1) - 0.5) * 8;
        const a = 3 + rand01(sd, i, 2) * 4;
        const ph = rand01(sd, i, 3) * TAU;
        let d = `M${fmt(x0 - 4)} ${fmt(y)}`;
        for (let x = x0; x <= -x0 + 4; x += w / 12) d += `L${fmt(x)} ${fmt(y + a * Math.sin(x / (w / 5) + ph))}`;
        out.push(
          <path
            key={`g${i}`}
            d={d}
            fill="none"
            stroke={darken(base, 0.16)}
            strokeWidth={2.2}
            opacity={0.5}
            strokeLinecap="round"
          />,
        );
      }
      const kx = x0 + w * (0.25 + rand01(sd, 9) * 0.5);
      const ky = y0 + h * (0.3 + rand01(sd, 10) * 0.4);
      out.push(
        <ellipse
          key="knot"
          cx={fmt(kx)}
          cy={fmt(ky)}
          rx={fmt(w * 0.07)}
          ry={fmt(h * 0.045)}
          fill="none"
          stroke={darken(base, 0.2)}
          strokeWidth={2}
          opacity={0.55}
        />,
      );
      break;
    }
    case "cork":
    case "foam":
    case "stone": {
      const count = material === "stone" ? 34 : 28;
      for (let i = 0; i < count; i++) {
        const r = 1.2 + rand01(sd, i, 5) * (material === "foam" ? 4 : 2.6);
        const c =
          material === "foam"
            ? withAlpha("#ffffff", 0.55)
            : material === "stone"
              ? rand01(sd, i, 6) > 0.5
                ? darken(base, 0.18)
                : lighten(base, 0.12)
              : darken(base, 0.22);
        out.push(
          <circle
            key={`s${i}`}
            cx={fmt(x0 + rand01(sd, i, 7) * w)}
            cy={fmt(y0 + rand01(sd, i, 8) * h)}
            r={fmt(r)}
            fill={c}
            opacity={0.7}
          />,
        );
      }
      if (material === "stone") {
        out.push(
          <path
            key="crack"
            d={`M${fmt(x0 + w * 0.62)} ${fmt(y0)}l${fmt(-w * 0.08)} ${fmt(h * 0.2)}l${fmt(w * 0.06)} ${fmt(h * 0.12)}l${fmt(-w * 0.1)} ${fmt(h * 0.18)}`}
            fill="none"
            stroke={darken(base, 0.25)}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity={0.6}
          />,
        );
      }
      break;
    }
    case "metal":
    case "aluminium":
    case "gold": {
      for (let i = 0; i < 9; i++) {
        const y = y0 + ((i + 0.5) * h) / 9;
        out.push(
          <line
            key={`br${i}`}
            x1={fmt(x0)}
            y1={fmt(y)}
            x2={fmt(-x0)}
            y2={fmt(y)}
            stroke="#ffffff"
            strokeWidth={1}
            opacity={0.1}
          />,
        );
      }
      out.push(
        <path
          key="spec"
          d={`M${fmt(x0 + w * 0.12)} ${fmt(-y0)}L${fmt(x0 + w * 0.42)} ${fmt(-y0)}L${fmt(x0 + w * 0.9)} ${fmt(y0)}L${fmt(x0 + w * 0.6)} ${fmt(y0)}Z`}
          fill="#ffffff"
          opacity={0.28}
        />,
      );
      if (material === "gold") {
        const tw = 0.5 + 0.5 * Math.sin(t * 3 + rand01(sd, 3) * TAU);
        const sx = x0 + w * 0.78;
        const sy2 = y0 + h * 0.24;
        out.push(
          <path
            key="twinkle"
            d={`M${fmt(sx)} ${fmt(sy2 - 9)}L${fmt(sx + 2.5)} ${fmt(sy2 - 2.5)}L${fmt(sx + 9)} ${fmt(sy2)}L${fmt(sx + 2.5)} ${fmt(sy2 + 2.5)}L${fmt(sx)} ${fmt(sy2 + 9)}L${fmt(sx - 2.5)} ${fmt(sy2 + 2.5)}L${fmt(sx - 9)} ${fmt(sy2)}L${fmt(sx - 2.5)} ${fmt(sy2 - 2.5)}Z`}
            fill="#ffffff"
            opacity={fmt(0.4 + 0.6 * tw, 3)}
          />,
        );
      }
      break;
    }
    case "ice": {
      out.push(
        <path
          key="f1"
          d={`M${fmt(x0 + w * 0.12)} ${fmt(y0 + h * 0.15)}L${fmt(x0 + w * 0.45)} ${fmt(y0 + h * 0.12)}L${fmt(x0 + w * 0.22)} ${fmt(y0 + h * 0.55)}Z`}
          fill="#ffffff"
          opacity={0.45}
        />,
      );
      out.push(
        <path
          key="f2"
          d={`M${fmt(x0 + w * 0.7)} ${fmt(y0 + h * 0.6)}L${fmt(x0 + w * 0.88)} ${fmt(y0 + h * 0.4)}L${fmt(x0 + w * 0.84)} ${fmt(y0 + h * 0.85)}Z`}
          fill="#ffffff"
          opacity={0.3}
        />,
      );
      break;
    }
    default: {
      out.push(
        <ellipse
          key="gloss"
          cx={fmt(x0 + w * 0.32)}
          cy={fmt(y0 + h * 0.26)}
          rx={fmt(w * 0.22)}
          ry={fmt(h * 0.1)}
          fill="#ffffff"
          opacity={0.32}
        />,
      );
    }
  }
  return out;
}

export function BlockR({ o, theme, t, seed }: RenderProps) {
  const p = o.params;
  const w = n(p.width, 120);
  const h = n(p.height, 100);
  const r = Math.min(n(p.cornerRadius, 10), w / 2, h / 2);
  const material = s(p.material, "wood");
  const mat = materialOf(material);
  const base = material === "custom" ? o.fill : mat.base;
  const sd = (seed ^ hashString(o.id)) >>> 0;
  const ids = { grad: uid(o.id, "grad"), clip: uid(o.id, "clip") };
  const label = s(p.label, "");
  const labelLayout = label
    ? layoutText(label, { size: Math.max(16, Math.min(40, h * 0.3, w * 0.32)), weight: 700, align: "center" })
    : null;
  const labelColor = lightness(base) > 0.72 ? theme.colors.text : "#ffffff";
  return (
    <g>
      <defs>
        <linearGradient id={ids.grad} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={lighten(base, 0.06)} />
          <stop offset="1" stopColor={darken(base, 0.07)} />
        </linearGradient>
        <clipPath id={ids.clip}>
          <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={r} />
        </clipPath>
      </defs>
      <rect
        x={-w / 2}
        y={-h / 2}
        width={w}
        height={h}
        rx={r}
        fill={`url(#${ids.grad})`}
        opacity={material === "ice" ? 0.86 : 1}
        fillOpacity={o.fillReveal}
      />
      <g clipPath={`url(#${ids.clip})`} opacity={o.fillReveal}>
        {texture(material, w, h, base, sd, t)}
        <rect x={-w / 2} y={-h / 2} width={w} height={fmt(Math.max(6, h * 0.14))} fill="#ffffff" opacity={0.2} />
        <rect
          x={-w / 2}
          y={fmt(h / 2 - Math.max(6, h * 0.12))}
          width={w}
          height={fmt(Math.max(6, h * 0.12))}
          fill="#000000"
          opacity={0.08}
        />
      </g>
      <rect
        x={-w / 2}
        y={-h / 2}
        width={w}
        height={h}
        rx={r}
        fill="none"
        stroke={darken(base, 0.2)}
        strokeWidth={3}
        {...drawAttrs(o.draw)}
      />
      {labelLayout && !s(p.face, "none").match(/happy|surprised|worried|calm|wink/) && (
        <CenteredText layout={labelLayout} cx={0} cy={0} color={labelColor} />
      )}
      <Face p={p} size={Math.min(w, h * 1.3)} t={t} sd={sd} />
    </g>
  );
}

export function BallR({ o, t, seed }: RenderProps) {
  const p = o.params;
  const r = n(p.radius, 48);
  const material = s(p.material, "plastic");
  const base = material === "custom" ? o.fill : materialOf(material).base;
  const id = uid(o.id, "grad");
  const sd = (seed ^ hashString(o.id)) >>> 0;
  return (
    <g>
      <defs>
        <radialGradient id={id} cx="0.36" cy="0.32" r="0.78">
          <stop offset="0" stopColor={lighten(base, 0.16)} />
          <stop offset="0.7" stopColor={base} />
          <stop offset="1" stopColor={darken(base, 0.12)} />
        </radialGradient>
      </defs>
      <circle
        r={r}
        fill={`url(#${id})`}
        fillOpacity={o.fillReveal}
        stroke={darken(base, 0.2)}
        strokeWidth={3}
        {...drawAttrs(o.draw)}
      />
      <ellipse
        cx={fmt(-r * 0.32)}
        cy={fmt(-r * 0.4)}
        rx={fmt(r * 0.3)}
        ry={fmt(r * 0.16)}
        fill="#ffffff"
        opacity={0.45}
        transform={`rotate(-30 ${fmt(-r * 0.32)} ${fmt(-r * 0.4)})`}
      />
      <Face p={p} size={r * 1.9} t={t} sd={sd} />
    </g>
  );
}

// ------------------------------------------------------------------ connectors

export function RopeR({ o, theme }: RenderProps) {
  const r = ropePath(o.params);
  const color = col(theme, o.params.role, "muted");
  const w = n(o.params.thickness, 4);
  const d = `M${fmt(r.a.x)} ${fmt(r.a.y)}Q${fmt(r.c.x)} ${fmt(r.c.y)} ${fmt(r.b.x)} ${fmt(r.b.y)}`;
  return (
    <g fill="none" strokeLinecap="round">
      <path d={d} stroke={darken(color, 0.05)} strokeWidth={w} {...drawAttrs(o.draw)} />
      {o.draw >= 1 && (
        <path
          d={d}
          stroke={lighten(color, 0.2)}
          strokeWidth={fmt(w * 0.45)}
          strokeDasharray={`${fmt(w * 1.4)} ${fmt(w * 1.2)}`}
          opacity={0.6}
        />
      )}
    </g>
  );
}

export function SpringR({ o, theme }: RenderProps) {
  const pts = springPoints(o.params);
  const color = col(theme, o.params.role, "muted");
  const w = n(o.params.thickness, 4);
  const d = pts.map((q, i) => `${i ? "L" : "M"}${fmt(q.x)} ${fmt(q.y)}`).join("");
  return (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} stroke={darken(color, 0.1)} strokeWidth={w + 1.5} {...drawAttrs(o.draw)} />
      {o.draw >= 1 && <path d={d} stroke={lighten(color, 0.25)} strokeWidth={fmt(w * 0.4)} opacity={0.7} />}
    </g>
  );
}

function Ceiling({ x, y, w, theme }: { x: number; y: number; w: number; theme: RenderProps["theme"] }) {
  const lines = [];
  for (let i = 0; i <= w / 14; i++)
    lines.push(<line key={i} x1={fmt(x + i * 14)} y1={fmt(y)} x2={fmt(x + i * 14 - 10)} y2={fmt(y - 10)} />);
  return (
    <g>
      <rect x={fmt(x)} y={fmt(y)} width={fmt(w)} height={8} rx={3} fill={theme.colors.muted} />
      <g stroke={theme.colors.muted} strokeWidth={2} opacity={0.6}>
        {lines}
      </g>
    </g>
  );
}

export function PulleyR({ o, theme }: RenderProps) {
  const r = n(o.params.radius, 42);
  const m = n(o.params.mount, 60);
  const a = n(o.params.angle, 0);
  const metal = theme.colors.muted;
  return (
    <g>
      <Ceiling x={-44} y={-r - m} w={88} theme={theme} />
      <rect x={-5} y={fmt(-r - m + 6)} width={10} height={fmt(m + r - 6)} rx={3} fill={darken(metal, 0.05)} />
      <circle r={r} fill={mixHex(metal, theme.colors.surface, 0.75)} stroke={darken(metal, 0.1)} strokeWidth={5} />
      <circle r={fmt(r - 9)} fill="none" stroke={metal} strokeWidth={2} opacity={0.6} />
      <g transform={`rotate(${fmt(a)})`} stroke={metal} strokeWidth={3} strokeLinecap="round">
        {[0, 45, 90, 135].map((k) => (
          <line
            key={k}
            x1={fmt(Math.cos((k * Math.PI) / 180) * (r - 10))}
            y1={fmt(Math.sin((k * Math.PI) / 180) * (r - 10))}
            x2={fmt(-Math.cos((k * Math.PI) / 180) * (r - 10))}
            y2={fmt(-Math.sin((k * Math.PI) / 180) * (r - 10))}
          />
        ))}
      </g>
      <circle r={8} fill={darken(metal, 0.1)} />
    </g>
  );
}

export function PendulumR({ o, theme }: RenderProps) {
  const p = o.params;
  const bob = pendulumBob(p);
  const L = n(p.length, 280);
  const br = n(p.bobRadius, 32);
  const base = materialOf(s(p.material, "metal")).base;
  const id = uid(o.id, "bob");
  const arcA = (40 * Math.PI) / 180;
  return (
    <g>
      {p.showArc !== false && (
        <path
          d={`M${fmt(-L * Math.sin(arcA))} ${fmt(L * Math.cos(arcA))}A${L} ${L} 0 0 0 ${fmt(L * Math.sin(arcA))} ${fmt(L * Math.cos(arcA))}`}
          fill="none"
          stroke={theme.colors.grid}
          strokeWidth={3}
          strokeDasharray="6 10"
        />
      )}
      <line x1={0} y1={0} x2={fmt(0)} y2={fmt(L)} stroke={theme.colors.grid} strokeWidth={2} strokeDasharray="4 8" />
      <Ceiling x={-60} y={-12} w={120} theme={theme} />
      <line
        x1={0}
        y1={0}
        x2={fmt(bob.x)}
        y2={fmt(bob.y)}
        stroke={darken(theme.colors.muted, 0.1)}
        strokeWidth={3}
        strokeLinecap="round"
      />
      <defs>
        <radialGradient id={id} cx="0.36" cy="0.32" r="0.78">
          <stop offset="0" stopColor={lighten(base, 0.16)} />
          <stop offset="1" stopColor={darken(base, 0.12)} />
        </radialGradient>
      </defs>
      <circle cx={fmt(bob.x)} cy={fmt(bob.y)} r={br} fill={`url(#${id})`} stroke={darken(base, 0.2)} strokeWidth={3} />
      <circle r={6} fill={theme.colors.text} />
    </g>
  );
}

export function BalanceR({ o, theme }: RenderProps) {
  const l = balanceLayout(o.params);
  const pw = n(o.params.panWidth, 110);
  const metal = theme.colors.muted;
  const pan = (c: { x: number; y: number }, end: { x: number; y: number }, key: string) => (
    <g key={key}>
      <line x1={fmt(end.x)} y1={fmt(end.y)} x2={fmt(c.x - pw / 2 + 6)} y2={fmt(c.y)} stroke={metal} strokeWidth={2} />
      <line x1={fmt(end.x)} y1={fmt(end.y)} x2={fmt(c.x + pw / 2 - 6)} y2={fmt(c.y)} stroke={metal} strokeWidth={2} />
      <path
        d={`M${fmt(c.x - pw / 2)} ${fmt(c.y)}Q${fmt(c.x)} ${fmt(c.y + 26)} ${fmt(c.x + pw / 2)} ${fmt(c.y)}Z`}
        fill={mixHex(metal, theme.colors.surface, 0.6)}
        stroke={darken(metal, 0.1)}
        strokeWidth={3}
      />
    </g>
  );
  return (
    <g>
      <path
        d={`M-70 ${fmt(l.post)}L70 ${fmt(l.post)}L40 ${fmt(l.post - 18)}L-40 ${fmt(l.post - 18)}Z`}
        fill={darken(metal, 0.05)}
      />
      <rect x={-7} y={0} width={14} height={fmt(l.post - 16)} rx={4} fill={metal} />
      <line
        x1={fmt(l.beamL.x)}
        y1={fmt(l.beamL.y)}
        x2={fmt(l.beamR.x)}
        y2={fmt(l.beamR.y)}
        stroke={darken(metal, 0.12)}
        strokeWidth={10}
        strokeLinecap="round"
      />
      {pan(l.left, l.beamL, "l")}
      {pan(l.right, l.beamR, "r")}
      <path d="M-14 12L0 -14L14 12Z" fill={theme.colors.text} />
    </g>
  );
}

export function SpringScaleR({ o, theme }: RenderProps) {
  const p = o.params;
  const l = springScaleLayout(p);
  const metal = theme.colors.muted;
  const body = mixHex(metal, theme.colors.surface, 0.55);
  const win = { x: -l.w * 0.3, w: l.w * 0.6 };
  const ticks: ReactNode[] = [];
  for (let k = 0; k <= 10; k++) {
    const y = l.scaleTop + (k / 10) * (l.scaleBottom - l.scaleTop);
    const major = k % 5 === 0;
    ticks.push(
      <line
        key={k}
        x1={fmt(win.x + win.w - (major ? 16 : 9))}
        y1={fmt(y)}
        x2={fmt(win.x + win.w - 2)}
        y2={fmt(y)}
        stroke={theme.colors.text}
        strokeWidth={major ? 2.4 : 1.5}
      />,
    );
  }
  const coil: string[] = [];
  const top = l.scaleTop - 8;
  const bottom = l.pointerY - 4;
  const turns = 9;
  for (let i = 0; i <= turns * 2; i++) {
    const y = top + ((bottom - top) * i) / (turns * 2);
    coil.push(`${fmt(win.x + win.w * 0.3 + (i % 2 ? 8 : -8))} ${fmt(y)}`);
  }
  const value = `${formatNumber(l.reading, s(p.format, "0.0"))} ${s(p.unit, "N")}`;
  const valueLayout = layoutText(value, { size: theme.typography.sizes.small, weight: 700, align: "center" });
  const pill = { w: valueLayout.width + 26, h: valueLayout.height + 10 };
  const hookY = l.h / 2 + l.hook;
  return (
    <g>
      <circle cx={0} cy={fmt(l.top - 12)} r={11} fill="none" stroke={darken(metal, 0.1)} strokeWidth={5} />
      <rect
        x={fmt(-l.w / 2)}
        y={fmt(l.top)}
        width={fmt(l.w)}
        height={fmt(l.h)}
        rx={fmt(l.w / 2.4)}
        fill={body}
        stroke={darken(metal, 0.12)}
        strokeWidth={3}
      />
      <rect
        x={fmt(win.x)}
        y={fmt(l.scaleTop - 12)}
        width={fmt(win.w)}
        height={fmt(l.scaleBottom - l.scaleTop + 24)}
        rx={8}
        fill={theme.colors.surface}
        stroke={darken(metal, 0.05)}
        strokeWidth={1.5}
      />
      {ticks}
      <path
        d={`M${coil.join("L")}`}
        fill="none"
        stroke={darken(metal, 0.15)}
        strokeWidth={2.5}
        strokeLinejoin="round"
      />
      <rect
        x={fmt(win.x + 2)}
        y={fmt(l.pointerY - 2.5)}
        width={fmt(win.w - 4)}
        height={5}
        rx={2.5}
        fill={theme.colors.negative}
      />
      <line
        x1={0}
        y1={fmt(l.h / 2 - 4)}
        x2={0}
        y2={fmt(hookY - 12)}
        stroke={darken(metal, 0.12)}
        strokeWidth={5}
        strokeLinecap="round"
      />
      <path
        d={`M0 ${fmt(hookY - 14)}L0 ${fmt(hookY)}Q0 ${fmt(hookY + 12)} -10 ${fmt(hookY + 8)}`}
        fill="none"
        stroke={darken(metal, 0.12)}
        strokeWidth={5}
        strokeLinecap="round"
      />
      {p.showValue !== false && (
        <g>
          <line
            x1={fmt(l.w / 2)}
            y1={fmt(l.pointerY)}
            x2={fmt(l.w / 2 + 16)}
            y2={fmt(l.pointerY)}
            stroke={theme.colors.negative}
            strokeWidth={2.5}
          />
          <Pill
            cx={l.w / 2 + 16 + pill.w / 2}
            cy={l.pointerY}
            w={pill.w}
            h={pill.h}
            fill={theme.colors.surface}
            stroke={theme.colors.negative}
            strokeWidth={2.5}
          />
          <CenteredText layout={valueLayout} cx={l.w / 2 + 16 + pill.w / 2} cy={l.pointerY} color={theme.colors.text} />
        </g>
      )}
    </g>
  );
}

// ------------------------------------------------------------------ pipe & flow

function pipeEdges(l: ReturnType<typeof pipeLayout>, step = 6) {
  const top: { x: number; y: number }[] = [];
  const bottom: { x: number; y: number }[] = [];
  for (let x = -l.length / 2; x <= l.length / 2 + 1e-6; x += step) {
    const c = l.centerAt(x);
    const w = l.widthAt(x);
    top.push({ x, y: c - w / 2 });
    bottom.push({ x, y: c + w / 2 });
  }
  return { top, bottom };
}

const poly = (pts: { x: number; y: number }[]) => pts.map((q, i) => `${i ? "L" : "M"}${fmt(q.x)} ${fmt(q.y)}`).join("");

export function PipeR({ o, theme }: RenderProps) {
  const l = pipeLayout(o.params);
  const fluid = col(theme, o.params.fluid, "fluid");
  const metal = theme.colors.muted;
  const { top, bottom } = pipeEdges(l);
  const fluidPath = `${poly(top)}${poly([...bottom].reverse()).replace(/^M/, "L")}Z`;
  const topWall = `${poly(top.map((q) => ({ x: q.x, y: q.y - l.wall })))}${poly([...top].reverse()).replace(/^M/, "L")}Z`;
  const bottomWall = `${poly(bottom)}${poly([...bottom].reverse().map((q) => ({ x: q.x, y: q.y + l.wall }))).replace(/^M/, "L")}Z`;
  const ids = { fluid: uid(o.id, "fluid"), metal: uid(o.id, "metal"), clip: uid(o.id, "reveal") };
  const flange = (x: number) => {
    const c = l.centerAt(x);
    const w = l.widthAt(x) + 2 * l.wall + 18;
    return (
      <rect
        key={`fl${x}`}
        x={fmt(x - 9)}
        y={fmt(c - w / 2)}
        width={18}
        height={fmt(w)}
        rx={5}
        fill={darken(metal, 0.02)}
        stroke={darken(metal, 0.18)}
        strokeWidth={2}
      />
    );
  };
  return (
    <g>
      <defs>
        <linearGradient id={ids.fluid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={lighten(fluid, 0.12)} stopOpacity={0.9} />
          <stop offset="1" stopColor={darken(fluid, 0.06)} stopOpacity={0.95} />
        </linearGradient>
        <linearGradient id={ids.metal} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={lighten(metal, 0.28)} />
          <stop offset="1" stopColor={darken(metal, 0.02)} />
        </linearGradient>
        <clipPath id={ids.clip}>
          <rect
            x={fmt(-l.length / 2 - 20)}
            y={fmt(l.bbox.y - 40)}
            width={fmt((l.length + 40) * o.draw)}
            height={fmt(l.bbox.h + 80)}
          />
        </clipPath>
      </defs>
      <g clipPath={o.draw < 1 ? `url(#${ids.clip})` : undefined}>
        <path d={fluidPath} fill={`url(#${ids.fluid})`} />
        <path
          d={topWall}
          fill={`url(#${ids.metal})`}
          stroke={darken(metal, 0.18)}
          strokeWidth={1.5}
          strokeLinejoin="round"
        />
        <path
          d={bottomWall}
          fill={`url(#${ids.metal})`}
          stroke={darken(metal, 0.18)}
          strokeWidth={1.5}
          strokeLinejoin="round"
        />
        <path
          d={poly(top.map((q) => ({ x: q.x, y: q.y - l.wall + 3 })))}
          fill="none"
          stroke="#ffffff"
          strokeWidth={2}
          opacity={0.45}
        />
        {flange(-l.length / 2)}
        {flange(l.length / 2)}
        {o.params.showSections !== false &&
          l.sections.map((sec) => (
            <g key={sec.id}>
              <line
                x1={fmt(sec.x)}
                y1={fmt(sec.y - sec.width / 2 + 4)}
                x2={fmt(sec.x)}
                y2={fmt(sec.y + sec.width / 2 - 4)}
                stroke="#ffffff"
                strokeWidth={2.5}
                strokeDasharray="6 6"
                opacity={0.85}
              />
              <ellipse
                cx={fmt(sec.x)}
                cy={fmt(sec.y)}
                rx={fmt(Math.max(5, sec.width * 0.12))}
                ry={fmt(sec.width / 2 - 2)}
                fill="none"
                stroke="#ffffff"
                strokeWidth={2}
                opacity={0.55}
              />
            </g>
          ))}
      </g>
    </g>
  );
}

export function FlowParticlesR({ o, theme, t, seed }: RenderProps) {
  const ref = objectRefOf(o.params, "pipe");
  if (!ref) return null;
  const l = pipeLayout(ref.params);
  const speed = n(o.params.speed, 90);
  if (speed <= 0) return null;
  const table = flowTable(l, speed);
  const count = Math.round(n(o.params.count, 44));
  const size = n(o.params.size, 6);
  const color = col(theme, o.params.role, "surface");
  const streaks = o.params.streaks !== false;
  const sd = (seed ^ hashString(o.id)) >>> 0;
  const clipId = uid(o.id, "clip");
  const { top, bottom } = pipeEdges(l, 10);
  const inside = `${poly(top)}${poly([...bottom].reverse()).replace(/^M/, "L")}Z`;
  const out: ReactNode[] = [];
  for (let i = 0; i < count; i++) {
    const phase = rand01(sd, i, 1);
    const lane = (rand01(sd, i, 2) * 2 - 1) * 0.8;
    const sTime = (t + phase * table.total) % table.total;
    let lo = 0;
    let hi = table.ts.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if ((table.ts[mid] as number) <= sTime) lo = mid;
      else hi = mid;
    }
    const t0 = table.ts[lo] as number;
    const t1 = table.ts[hi] as number;
    const x =
      (table.xs[lo] as number) +
      ((table.xs[hi] as number) - (table.xs[lo] as number)) * (t1 > t0 ? (sTime - t0) / (t1 - t0) : 0);
    const half = l.widthAt(x) / 2 - size;
    const y = l.centerAt(x) + lane * Math.max(0, half);
    const v = table.speedAt(x);
    const len = streaks ? Math.min(90, v * 0.1) : 0;
    out.push(
      len > 2 ? (
        <line
          key={i}
          x1={fmt(x - len)}
          y1={fmt(y)}
          x2={fmt(x)}
          y2={fmt(y)}
          stroke={color}
          strokeWidth={fmt(size)}
          strokeLinecap="round"
          opacity={0.8}
        />
      ) : (
        <circle key={i} cx={fmt(x)} cy={fmt(y)} r={fmt(size / 2)} fill={color} opacity={0.85} />
      ),
    );
  }
  return (
    <g transform={matrixAttr(ref.matrix)}>
      <defs>
        <clipPath id={clipId}>
          <path d={inside} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`}>{out}</g>
    </g>
  );
}

// ------------------------------------------------------------------ gauges

export function PressureGaugeR({ o, theme }: RenderProps) {
  const l = gaugeLayout(o.params, theme);
  const r = l.r;
  const metal = theme.colors.muted;
  const needle = col(theme, o.params.role, "pressure");
  const arc = (a0: number, a1: number, color: string) => {
    const R = r * 0.74;
    const p0 = { x: R * Math.cos(a0), y: R * Math.sin(a0) };
    const p1 = { x: R * Math.cos(a1), y: R * Math.sin(a1) };
    return (
      <path
        d={`M${fmt(p0.x)} ${fmt(p0.y)}A${fmt(R)} ${fmt(R)} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${fmt(p1.x)} ${fmt(p1.y)}`}
        fill="none"
        stroke={color}
        strokeWidth={fmt(r * 0.1)}
        strokeLinecap="butt"
      />
    );
  };
  const ang = (f: number) => (-225 + 270 * f) * (Math.PI / 180);
  const ticks: ReactNode[] = [];
  for (let k = 0; k <= 10; k++) {
    const a = ang(k / 10);
    const r0 = r * (k % 5 === 0 ? 0.58 : 0.63);
    ticks.push(
      <line
        key={k}
        x1={fmt(r0 * Math.cos(a))}
        y1={fmt(r0 * Math.sin(a))}
        x2={fmt(r * 0.68 * Math.cos(a))}
        y2={fmt(r * 0.68 * Math.sin(a))}
        stroke={theme.colors.text}
        strokeWidth={k % 5 === 0 ? 3 : 1.6}
        strokeLinecap="round"
      />,
    );
  }
  return (
    <g>
      <rect x={-8} y={fmt(r - 6)} width={16} height={fmt(l.stem + 6)} rx={3} fill={darken(metal, 0.05)} />
      <rect x={-14} y={fmt(r + l.stem - 12)} width={28} height={12} rx={3} fill={darken(metal, 0.15)} />
      <circle r={r} fill={mixHex(metal, theme.colors.surface, 0.4)} stroke={darken(metal, 0.2)} strokeWidth={3} />
      <circle r={fmt(r * 0.86)} fill={theme.colors.surface} />
      {arc(ang(0), ang(0.6), "#3bb273")}
      {arc(ang(0.6), ang(0.85), "#f2b233")}
      {arc(ang(0.85), ang(1), "#e2553c")}
      {ticks}
      <line
        x1={0}
        y1={0}
        x2={fmt(r * 0.7 * Math.cos(l.angle))}
        y2={fmt(r * 0.7 * Math.sin(l.angle))}
        stroke={needle}
        strokeWidth={5}
        strokeLinecap="round"
      />
      <circle r={fmt(r * 0.1)} fill={darken(needle, 0.1)} />
      <GlyphText layout={l.value} color={theme.colors.text} x={-l.value.width / 2} y={r * 0.42} />
      {l.label && (
        <GlyphText layout={l.label} color={theme.colors.text} x={-l.label.width / 2} y={-r - 8 - l.label.height} />
      )}
    </g>
  );
}

export function ManometerR({ o, theme }: RenderProps) {
  const l = manometerLayout(o.params);
  const fluid = col(theme, o.params.fluid, "pressure");
  const glass = theme.colors.glass;
  const tw = l.tube;
  const bottom = l.h / 2;
  const outer = `M${fmt(l.xL - tw / 2)} ${fmt(-l.h / 2)}L${fmt(l.xL - tw / 2)} ${fmt(bottom - tw)}A${fmt(l.w / 2)} ${fmt(tw * 1.2)} 0 0 0 ${fmt(l.xR + tw / 2)} ${fmt(bottom - tw)}L${fmt(l.xR + tw / 2)} ${fmt(-l.h / 2)}`;
  const inner = `M${fmt(l.xL)} ${fmt(l.leftLevel)}L${fmt(l.xL)} ${fmt(bottom - tw)}A${fmt(l.w / 2 - tw / 2)} ${fmt(tw * 0.8)} 0 0 0 ${fmt(l.xR)} ${fmt(bottom - tw)}L${fmt(l.xR)} ${fmt(l.rightLevel)}`;
  return (
    <g fill="none" strokeLinecap="round">
      <path d={outer} stroke={withAlpha(glass, 0.8)} strokeWidth={tw + 8} />
      <path d={outer} stroke={theme.colors.surface} strokeWidth={tw - 2} />
      <path d={inner} stroke={fluid} strokeWidth={tw - 8} />
      <line
        x1={fmt(l.xL - tw)}
        y1={fmt(l.leftLevel)}
        x2={fmt(l.xR + tw)}
        y2={fmt(l.leftLevel)}
        stroke={theme.colors.muted}
        strokeWidth={1.5}
        strokeDasharray="5 6"
      />
      <line
        x1={fmt(l.xL - tw)}
        y1={fmt(l.rightLevel)}
        x2={fmt(l.xR + tw)}
        y2={fmt(l.rightLevel)}
        stroke={theme.colors.muted}
        strokeWidth={1.5}
        strokeDasharray="5 6"
      />
    </g>
  );
}

// ------------------------------------------------------------------ ambient particles

export function ParticlesR({ o, theme, t, seed }: RenderProps) {
  const p = o.params;
  const kind = s(p.kind, "bubbles");
  const count = Math.round(n(p.count, 24));
  const w = n(p.width, 400);
  const h = n(p.height, 300);
  const speed = n(p.speed, 1);
  const size = n(p.size, 8);
  const color = col(theme, p.role, "surface");
  const sd = (seed ^ hashString(o.id)) >>> 0;
  const out: ReactNode[] = [];
  for (let i = 0; i < count; i++) {
    const r1 = rand01(sd, i, 1);
    const r2 = rand01(sd, i, 2);
    const r3 = rand01(sd, i, 3);
    const period = (3 + r2 * 4) / Math.max(0.05, speed);
    const u = ((t + r1 * period) % period) / period;
    const x = -w / 2 + r3 * w;
    const sz = size * (0.5 + rand01(sd, i, 4) * 0.8);
    switch (kind) {
      case "sparkles": {
        const a = Math.max(0, Math.sin(u * Math.PI * 2));
        const y = -h / 2 + rand01(sd, i, 5) * h;
        out.push(
          <path
            key={i}
            d={`M${fmt(x)} ${fmt(y - sz)}L${fmt(x + sz * 0.25)} ${fmt(y - sz * 0.25)}L${fmt(x + sz)} ${fmt(y)}L${fmt(x + sz * 0.25)} ${fmt(y + sz * 0.25)}L${fmt(x)} ${fmt(y + sz)}L${fmt(x - sz * 0.25)} ${fmt(y + sz * 0.25)}L${fmt(x - sz)} ${fmt(y)}L${fmt(x - sz * 0.25)} ${fmt(y - sz * 0.25)}Z`}
            fill={color}
            opacity={fmt(a, 3)}
          />,
        );
        break;
      }
      case "snow":
        out.push(
          <circle
            key={i}
            cx={fmt(x + 12 * Math.sin(t + i))}
            cy={fmt(-h / 2 + u * h)}
            r={fmt(sz / 2)}
            fill={color}
            opacity={0.85}
          />,
        );
        break;
      case "steam":
        out.push(
          <circle
            key={i}
            cx={fmt(x + 20 * Math.sin(u * 3 + i))}
            cy={fmt(h / 2 - u * h)}
            r={fmt(sz * (0.6 + u * 1.8))}
            fill={color}
            opacity={fmt(0.35 * (1 - u), 3)}
            filter="url(#fx-soft)"
          />,
        );
        break;
      case "dots":
        out.push(
          <circle
            key={i}
            cx={fmt(x + 30 * Math.sin(t * 0.5 * speed + i))}
            cy={fmt(-h / 2 + rand01(sd, i, 5) * h + 20 * Math.cos(t * 0.4 * speed + i))}
            r={fmt(sz / 2)}
            fill={color}
            opacity={0.6}
          />,
        );
        break;
      default:
        out.push(
          <circle
            key={i}
            cx={fmt(x + 6 * Math.sin(t * 2 + i))}
            cy={fmt(h / 2 - u * h)}
            r={fmt(sz / 2)}
            fill={withAlpha(color, 0.2)}
            stroke={color}
            strokeWidth={1.5}
            opacity={fmt(Math.min(1, u * 5, (1 - u) * 5), 3)}
          />,
        );
    }
  }
  return <g>{out}</g>;
}
