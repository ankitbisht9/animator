/** Light by default (the brand: white and red); dark or the system's choice when the learner picks it.
 * The app's color (ADR-057) is the brand's red unless the person picks another of the avatar colors.
 * Both are remembered in the browser too, so the right look shows before the profile loads. */
import type { Accent, ThemePref } from "./api";

const KEY = "animator.theme";
let media: MediaQueryList | null = null;
let current: ThemePref = "light";
let motion = false;

/** The app's colors, as the avatar colors are named; crimson (the brand's red) is the default. */
export const ACCENTS: readonly Accent[] = ["crimson", "ruby", "amber", "teal", "indigo", "slate", "plum", "forest"];
export const ACCENT_NAMES: Record<Accent, string> = {
  crimson: "Crimson",
  ruby: "Ruby",
  amber: "Amber",
  teal: "Teal",
  indigo: "Indigo",
  slate: "Slate",
  plum: "Plum",
  forest: "Forest",
};
let accentNow: Accent = "crimson";

export function isAccent(v: unknown): v is Accent {
  return typeof v === "string" && (ACCENTS as readonly string[]).includes(v);
}

function resolve(pref: ThemePref): "light" | "dark" {
  if (pref !== "system") return pref;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function remember(): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ pref: current, reducedMotion: motion, accent: accentNow }));
  } catch {
    /* private mode */
  }
}

/** Show the app in this color (crimson: the default, set by no attribute). Anything else unknown
 * shows the default. */
export function applyAccent(accent: Accent | null | undefined, keep = true): void {
  accentNow = isAccent(accent) ? accent : "crimson";
  const root = document.documentElement;
  if (accentNow === "crimson") delete root.dataset.accent;
  else root.dataset.accent = accentNow;
  if (keep) remember();
}

/** The color shown now (a preview included). */
export function shownAccent(): Accent {
  return accentNow;
}

/** The page's theme, motion and (when given) color. */
export function applyTheme(pref: ThemePref, reducedMotion = false, accent?: Accent | null): void {
  current = pref;
  motion = reducedMotion;
  const root = document.documentElement;
  root.dataset.theme = resolve(pref);
  root.dataset.motion = reducedMotion ? "reduced" : "full";
  if (accent !== undefined) applyAccent(accent, false);
  remember();
  if (!media && window.matchMedia) {
    media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener?.("change", () => {
      if (current === "system") root.dataset.theme = resolve("system");
    });
  }
}

/** The last look this browser used (before anyone is signed in). */
export function restoreTheme(): void {
  let saved: { pref?: ThemePref; reducedMotion?: boolean; accent?: Accent } = {};
  try {
    saved = JSON.parse(window.localStorage.getItem(KEY) ?? "{}") as typeof saved;
  } catch {
    saved = {};
  }
  applyTheme(saved.pref ?? "light", saved.reducedMotion ?? false, saved.accent ?? "crimson");
}
