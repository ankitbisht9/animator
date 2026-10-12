/**
 * Component spec contract (the engine half of `defineComponent`, ADR-008).
 * Renderers live in @animator/components and are joined to specs by `type`.
 */
import type { z } from "zod";
import type { Rect } from "../math/rect";
import type { Vec2 } from "../math/vec";
import type { Theme } from "../theme/theme";

export type Category = "primitive" | "annotation" | "math" | "physics" | "ui";

export const ENTRANCE_STYLES = [
  "auto",
  "fade",
  "pop",
  "grow",
  "draw",
  "write",
  "slide_up",
  "slide_down",
  "slide_left",
  "slide_right",
  "zoom",
  "none",
] as const;
export type EntranceStyle = (typeof ENTRANCE_STYLES)[number];

export const EXIT_STYLES = ["fade", "shrink", "slide_up", "slide_down", "slide_left", "slide_right", "none"] as const;
export type ExitStyle = (typeof EXIT_STYLES)[number];

/** Params after bindings and references are resolved for a given frame. */
export type Params = Record<string, unknown>;

export interface GeoCtx {
  theme: Theme;
  /** Compile-time prepared data (e.g. typeset equation), if the spec has `prepare`. */
  prep: unknown;
}

export interface Geometry {
  /** Bounds in the object's local frame (world frame for `worldSpace` components). */
  bbox: Rect;
  /** Component-specific anchors in the same frame. The nine box anchors are always added. */
  anchors?: Record<string, Vec2>;
}

export interface DefaultStyle {
  fill?: string;
  stroke?: string;
  text?: string;
  strokeWidth?: number;
}

export interface ComponentSpec {
  type: string;
  category: Category;
  description: string;
  /** Canonical params schema (strict object, defaults applied by Zod). */
  params: z.ZodObject;
  /** Names of component-specific anchors (in addition to the nine box anchors). */
  anchors?: readonly string[];
  geometry: (p: Params, ctx: GeoCtx) => Geometry;
  /** Default entrance style used by `create` with style "auto". */
  entrance: Exclude<EntranceStyle, "auto">;
  defaultStyle?: DefaultStyle;
  /** Supports `draw` (stroke write-on). */
  draw?: boolean;
  /** Supports `write` (text/equation reveal). */
  write?: boolean;
  /** Text-like: participates in label overlap and legibility checks. */
  labelLike?: boolean;
  /** Excluded from the clutter limit and overlap checks. */
  decorative?: boolean;
  /** Plays once during its entrance, then is gone: never carried into the next scene. */
  oneShot?: boolean;
  /** Geometry is expressed in world coordinates (endpoints come from params). */
  worldSpace?: boolean;
  /** Written by the pipeline, never by the model: left out of the LLM schema, the catalog and the docs. */
  hidden?: boolean;
  /** Params that hold a Point (raw or anchor reference), resolved every frame. */
  pointParams?: readonly string[];
  /** Params that reference another object id (creates an evaluation dependency). */
  objectParams?: readonly string[];
  /** Params holding text (string or binding). */
  textParams?: readonly string[];
  /** Params holding a ColorRef (animatable with color interpolation). */
  colorParams?: readonly string[];
  /** Numeric params whose default comes from a physics solver when bound. */
  semanticRoles: readonly string[];
  /** Example object definitions (without id) for the LLM catalog and docs. */
  examples: readonly Record<string, unknown>[];
  /** Expensive compile-time preparation, keyed by `prepareKey`. */
  prepare?: (p: Params) => unknown;
  prepareKey?: (p: Params) => string;
  /** Component-specific semantic checks (e.g. path syntax, TeX allowlist). */
  validate?: (p: Params) => { code: string; message: string; param: string; suggestion?: string }[];
  /** Anchors that depend on params (e.g. equation terms, pipe sections). */
  dynamicAnchors?: (p: Params) => string[];
  /** Smallest font size (canvas px) this component draws text at, for legibility checks. */
  minTextSize?: (p: Params, theme: Theme) => number | null;
  /** True when text is drawn straight on the background (so contrast must be checked). */
  bareText?: (p: Params) => boolean;
  /** Points along a connector's stroke (same frame as `geometry`), for "runs across a label" checks. */
  strokeLine?: (p: Params, theme: Theme) => Vec2[];
}

/**
 * What an object param resolves to during evaluation: the referenced object's
 * layout bounds, layout matrix and resolved params (stored at `__<param>`).
 */
export interface ObjectRef {
  box: Rect;
  matrix: readonly [number, number, number, number, number, number];
  params: Params;
}

export const objectRefOf = (p: Params, key: string): ObjectRef | undefined => {
  const v = p[`__${key}`] as ObjectRef | undefined;
  return v && typeof v === "object" && "box" in v ? v : undefined;
};

// Small typed accessors for resolved params.
export const pNum = (p: Params, k: string, d = 0): number => {
  const v = p[k];
  return typeof v === "number" && Number.isFinite(v) ? v : d;
};
export const pStr = (p: Params, k: string, d = ""): string => {
  const v = p[k];
  return typeof v === "string" ? v : d;
};
export const pBool = (p: Params, k: string, d = false): boolean => {
  const v = p[k];
  return typeof v === "boolean" ? v : d;
};
export const pVec = (p: Params, k: string, d: Vec2 = { x: 0, y: 0 }): Vec2 => {
  const v = p[k] as { x?: unknown; y?: unknown } | undefined;
  return v && typeof v.x === "number" && typeof v.y === "number" ? { x: v.x, y: v.y } : d;
};
