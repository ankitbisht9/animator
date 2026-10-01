import { type Theme, ThemeSchema } from "./theme";

const shared = {
  hexWhitelist: [],
  typography: {
    family: "Lexend",
    // Canvas pixels (1600x900 logical canvas; 1080p output renders them 1.2x).
    sizes: { title: 60, heading: 44, body: 32, label: 28, caption: 30, small: 24 },
    minSizePx1080: 22,
    lineHeight: 1.3,
  },
  stroke: { thin: 2, normal: 4, thick: 7 },
  radius: { sm: 8, md: 16, lg: 28 },
  arrowHead: { length: 22, width: 22 },
  spacing: [4, 8, 12, 16, 24, 32, 48, 64, 96],
  safeMargin: { x: 48, y: 32 },
  motion: {
    enter: { duration: 0.7, easing: "back_out" },
    exit: { duration: 0.5, easing: "ease_in" },
    move: { duration: 1.2, easing: "ease_in_out" },
    transform: { duration: 1.0, easing: "ease_in_out" },
    fade: { duration: 0.6, easing: "ease_in_out" },
    color: { duration: 0.6, easing: "ease_in_out" },
    draw: { duration: 1.0, easing: "ease_in_out" },
    write: { duration: 1.2, easing: "ease_out" },
    emphasis: { duration: 1.0, easing: "ease_in_out" },
    camera: { duration: 1.4, easing: "ease_in_out" },
    label: { duration: 0.6, easing: "back_out" },
  },
  effects: { shadows: true, shadowOpacity: 0.14, gradients: true, glow: true },
  minContrast: 3.0,
} as const;

/**
 * Educational Clean (light). Okabe-Ito-derived semantic colors keep forces
 * distinguishable for color-blind viewers: gravity = vermillion, buoyancy =
 * bluish green, normal = blue, applied = purple (also differentiated by labels).
 */
export const EDUCATIONAL_CLEAN: Theme = ThemeSchema.parse({
  ...shared,
  id: "educational_clean",
  name: "Educational Clean",
  mode: "light",
  colors: {
    background: "#f4f7fc",
    surface: "#ffffff",
    surface_alt: "#eaf0fa",
    text: "#1c2541",
    muted: "#5f6d88",
    accent: "#4f46e5",
    highlight: "#ffc43d",
    object_a: "#f08a24",
    object_b: "#c9559a",
    object_c: "#7c5ce0",
    fluid: "#2f9be0",
    glass: "#8fb8e0",
    grid: "#d9e2f0",
    shadow: "#1c2541",
    force_gravity: "#d9541a",
    force_buoyant: "#0b9b6c",
    force_normal: "#2f6fdb",
    force_applied: "#9b51e0",
    velocity: "#e09a00",
    pressure: "#b24bd6",
    positive: "#0b9b6c",
    negative: "#d9541a",
    red: "#e0453a",
    orange: "#f08a24",
    yellow: "#f5c518",
    green: "#2fa84f",
    blue: "#2f6fdb",
    sky: "#8fd0f5",
    purple: "#7c5ce0",
    pink: "#e0609c",
    brown: "#9a6534",
    gray: "#8a94a6",
  },
});

export const EDUCATIONAL_CLEAN_DARK: Theme = ThemeSchema.parse({
  ...shared,
  id: "educational_clean_dark",
  name: "Educational Clean (dark)",
  mode: "dark",
  effects: { ...shared.effects, shadowOpacity: 0.35 },
  colors: {
    background: "#0f1729",
    surface: "#18233d",
    surface_alt: "#1f2c4b",
    text: "#eef2fa",
    muted: "#9eabc6",
    accent: "#8b83ff",
    highlight: "#ffd166",
    object_a: "#f7a24f",
    object_b: "#e07cc0",
    object_c: "#a690f5",
    fluid: "#4fb5f2",
    glass: "#7497c0",
    grid: "#26345a",
    shadow: "#000000",
    force_gravity: "#ff7a45",
    force_buoyant: "#2fd39a",
    force_normal: "#5e9bff",
    force_applied: "#b98bff",
    velocity: "#ffc43d",
    pressure: "#d17bef",
    positive: "#2fd39a",
    negative: "#ff7a45",
    red: "#ff6b5e",
    orange: "#f7a24f",
    yellow: "#ffd84d",
    green: "#4fd17a",
    blue: "#5e9bff",
    sky: "#7cc8f2",
    purple: "#a690f5",
    pink: "#f088bd",
    brown: "#c08a52",
    gray: "#9eabc6",
  },
});

export const THEMES: Record<string, Theme> = {
  [EDUCATIONAL_CLEAN.id]: EDUCATIONAL_CLEAN,
  [EDUCATIONAL_CLEAN_DARK.id]: EDUCATIONAL_CLEAN_DARK,
};

export const THEME_IDS = Object.keys(THEMES) as [string, ...string[]];
export const DEFAULT_THEME_ID = EDUCATIONAL_CLEAN.id;

export function getTheme(id: string | undefined): Theme {
  return (id && THEMES[id]) || EDUCATIONAL_CLEAN;
}
