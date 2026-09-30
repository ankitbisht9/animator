/**
 * The caption bar: the narration beat as on-screen text. Words light up as they are
 * read, which helps struggling readers follow along. With a voice (TTS), each word
 * lights up when it is said (`words`: [start, end] per word, seconds into the beat's
 * audio); without one, timing is proportional to word length, finishing at ~88% of
 * the beat.
 */
import { CAPTION_BAR, layoutText, type Theme } from "@animator/engine";
import { GlyphText } from "./GlyphText";
import { fmt, mixHex, withAlpha } from "./util";

/** After the last word is said, its highlight fades out this many seconds later. */
const HOLD_S = 0.3;

export function CaptionBar({
  text,
  progress,
  theme,
  words,
  elapsed,
}: {
  text: string;
  progress: number;
  theme: Theme;
  words?: [number, number][];
  elapsed?: number;
}) {
  const pad = 30;
  const size = theme.typography.sizes.caption;
  const layout = layoutText(text, { size, maxWidth: CAPTION_BAR.w - 2 * pad, align: "center", lineHeight: 1.32 });
  const h = Math.max(CAPTION_BAR.h - 30, layout.height + 34);
  const y = CAPTION_BAR.y + CAPTION_BAR.h - h;
  const w = Math.min(CAPTION_BAR.w, Math.max(layout.width + 2 * pad + 20, 420));
  const x = CAPTION_BAR.x + (CAPTION_BAR.w - w) / 2;

  // Word timing weighted by length.
  const lengths: number[] = [];
  for (const line of layout.lines)
    for (const g of line.glyphs) if (g.word >= 0) lengths[g.word] = (lengths[g.word] ?? 0) + 1;
  const total = lengths.reduce((a, b) => a + (b ?? 0), 0) || 1;
  const upcoming = mixHex(theme.colors.text, theme.colors.surface, theme.mode === "dark" ? 0.45 : 0.55);
  // Word i is still to come when `ahead(i)`; `current` is the word being read (-1: none).
  let ahead: (i: number) => boolean;
  let current: number;
  if (words && elapsed !== undefined && words.length === lengths.length && words.length > 0) {
    ahead = (i) => (words[i]?.[0] ?? 0) > elapsed;
    current = -1;
    for (let i = 0; i < words.length; i++) if ((words[i]?.[0] ?? 0) <= elapsed) current = i;
    const last = words[words.length - 1] as [number, number];
    if (current === words.length - 1 && elapsed > last[1] + HOLD_S) current = -1;
  } else {
    const spoken = Math.min(1, progress / 0.88) * total;
    const starts: number[] = [];
    let acc = 0;
    for (let i = 0; i < lengths.length; i++) {
      starts[i] = acc;
      acc += lengths[i] ?? 0;
    }
    ahead = (i) => (starts[i] ?? 0) >= spoken;
    current = lengths.findIndex((len, i) => spoken >= (starts[i] ?? 0) && spoken < (starts[i] ?? 0) + (len ?? 0));
  }

  // Highlight pill behind the word being read.
  let marker = null;
  if (current >= 0 && progress < 0.999) {
    let x0 = Number.POSITIVE_INFINITY;
    let x1 = Number.NEGATIVE_INFINITY;
    let baseline = 0;
    let lineX = 0;
    for (const line of layout.lines) {
      for (const g of line.glyphs) {
        if (g.word !== current) continue;
        x0 = Math.min(x0, g.x);
        x1 = Math.max(x1, g.x + g.w);
        baseline = line.baseline;
        lineX = line.x;
      }
    }
    if (Number.isFinite(x0)) {
      const tx = x + (w - layout.width) / 2;
      const ty = y + (h - layout.height) / 2;
      marker = (
        <rect
          x={fmt(tx + lineX + x0 - 6)}
          y={fmt(ty + baseline - size * 0.95)}
          width={fmt(x1 - x0 + 12)}
          height={fmt(size * 1.3)}
          rx={10}
          fill={withAlpha(theme.colors.highlight, theme.mode === "dark" ? 0.28 : 0.42)}
        />
      );
    }
  }

  return (
    <g>
      <rect
        x={fmt(x)}
        y={fmt(y + 6)}
        width={fmt(w)}
        height={fmt(h)}
        rx={26}
        fill={withAlpha(theme.colors.shadow, 0.08)}
      />
      <rect
        x={fmt(x)}
        y={fmt(y)}
        width={fmt(w)}
        height={fmt(h)}
        rx={26}
        fill={withAlpha(theme.colors.surface, 0.94)}
        stroke={withAlpha(theme.colors.grid, 0.9)}
        strokeWidth={1.5}
      />
      {marker}
      <GlyphText
        layout={layout}
        color={theme.colors.text}
        emphColor={theme.colors.accent}
        x={x + (w - layout.width) / 2}
        y={y + (h - layout.height) / 2}
        wordColor={(word) => (ahead(word) ? upcoming : undefined)}
      />
    </g>
  );
}
