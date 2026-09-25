/** Scene backgrounds: calm, low-contrast, a little depth. Deterministic (no randomness). */
import { CANVAS, type Theme } from "@animator/engine";
import { lighten, mixHex, withAlpha } from "./util";

export function Background({ style, color, theme }: { style: string; color: string; theme: Theme }) {
  const W = CANVAS.width;
  const H = CANVAS.height;
  const dark = theme.mode === "dark";
  const top = lighten(color, dark ? 0.03 : 0.015);
  const bottom = mixHex(color, theme.colors.accent, dark ? 0.1 : 0.06);
  const grid = withAlpha(theme.colors.grid, dark ? 0.55 : 0.7);
  return (
    <g>
      <defs>
        <linearGradient id="bg-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={top} />
          <stop offset="1" stopColor={bottom} />
        </linearGradient>
        <radialGradient id="bg-blob-a" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor={withAlpha(theme.colors.accent, dark ? 0.16 : 0.08)} />
          <stop offset="1" stopColor={withAlpha(theme.colors.accent, 0)} />
        </radialGradient>
        <radialGradient id="bg-blob-b" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor={withAlpha(theme.colors.fluid, dark ? 0.14 : 0.08)} />
          <stop offset="1" stopColor={withAlpha(theme.colors.fluid, 0)} />
        </radialGradient>
        <pattern id="bg-grid" width="40" height="40" patternUnits="userSpaceOnUse">
          <path d="M40 0H0V40" fill="none" stroke={grid} strokeWidth="1" />
        </pattern>
        <pattern id="bg-dots" width="32" height="32" patternUnits="userSpaceOnUse">
          <circle cx="16" cy="16" r="1.6" fill={grid} />
        </pattern>
      </defs>
      <rect width={W} height={H} fill={style === "plain" ? color : "url(#bg-grad)"} />
      {style !== "plain" && (
        <>
          <ellipse cx={W * 0.08} cy={H * 0.1} rx={W * 0.32} ry={H * 0.42} fill="url(#bg-blob-a)" />
          <ellipse cx={W * 0.95} cy={H * 0.92} rx={W * 0.36} ry={H * 0.46} fill="url(#bg-blob-b)" />
        </>
      )}
      {style === "grid" && <rect width={W} height={H} fill="url(#bg-grid)" />}
      {style === "dots" && <rect width={W} height={H} fill="url(#bg-dots)" />}
      {style === "paper" && <rect width={W} height={H} fill="url(#bg-dots)" opacity="0.5" />}
    </g>
  );
}
