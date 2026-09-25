/**
 * Text drawn as glyph outlines from the engine's bundled font tables (ADR-004):
 * identical in the browser, the worker's measurements and headless export.
 */
import { glyph, type TextLayout } from "@animator/engine";
import { memo } from "react";
import { fmt } from "./util";

export interface GlyphTextProps {
  layout: TextLayout;
  color: string;
  /** Color for **emphasized** runs. */
  emphColor?: string;
  /** 0..1 write-on progress: glyphs appear in order with a gentle rise. */
  reveal?: number;
  /** Box origin: layout (0,0) is the top-left of the text box. */
  x?: number;
  y?: number;
  /** Per-word color override (karaoke captions): index -> color. */
  wordColor?: (word: number) => string | undefined;
}

function GlyphTextInner({ layout, color, emphColor, reveal = 1, x = 0, y = 0, wordColor }: GlyphTextProps) {
  const scale = layout.size / 1000;
  const total = layout.glyphCount;
  let index = 0;
  const out = [];
  for (let li = 0; li < layout.lines.length; li++) {
    const line = layout.lines[li];
    if (!line) continue;
    for (let gi = 0; gi < line.glyphs.length; gi++) {
      const g = line.glyphs[gi];
      if (!g || g.ch === " ") continue;
      const i = index++;
      // Each glyph fades in over ~3 glyph-widths of progress.
      const f = reveal >= 1 ? 1 : Math.max(0, Math.min(1, (reveal * (total + 2) - i) / 3));
      if (f <= 0) continue;
      const fill = wordColor?.(g.word) ?? (g.emph && emphColor ? emphColor : color);
      const gx = x + line.x + g.x;
      const gy = y + line.baseline + (1 - f) * layout.size * 0.22;
      out.push(
        <path
          key={`${li}-${gi}`}
          d={glyph(g.ch, g.weight).d}
          transform={`translate(${fmt(gx)} ${fmt(gy)}) scale(${fmt(scale, 5)})`}
          fill={fill}
          opacity={f < 1 ? fmt(f, 3) : undefined}
        />,
      );
    }
  }
  return <g>{out}</g>;
}

export const GlyphText = memo(GlyphTextInner);
