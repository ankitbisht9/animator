/**
 * Legibility rules per component, in one table: the smallest font size each
 * component draws (canvas px), and whether its text sits directly on the
 * background (so contrast must be checked). Used by the layout validator.
 */

import type { Theme } from "../theme/theme";
import { resolveTextSize } from "./common";
import { getSpec } from "./registry";
import type { ComponentSpec, Params } from "./spec";

type Rule = Pick<ComponentSpec, "minTextSize" | "bareText">;

const size = (p: Params, t: Theme, key: string, fallback: Parameters<typeof resolveTextSize>[3]) =>
  resolveTextSize(p, t, key, fallback);
const hasText = (p: Params, key: string) => typeof p[key] === "string" && (p[key] as string).length > 0;

const RULES: Record<string, Rule> = {
  text: { minTextSize: (p, t) => size(p, t, "size", "body"), bareText: () => true },
  label: { minTextSize: (p, t) => size(p, t, "size", "label"), bareText: (p) => p.style === "plain" },
  callout: { minTextSize: (p, t) => size(p, t, "size", "label") },
  note: { minTextSize: (_p, t) => t.typography.sizes.small },
  caption: { minTextSize: (p, t) => size(p, t, "size", "body") },
  card: { minTextSize: (p, t) => size(p, t, "size", "body") },
  readout: {
    minTextSize: (p, t) => (hasText(p, "label") ? t.typography.sizes.small : size(p, t, "size", "heading") * 0.55),
  },
  vector_arrow: { minTextSize: (p, t) => (hasText(p, "label") ? size(p, t, "labelSize", "small") : null) },
  dimension: { minTextSize: (p, t) => (hasText(p, "label") ? size(p, t, "labelSize", "small") : null) },
  bracket: { minTextSize: (p, t) => (hasText(p, "label") ? t.typography.sizes.small : null), bareText: () => true },
  // TeX x-height is ~0.82 of Lexend's at the same size; subscripts are 0.707 of that.
  equation: {
    minTextSize: (p) => (typeof p.size === "number" ? p.size : 52) * 0.82 * Math.SQRT1_2,
    bareText: (p) => p.box !== true,
  },
  title_card: {
    minTextSize: (p, t) => (hasText(p, "kicker") ? t.typography.sizes.small : t.typography.sizes.body),
    bareText: () => true,
  },
  badge: { minTextSize: (p, t) => size(p, t, "size", "small") },
  speech_bubble: { minTextSize: (p, t) => size(p, t, "size", "label") },
  pressure_gauge: { minTextSize: (p) => Math.max(19, (typeof p.radius === "number" ? p.radius : 64) * 0.24) },
  spring_scale: { minTextSize: (p, t) => (p.showValue === false ? null : t.typography.sizes.small) },
  graph: { minTextSize: (_p, t) => t.typography.sizes.small * 0.82 },
  axes: { minTextSize: (_p, t) => t.typography.sizes.small * 0.82 },
};

for (const [type, rule] of Object.entries(RULES)) {
  const spec = getSpec(type);
  if (!spec) throw new Error(`legibility rule for unknown component '${type}'`);
  Object.assign(spec, rule);
}
