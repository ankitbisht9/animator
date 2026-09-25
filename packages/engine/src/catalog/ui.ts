/** Engagement components: title cards, summary cards, icons, badges, speech bubbles, bursts. */
import { z } from "zod";
import { ColorRef, Point } from "../dsl/primitives";
import { centered } from "../math/rect";
import { layoutText, type TextLayout } from "../text/layout";
import type { Theme } from "../theme/theme";
import { IconName, pillLayout } from "./annotation";
import { resolveTextSize, TextSize } from "./common";
import { defineComponentSpec } from "./registry";
import { type Params, pNum, pStr } from "./spec";

// ------------------------------------------------------------------ title card

export function titleCardLayout(p: Params, theme: Theme) {
  const width = pNum(p, "width", 1100);
  const kicker = pStr(p, "kicker")
    ? layoutText(pStr(p, "kicker").toUpperCase(), {
        size: theme.typography.sizes.small,
        weight: 700,
        align: "center",
        letterSpacing: 2,
      })
    : null;
  const title = layoutText(pStr(p, "title"), {
    size: pNum(p, "titleSize", 76),
    weight: 700,
    align: "center",
    maxWidth: width,
    lineHeight: 1.12,
  });
  const subtitle = pStr(p, "subtitle")
    ? layoutText(pStr(p, "subtitle"), {
        size: theme.typography.sizes.body,
        weight: 400,
        align: "center",
        maxWidth: width * 0.8,
        lineHeight: 1.35,
      })
    : null;
  const icon = p.icon ? 92 : 0;
  const gap = 18;
  const height =
    (icon ? icon + gap : 0) +
    (kicker ? kicker.height + 10 : 0) +
    title.height +
    34 +
    (subtitle ? subtitle.height + gap : 0);
  return { width, icon, gap, kicker, title, subtitle, height };
}

export const titleCard = defineComponentSpec({
  type: "title_card",
  category: "ui",
  description:
    "A big, friendly title with an optional kicker, subtitle and icon, and an underline that draws itself. Use for lesson and chapter openings.",
  params: z.strictObject({
    title: z.string().min(1).max(80),
    subtitle: z.string().max(160).optional(),
    kicker: z.string().max(40).optional(),
    icon: IconName.optional(),
    role: ColorRef.default("accent"),
    width: z.number().min(300).max(1500).default(1100),
    titleSize: z.number().min(24).max(140).default(76),
  }),
  geometry: (p, ctx) => {
    const l = titleCardLayout(p, ctx.theme);
    return { bbox: centered(0, 0, Math.max(l.title.width, l.subtitle?.width ?? 0, 200), l.height) };
  },
  entrance: "write",
  write: true,
  labelLike: true,
  colorParams: ["role"],
  textParams: ["title", "subtitle", "kicker"],
  semanticRoles: ["title"],
  examples: [
    {
      type: "title_card",
      params: { kicker: "Physics", title: "Why do things **float**?", subtitle: "Archimedes' principle", icon: "drop" },
      place: { slot: "hero" },
    },
  ],
});

// ------------------------------------------------------------------ card (bullet list)

export interface CardLayout {
  width: number;
  height: number;
  pad: number;
  title: TextLayout | null;
  items: { text: TextLayout; y: number }[];
  bullet: number;
}

export function cardLayout(p: Params, theme: Theme): CardLayout {
  const width = pNum(p, "width", 640);
  const pad = 30;
  const size = resolveTextSize(p, theme, "size", "body");
  const bullet = size * 1.25;
  const title = pStr(p, "title")
    ? layoutText(pStr(p, "title"), {
        size: theme.typography.sizes.heading * 0.82,
        weight: 700,
        align: "left",
        maxWidth: width - 2 * pad,
      })
    : null;
  const items: CardLayout["items"] = [];
  let y = pad + (title ? title.height + 14 : 0);
  for (const it of Array.isArray(p.items) ? (p.items as unknown[]) : []) {
    const text = layoutText(String(it), {
      size,
      weight: 400,
      align: "left",
      maxWidth: width - 2 * pad - bullet - 12,
      lineHeight: 1.3,
    });
    items.push({ text, y });
    y += text.height + size * 0.45;
  }
  return { width, height: y + pad - size * 0.45, pad, title, items, bullet };
}

export const card = defineComponentSpec({
  type: "card",
  category: "ui",
  description:
    "A rounded card with an optional title and a list of short points that appear one by one (write). Great for summaries and step lists.",
  params: z.strictObject({
    title: z.string().max(60).optional(),
    items: z.array(z.string().min(1).max(160)).min(1).max(7),
    marker: z.enum(["check", "dot", "number", "star", "arrow"]).default("check"),
    role: ColorRef.default("accent"),
    width: z.number().min(200).max(1400).default(640),
    size: TextSize.default("body"),
  }),
  geometry: (p, ctx) => {
    const l = cardLayout(p, ctx.theme);
    return { bbox: centered(0, 0, l.width, l.height) };
  },
  entrance: "write",
  write: true,
  labelLike: true,
  colorParams: ["role"],
  textParams: ["title"],
  semanticRoles: ["summary", "list"],
  examples: [
    {
      type: "card",
      params: {
        title: "Remember",
        items: ["Water pushes up on things in it.", "The push equals the weight of water moved aside."],
      },
    },
  ],
});

// ------------------------------------------------------------------ icon, badge

export const icon = defineComponentSpec({
  type: "icon",
  category: "ui",
  description:
    "A friendly line icon (lightbulb, check, cross, question, star, info, warning, magnifier, drop, balance, clock, arrow_up, arrow_down, sparkle, target, book), optionally on a soft round badge.",
  params: z.strictObject({
    name: IconName,
    size: z.number().min(12).max(400).default(72),
    role: ColorRef.default("accent"),
    badge: z.boolean().default(true),
  }),
  geometry: (p) => {
    const s = pNum(p, "size", 72) * (p.badge === false ? 1 : 1.5);
    return { bbox: centered(0, 0, s, s) };
  },
  entrance: "pop",
  colorParams: ["role"],
  semanticRoles: ["symbol"],
  examples: [{ type: "icon", params: { name: "lightbulb", role: "highlight" } }],
});

export const badge = defineComponentSpec({
  type: "badge",
  category: "ui",
  description: "A small colored chip with text (and optional icon), e.g. 'FLOATS', 'Step 2', 'Key idea'.",
  params: z.strictObject({
    text: z.string().min(1).max(40),
    role: ColorRef.default("accent"),
    icon: IconName.optional(),
    size: TextSize.default("small"),
  }),
  geometry: (p, ctx) => {
    const size = resolveTextSize(p, ctx.theme, "size", "small");
    const l = pillLayout(pStr(p, "text"), size, 700);
    return { bbox: centered(0, 0, l.w + (p.icon ? size * 1.3 : 0), l.h) };
  },
  entrance: "pop",
  labelLike: true,
  colorParams: ["role"],
  textParams: ["text"],
  semanticRoles: ["tag"],
  examples: [{ type: "badge", params: { text: "FLOATS", role: "positive", icon: "check" } }],
});

// ------------------------------------------------------------------ speech bubble

export function bubbleLayout(p: Params, theme: Theme) {
  const size = resolveTextSize(p, theme, "size", "label");
  const text = layoutText(pStr(p, "text"), {
    size,
    weight: 400,
    maxWidth: pNum(p, "maxWidth", 380),
    align: "center",
    lineHeight: 1.3,
  });
  const pad = size * 0.75;
  return { text, w: text.width + 2 * pad, h: text.height + 2 * pad, pad };
}

export const speechBubble = defineComponentSpec({
  type: "speech_bubble",
  category: "ui",
  description:
    "A speech or thought bubble with a tail pointing at `pointTo` (e.g. a character's face). Great for questions like 'Why does it float?'.",
  params: z.strictObject({
    text: z.string().min(1).max(200),
    pointTo: Point.optional(),
    kind: z.enum(["speech", "thought"]).default("speech"),
    maxWidth: z.number().min(80).max(800).default(380),
    size: TextSize.default("label"),
    role: ColorRef.default("surface"),
  }),
  geometry: (p, ctx) => {
    const l = bubbleLayout(p, ctx.theme);
    return { bbox: centered(0, 0, l.w, l.h) };
  },
  entrance: "pop",
  labelLike: true,
  pointParams: ["pointTo"],
  colorParams: ["role"],
  textParams: ["text"],
  semanticRoles: ["dialogue", "question"],
  examples: [{ type: "speech_bubble", params: { text: "Why does the heavy ship **float**?", pointTo: "block.top" } }],
});

// ------------------------------------------------------------------ burst

export const burst = defineComponentSpec({
  type: "burst",
  category: "ui",
  description:
    "A one-shot burst: confetti, stars or rays for 'aha!' moments, or a water splash (droplets arcing up and falling back). Create it; it plays during its entrance and then fades away.",
  params: z.strictObject({
    kind: z.enum(["confetti", "stars", "rays", "splash"]).default("confetti"),
    count: z.number().int().min(4).max(80).default(26),
    radius: z.number().min(20).max(800).default(170),
    role: ColorRef.default("highlight"),
  }),
  geometry: (p) => {
    const r = pNum(p, "radius", 170);
    return { bbox: centered(0, 0, 2 * r, 2 * r) };
  },
  entrance: "write",
  write: true,
  decorative: true,
  oneShot: true,
  colorParams: ["role"],
  semanticRoles: ["celebration"],
  examples: [{ type: "burst", params: { kind: "stars" }, place: { at: "equation.center" } }],
});
