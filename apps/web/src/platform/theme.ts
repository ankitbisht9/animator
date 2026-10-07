/** Light by default (the brand: white and red); dark or the system's choice when the learner picks it.
 * The choice is remembered in the browser too, so the right theme shows before the profile loads. */
import type { ThemePref } from "./api";

const KEY = "animator.theme";
let media: MediaQueryList | null = null;
let current: ThemePref = "light";

function resolve(pref: ThemePref): "light" | "dark" {
  if (pref !== "system") return pref;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(pref: ThemePref, reducedMotion = false): void {
  current = pref;
  const root = document.documentElement;
  root.dataset.theme = resolve(pref);
  root.dataset.motion = reducedMotion ? "reduced" : "full";
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ pref, reducedMotion }));
  } catch {
    /* private mode */
  }
  if (!media && window.matchMedia) {
    media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener?.("change", () => {
      if (current === "system") root.dataset.theme = resolve("system");
    });
  }
}

/** The last theme this browser used (before anyone is signed in). */
export function restoreTheme(): void {
  let saved: { pref?: ThemePref; reducedMotion?: boolean } = {};
  try {
    saved = JSON.parse(window.localStorage.getItem(KEY) ?? "{}") as typeof saved;
  } catch {
    saved = {};
  }
  applyTheme(saved.pref ?? "light", saved.reducedMotion ?? false);
}
