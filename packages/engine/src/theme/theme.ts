/**
 * Themes are data, validated by schema. The LLM refers to semantic color roles
 * ("force_buoyant", "fluid", ...), never hex, so the same concept keeps the same
 * color across a whole video (weight is always the same vermillion, buoyancy the
 * same bluish green). Role variants: `role.light`, `role.dark`, `role.soft`.
 */
import { z } from "zod";
import { adjustLightness, isHexColor, mixHex, withAlpha } from "../color/color";
import type { Easing } from "../interp/easing";

export const COLOR_ROLES = [
  "background",
  "surface",
  "surface_alt",
  "text",
  "muted",
  "accent",
  "highlight",
  "object_a",
  "object_b",
  "object_c",
  "fluid",
  "glass",
  "grid",
  "shadow",
  "force_gravity",
  "force_buoyant",
  "force_normal",
  "force_applied",
  "velocity",
  "pressure",
  "positive",
  "negative",
  // Named colours, for things whose real colour carries meaning (a blue sky, a green leaf).
  "red",
  "orange",
  "yellow",
  "green",
  "blue",
  "sky",
  "purple",
  "pink",
  "brown",
  "gray",
] as const;
export type ColorRole = (typeof COLOR_ROLES)[number];

export const COLOR_VARIANTS = ["light", "dark", "soft"] as const;
export type ColorVariant = (typeof COLOR_VARIANTS)[number];

const Hex = z.string().refine(isHexColor, "expected a hex color");

const MotionDefault = z.strictObject({ duration: z.number().positive(), easing: z.string() });

export const ThemeSchema = z.strictObject({
  id: z.string(),
  name: z.string(),
  mode: z.enum(["light", "dark"]),
  colors: z.strictObject(Object.fromEntries(COLOR_ROLES.map((r) => [r, Hex])) as Record<ColorRole, typeof Hex>),
  /** Raw hex colors a scene may use directly (usually empty). */
  hexWhitelist: z.array(Hex),
  typography: z.strictObject({
    family: z.string(),
    sizes: z.strictObject({
      title: z.number(),
      heading: z.number(),
      body: z.number(),
      label: z.number(),
      caption: z.number(),
      small: z.number(),
    }),
    /** Minimum rendered text height at 1080p output, in pixels. */
    minSizePx1080: z.number(),
    lineHeight: z.number(),
  }),
  stroke: z.strictObject({ thin: z.number(), normal: z.number(), thick: z.number() }),
  radius: z.strictObject({ sm: z.number(), md: z.number(), lg: z.number() }),
  arrowHead: z.strictObject({ length: z.number(), width: z.number() }),
  spacing: z.array(z.number()),
  safeMargin: z.strictObject({ x: z.number(), y: z.number() }),
  motion: z.strictObject({
    enter: MotionDefault,
    exit: MotionDefault,
    move: MotionDefault,
    transform: MotionDefault,
    fade: MotionDefault,
    color: MotionDefault,
    draw: MotionDefault,
    write: MotionDefault,
    emphasis: MotionDefault,
    camera: MotionDefault,
    label: MotionDefault,
  }),
  effects: z.strictObject({
    shadows: z.boolean(),
    shadowOpacity: z.number(),
    gradients: z.boolean(),
    glow: z.boolean(),
  }),
  minContrast: z.number(),
});

export type Theme = z.infer<typeof ThemeSchema>;
export type MotionCategory = keyof Theme["motion"];

export function motionDefault(theme: Theme, cat: MotionCategory): { duration: number; easing: Easing } {
  const m = theme.motion[cat];
  return { duration: m.duration, easing: m.easing as Easing };
}

const ROLE_SET = new Set<string>(COLOR_ROLES);
const VARIANT_SET = new Set<string>(COLOR_VARIANTS);

/** Parse a color reference: `role`, `role.variant`, or a whitelisted hex. */
export function parseColorRef(ref: string): { role: ColorRole; variant: ColorVariant | null } | { hex: string } | null {
  if (isHexColor(ref)) return { hex: ref };
  const [role, variant, extra] = ref.split(".");
  if (extra !== undefined || !role || !ROLE_SET.has(role)) return null;
  if (variant !== undefined && !VARIANT_SET.has(variant)) return null;
  return { role: role as ColorRole, variant: (variant as ColorVariant | undefined) ?? null };
}

export function isColorRef(ref: string, theme?: Theme): boolean {
  const p = parseColorRef(ref);
  if (!p) return false;
  if ("hex" in p) return theme ? theme.hexWhitelist.map((h) => h.toLowerCase()).includes(ref.toLowerCase()) : false;
  return true;
}

const variantCache = new Map<string, string>();

/** Resolve a color reference to hex. Unknown references resolve to the text color (validators flag them). */
export function resolveColor(theme: Theme, ref: string | undefined, fallback: ColorRole = "text"): string {
  if (!ref) return theme.colors[fallback];
  const key = `${theme.id}|${ref}`;
  const hit = variantCache.get(key);
  if (hit) return hit;
  const p = parseColorRef(ref);
  let out: string;
  if (!p) out = theme.colors[fallback];
  else if ("hex" in p) out = p.hex;
  else {
    const base = theme.colors[p.role];
    const dark = theme.mode === "dark";
    switch (p.variant) {
      case "light":
        out = adjustLightness(base, dark ? -0.1 : 0.12);
        break;
      case "dark":
        out = adjustLightness(base, dark ? 0.1 : -0.14);
        break;
      case "soft":
        out = mixHex(base, theme.colors.surface, dark ? 0.72 : 0.82);
        break;
      default:
        out = base;
    }
  }
  variantCache.set(key, out);
  return out;
}

export { withAlpha };
