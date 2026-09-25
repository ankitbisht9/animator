/** A small curated icon set (24x24 line icons, round caps). Paths are static data, never input. */
import type { ReactNode } from "react";

const ICONS: Record<string, { d: string; fill?: boolean }> = {
  lightbulb: {
    d: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.8 10.6c.8.7 1.3 1.6 1.3 2.6V17h5v-.8c0-1 .5-1.9 1.3-2.6A6 6 0 0 0 12 3z",
  },
  check: { d: "M5 12.5l4.5 4.5L19 7.5" },
  cross: { d: "M6.5 6.5l11 11M17.5 6.5l-11 11" },
  question: { d: "M9 9.2a3 3 0 1 1 4.2 2.7c-.8.4-1.2 1.1-1.2 2V15M12 18.8h.01" },
  star: { d: "M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.8z", fill: true },
  info: { d: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.6h.01" },
  warning: { d: "M12 3.8l9.2 16H2.8zM12 10v4.4M12 17.4h.01" },
  magnifier: { d: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.3 15.3l5.2 5.2" },
  drop: {
    d: "M12 3.2c3.6 4.4 6.2 7.9 6.2 11.1a6.2 6.2 0 0 1-12.4 0c0-3.2 2.6-6.7 6.2-11.1zM9.2 14.6a2.9 2.9 0 0 0 2.4 2.9",
  },
  balance: {
    d: "M12 4v16M7.5 20h9M4.5 7.5h15M4.5 7.5l-2.6 6.2a2.8 2.8 0 0 0 5.2 0zM19.5 7.5l-2.6 6.2a2.8 2.8 0 0 0 5.2 0zM12 4h.01",
  },
  clock: { d: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.2V12l3.4 2.1" },
  arrow_up: { d: "M12 19.5V4.5M5.8 10.7L12 4.5l6.2 6.2" },
  arrow_down: { d: "M12 4.5v15M5.8 13.3l6.2 6.2 6.2-6.2" },
  sparkle: {
    d: "M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9zM19 16l.8 2.2 2.2.8-2.2.8L19 22l-.8-2.2-2.2-.8 2.2-.8z",
    fill: true,
  },
  target: {
    d: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM12 12.8a.8.8 0 1 0 0-1.6.8.8 0 0 0 0 1.6z",
  },
  book: { d: "M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5zM4 20.5A2.5 2.5 0 0 0 6.5 23H20V18M8 7.5h8" },
  ship: {
    d: "M3 13.5h18l-2.3 4.3a1.8 1.8 0 0 1-1.6 1H6.9a1.8 1.8 0 0 1-1.6-1zM6.5 13.5V10h11v3.5M10 10V6.5h3.5V10M2.5 21.5c1.6 0 1.6-1 3.2-1s1.6 1 3.2 1 1.6-1 3.2-1 1.6 1 3.2 1 1.6-1 3.2-1 1.6 1 3.2 1",
  },
};

/** Draw icon `name` centered at (0,0) with the given size and color. */
export function IconGlyph({
  name,
  size,
  color,
  strokeWidth = 2,
}: {
  name: string;
  size: number;
  color: string;
  strokeWidth?: number;
}): ReactNode {
  const icon = ICONS[name] ?? ICONS.info;
  if (!icon) return null;
  const s = size / 24;
  return (
    <g transform={`translate(${-size / 2} ${-size / 2}) scale(${s})`}>
      <path
        d={icon.d}
        fill={icon.fill ? color : "none"}
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </g>
  );
}
