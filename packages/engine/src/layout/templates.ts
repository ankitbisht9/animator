/**
 * Layout templates and named slots. LLMs are poor at raw coordinates, so scenes
 * place objects into slots ("stage.left", "equation_panel", ...) or relative to
 * other objects. Every template reserves the caption band at the bottom.
 */

import type { Rect } from "../math/rect";
import { CANVAS } from "../version";

export const LAYOUT_IDS = ["stage_with_caption", "stage_with_equation", "split", "title_card", "no_title"] as const;
export type LayoutId = (typeof LAYOUT_IDS)[number];

const W = CANVAS.width;
const MX = 48;
const TITLE: Rect = { x: MX, y: 26, w: W - 2 * MX, h: 78 };
export const CAPTION_BAR: Rect = { x: 150, y: 752, w: W - 300, h: 116 };
const STAGE_Y = 116;
const STAGE_H = 620;

const REGIONS: Record<LayoutId, Record<string, Rect>> = {
  stage_with_caption: {
    title_bar: TITLE,
    stage: { x: MX, y: STAGE_Y, w: W - 2 * MX, h: STAGE_H },
    caption_bar: CAPTION_BAR,
  },
  stage_with_equation: {
    title_bar: TITLE,
    stage: { x: MX, y: STAGE_Y, w: 940, h: STAGE_H },
    equation_panel: { x: 1012, y: STAGE_Y, w: W - 1012 - MX, h: STAGE_H },
    caption_bar: CAPTION_BAR,
  },
  split: {
    title_bar: TITLE,
    left: { x: MX, y: STAGE_Y, w: 736, h: STAGE_H },
    right: { x: W - MX - 736, y: STAGE_Y, w: 736, h: STAGE_H },
    caption_bar: CAPTION_BAR,
  },
  title_card: {
    hero: { x: 150, y: 150, w: W - 300, h: 520 },
    caption_bar: CAPTION_BAR,
  },
  no_title: {
    stage: { x: MX, y: 36, w: W - 2 * MX, h: 700 },
    caption_bar: CAPTION_BAR,
  },
};

const SUBSLOTS = [
  "center",
  "top",
  "bottom",
  "left",
  "right",
  "top_left",
  "top_right",
  "bottom_left",
  "bottom_right",
] as const;

/** Sub-rectangle of a region on a 3x3 grid. Edge slots span the full opposite axis. */
function subRect(r: Rect, sub: (typeof SUBSLOTS)[number]): Rect {
  const cw = r.w / 3;
  const ch = r.h / 3;
  const col = sub.includes("left") ? 0 : sub.includes("right") ? 2 : 1;
  const row = sub.includes("top") ? 0 : sub.includes("bottom") ? 2 : 1;
  const spansX = sub === "top" || sub === "bottom";
  const spansY = sub === "left" || sub === "right";
  return {
    x: spansX ? r.x : r.x + col * cw,
    y: spansY ? r.y : r.y + row * ch,
    w: spansX ? r.w : cw,
    h: spansY ? r.h : ch,
  };
}

export interface LayoutTemplate {
  id: LayoutId;
  regions: Record<string, Rect>;
  slots: Record<string, Rect>;
}

function build(id: LayoutId): LayoutTemplate {
  const regions = REGIONS[id];
  const slots: Record<string, Rect> = {};
  for (const [name, r] of Object.entries(regions)) {
    slots[name] = r;
    if (name === "caption_bar" || name === "title_bar") continue;
    for (const sub of SUBSLOTS) slots[`${name}.${sub}`] = subRect(r, sub);
  }
  return { id, regions, slots };
}

export const LAYOUTS: Record<LayoutId, LayoutTemplate> = Object.fromEntries(
  LAYOUT_IDS.map((id) => [id, build(id)]),
) as Record<LayoutId, LayoutTemplate>;

export const getLayout = (id: string | undefined): LayoutTemplate =>
  LAYOUTS[(id as LayoutId) ?? "stage_with_caption"] ?? LAYOUTS.stage_with_caption;

/** The canvas minus safe margins. */
export const SAFE_AREA: Rect = { x: 32, y: 20, w: CANVAS.width - 64, h: CANVAS.height - 40 };
export const CANVAS_RECT: Rect = { x: 0, y: 0, w: CANVAS.width, h: CANVAS.height };
