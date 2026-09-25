/** Param schemas and helpers shared by several components. */
import { z } from "zod";
import { Binding } from "../dsl/primitives";
import { type FontWeight, layoutText, type TextAlign, type TextLayout } from "../text/layout";
import type { Theme } from "../theme/theme";
import { type Params, pNum, pStr } from "./spec";

export const TEXT_SIZE_NAMES = ["title", "heading", "body", "label", "caption", "small"] as const;
export const TextSize = z
  .union([z.enum(TEXT_SIZE_NAMES), z.number().min(8).max(220)])
  .describe("Named size from the theme type scale, or pixels on the 1600x900 canvas");

export const WEIGHT_NAMES = ["regular", "semibold", "bold"] as const;
export const Weight = z.enum(WEIGHT_NAMES);

export const TextValue = z.union([z.string().max(400), Binding]);

export function resolveTextSize(
  p: Params,
  theme: Theme,
  key = "size",
  fallback: (typeof TEXT_SIZE_NAMES)[number] = "body",
): number {
  const v = p[key];
  if (typeof v === "number") return v;
  const name = (
    typeof v === "string" && (TEXT_SIZE_NAMES as readonly string[]).includes(v) ? v : fallback
  ) as keyof Theme["typography"]["sizes"];
  return theme.typography.sizes[name];
}

export function resolveWeight(p: Params, key = "weight", fallback: FontWeight = 400): FontWeight {
  const v = p[key];
  if (v === "bold") return 700;
  if (v === "semibold") return 600;
  if (v === "regular") return 400;
  return fallback;
}

/** Text layout for a resolved text param (bindings already turned into strings). */
export function textLayoutOf(
  p: Params,
  theme: Theme,
  opts: {
    textKey?: string;
    sizeKey?: string;
    size?: number;
    weight?: FontWeight;
    align?: TextAlign;
    maxWidth?: number;
  } = {},
): TextLayout {
  const text = pStr(p, opts.textKey ?? "text");
  const size = opts.size ?? resolveTextSize(p, theme, opts.sizeKey ?? "size");
  return layoutText(text, {
    size,
    weight: opts.weight ?? resolveWeight(p),
    align: opts.align ?? ((pStr(p, "align", "center") as TextAlign) || "center"),
    maxWidth: opts.maxWidth ?? (pNum(p, "maxWidth", 0) || undefined),
    lineHeight: pNum(p, "lineHeight", theme.typography.lineHeight),
  });
}
