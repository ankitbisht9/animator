/** Saved drawings (ADR-055): the art's shapes, scaled to the object's width. */
import { type FigureArtT, figureSize, isHexColor, resolveColor, type Theme } from "@animator/engine";
import type { ReactNode } from "react";
import type { RenderProps } from "../types";
import { fmt } from "../util";

const paint = (theme: Theme, ref: string | undefined): string =>
  !ref || ref === "none" ? "none" : isHexColor(ref) ? ref : resolveColor(theme, ref);

// Renderers are plain functions (no hooks): the shapes are built once per art and theme.
const cache = new WeakMap<FigureArtT, Map<string, ReactNode>>();

function shapesOf(art: FigureArtT, theme: Theme): ReactNode {
  let byTheme = cache.get(art);
  if (!byTheme) {
    byTheme = new Map();
    cache.set(art, byTheme);
  }
  const hit = byTheme.get(theme.id);
  if (hit !== undefined) return hit;
  const out = art.shapes.map((s, i) => {
    const stroke = paint(theme, s.stroke);
    return (
      <path
        key={i}
        d={s.d}
        fill={paint(theme, s.fill)}
        fillRule={s.fillRule}
        stroke={stroke}
        strokeWidth={stroke === "none" ? undefined : (s.strokeWidth ?? 2)}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={s.opacity !== undefined && s.opacity < 1 ? s.opacity : undefined}
      />
    );
  });
  byTheme.set(theme.id, out);
  return out;
}

/** The focused part: drawn again over the dimmed drawing, ringed in the theme's highlight color. */
function focusOf(art: FigureArtT, theme: Theme, part: string, k: number): ReactNode {
  const mine = art.shapes.filter((s) => s.part === part);
  if (!mine.length) return null;
  const ring = theme.colors.highlight;
  return (
    <g>
      {mine.map((s, i) => (
        <path
          key={`r${i}`}
          d={s.d}
          fill="none"
          stroke={ring}
          strokeWidth={fmt(10 / k, 3)}
          strokeLinejoin="round"
          opacity={0.85}
        />
      ))}
      {mine.map((s, i) => {
        const stroke = paint(theme, s.stroke);
        return (
          <path
            key={`p${i}`}
            d={s.d}
            fill={paint(theme, s.fill)}
            fillRule={s.fillRule}
            stroke={stroke}
            strokeWidth={stroke === "none" ? undefined : (s.strokeWidth ?? 2)}
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity={s.opacity !== undefined && s.opacity < 1 ? s.opacity : undefined}
          />
        );
      })}
    </g>
  );
}

export function FigureR({ o, theme }: RenderProps) {
  const art = o.params.art as FigureArtT | undefined;
  if (!art || !Array.isArray(art.shapes) || !(art.w > 0) || !(art.h > 0)) return null;
  const k = figureSize(o.params).w / art.w;
  const focus = typeof o.params.focus === "string" ? o.params.focus : "";
  const shown = focus ? focusOf(art, theme, focus, k) : null;
  return (
    <g transform={`scale(${fmt(k, 5)}) translate(${fmt(-art.w / 2)} ${fmt(-art.h / 2)})`}>
      {shown ? <g opacity={0.55}>{shapesOf(art, theme)}</g> : shapesOf(art, theme)}
      {shown}
    </g>
  );
}
