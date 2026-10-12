import {
  compileScene,
  EDUCATIONAL_CLEAN,
  EDUCATIONAL_CLEAN_DARK,
  evaluate,
  lift,
  resolveColor,
} from "@animator/engine";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SceneView } from "../src";

const ART = {
  w: 200,
  h: 100,
  shapes: [
    { d: "M0 0 L100 0 L100 100 L0 100 Z", fill: "red", stroke: "red.dark", strokeWidth: 3, part: "body" },
    { d: "M120 0 L200 0 L200 40 L120 40 Z", fill: "#3366cc", stroke: "none", part: "lid" },
  ],
};

function frameSvg(art: unknown, theme = "educational_clean", focus?: string) {
  const l = lift({
    dsl_version: "1.0",
    id: "s1",
    title: "Figure",
    duration: 3,
    theme,
    narration: { beats: [{ id: "b1", text: "A saved drawing." }] },
    objects: [{ id: "thing", type: "figure", params: { art, width: 400, focus }, place: { x: 800, y: 400 } }],
    timeline: [{ id: "a1", action: "create", target: "thing", start: { at: 0 } }],
  });
  if (!l.ok || !l.scene) throw new Error(JSON.stringify(l.issues));
  const c = compileScene(l.scene, { prev: null });
  if (!c.ok || !c.compiled) throw new Error(JSON.stringify(c.issues));
  return renderToStaticMarkup(<SceneView compiled={c.compiled} frame={evaluate(c.compiled, 2)} />);
}

describe("figure renderer", () => {
  it("draws every shape, scaled to the width, with theme colors resolved", () => {
    const svg = frameSvg(ART);
    expect(svg).toContain('transform="scale(2) translate(-100 -50)"');
    expect(svg).toContain('d="M0 0 L100 0 L100 100 L0 100 Z"');
    expect(svg).toContain(`fill="${resolveColor(EDUCATIONAL_CLEAN, "red")}"`);
    expect(svg).toContain(`stroke="${resolveColor(EDUCATIONAL_CLEAN, "red.dark")}"`);
    expect(svg).toContain('fill="#3366cc"');
    expect(svg).toContain('stroke="none"');
  });

  it("follows the theme: role colors change in the dark theme, hex colors stay", () => {
    const dark = frameSvg(ART, "educational_clean_dark");
    expect(resolveColor(EDUCATIONAL_CLEAN_DARK, "red")).not.toEqual(resolveColor(EDUCATIONAL_CLEAN, "red"));
    expect(dark).toContain(`fill="${resolveColor(EDUCATIONAL_CLEAN_DARK, "red")}"`);
    expect(dark).toContain('fill="#3366cc"');
    // The light theme's shapes are not reused for the dark one.
    expect(frameSvg(ART)).toContain(`fill="${resolveColor(EDUCATIONAL_CLEAN, "red")}"`);
  });

  it("rings a focused part in the highlight color and dims the rest", () => {
    const svg = frameSvg(ART, "educational_clean", "lid");
    expect(svg).toContain('<g opacity="0.55">');
    expect(svg).toContain(`stroke="${EDUCATIONAL_CLEAN.colors.highlight}"`);
    expect(frameSvg(ART)).not.toContain('<g opacity="0.55">');
    expect(frameSvg(ART, "educational_clean", "")).not.toContain('<g opacity="0.55">');
  });
});
