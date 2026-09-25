/**
 * Shared DSL building blocks. The Zod schemas here are the single source of truth
 * for the canonical DSL; JSON Schemas (canonical and LLM-facing) are generated
 * from them. `x-llm` metadata tells the LLM-schema generator to substitute a
 * stricter projection (e.g. an enum of color roles instead of a free string).
 */
import { z } from "zod";
import { EASING_NAMES } from "../interp/easing";
import { COLOR_ROLES, COLOR_VARIANTS } from "../theme/theme";

export const ID_PATTERN = /^[a-z][a-z0-9_]{0,47}$/;
export const Id = z
  .string()
  .regex(ID_PATTERN, "ids are lowercase snake_case, start with a letter, at most 48 characters");

export const MAX_SCENE_DURATION = 120;

export const Seconds = z.number().min(0).max(MAX_SCENE_DURATION);

export const Vec = z
  .strictObject({ x: z.number(), y: z.number() })
  .meta({ id: "Vec", description: "A point in scene pixels (1600x900 canvas, y down)." });

export const EasingSchema = z
  .union([
    z.enum(EASING_NAMES),
    z.strictObject({
      cubic_bezier: z.array(z.number()).length(4).describe("CSS cubic-bezier control points [x1, y1, x2, y2]"),
    }),
  ])
  .meta({ id: "Easing" });

/** All valid color role references: `role` and `role.variant`. */
export const COLOR_REFS: string[] = COLOR_ROLES.flatMap((r) => [r, ...COLOR_VARIANTS.map((v) => `${r}.${v}`)]);

export const ColorRef = z
  .string()
  .max(32)
  .meta({
    id: "ColorRef",
    description:
      "A theme color role (e.g. 'fluid', 'force_buoyant', 'object_a'), optionally with a variant suffix '.light', '.dark' or '.soft'. Never raw hex.",
    "x-llm": { type: "string", enum: COLOR_REFS },
  });

/** A value computed by the engine: a solver output, scene value or expression, never LLM arithmetic. */
export const Binding = z
  .strictObject({
    bind: z
      .string()
      .max(240)
      .describe("Expression over solver outputs and scene values, e.g. 'buoy.buoyantForce' or 'rho_w * g'"),
    format: z.string().max(32).optional().describe("Number format, e.g. '0.0 N', '#,##0 kg', '0.00 m/s'"),
  })
  .meta({ id: "Binding" });

export const NumOrBind = z.union([z.number(), Binding]);
export const TextOrBind = z.union([z.string().max(400), Binding]);

/** Reference to a named anchor of another object: "object_id.anchor_name". */
export const ANCHOR_REF_PATTERN = /^[a-z][a-z0-9_]{0,47}\.[a-z][a-z0-9_]{0,31}$/;
export const AnchorRef = z
  .string()
  .regex(ANCHOR_REF_PATTERN, "anchor references look like 'object_id.anchor'")
  .describe("Another object's anchor, e.g. 'block.center' or 'tank.water_surface'");

/** A point: raw coordinates or an anchor reference that follows its object over time. */
export const Point = z.union([Vec, AnchorRef]).meta({ id: "Point" });

export const SIDES = [
  "above",
  "below",
  "left",
  "right",
  "center",
  "inside_top",
  "inside_bottom",
  "inside_left",
  "inside_right",
] as const;
export const Side = z.enum(SIDES);

export const Align = z.enum(["start", "center", "end"]);

export const AnchorName = z.string().regex(/^[a-z][a-z0-9_]{0,31}$/);

export const SlotPlace = z.strictObject({
  slot: z.string().max(48).describe("Layout slot, e.g. 'stage.center', 'stage.left', 'title_bar', 'equation_panel'"),
  anchor: AnchorName.optional().describe("Which of this object's anchors sits on the slot center (default 'center')"),
  offset: Vec.optional(),
});

export const RelativePlace = z.strictObject({
  relativeTo: Id,
  side: Side,
  gap: z.number().min(-400).max(800).optional(),
  align: Align.optional(),
  follow: z.boolean().optional().describe("Keep following the reference object as it moves"),
});

export const PinPlace = z.strictObject({
  at: AnchorRef.describe("Pin to another object's anchor"),
  anchor: AnchorName.optional().describe("Which of this object's anchors is pinned (default 'center')"),
  offset: Vec.optional(),
  follow: z.boolean().optional(),
});

export const RawPlace = z.strictObject({
  x: z.number(),
  y: z.number(),
  anchor: AnchorName.optional(),
});

export const Place = z.union([SlotPlace, RelativePlace, PinPlace, RawPlace]).meta({
  id: "Place",
  description:
    "Where an object goes. Prefer a layout slot or placement relative to another object; raw x/y is an escape hatch.",
});

export const Style = z
  .strictObject({
    fill: ColorRef.optional(),
    stroke: ColorRef.optional(),
    text: ColorRef.optional(),
    strokeWidth: z.number().min(0).max(40).optional(),
    shadow: z.boolean().optional(),
    dashed: z.boolean().optional(),
  })
  .meta({ id: "Style" });

export type VecT = z.infer<typeof Vec>;
export type BindingT = z.infer<typeof Binding>;
export type PlaceT = z.infer<typeof Place>;
export type StyleT = z.infer<typeof Style>;
export type PointT = z.infer<typeof Point>;

export const isBinding = (v: unknown): v is BindingT =>
  typeof v === "object" && v !== null && typeof (v as { bind?: unknown }).bind === "string";

export const isAnchorRef = (v: unknown): v is string => typeof v === "string" && ANCHOR_REF_PATTERN.test(v);
