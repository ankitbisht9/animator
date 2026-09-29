import { describe, expect, it } from "vitest";
import { layoutText, parsePath, parseRuns, stripMarkup, unsupportedChars } from "../src";
import lexend400 from "../src/text/fonts/lexend-400.json";
import lexend600 from "../src/text/fonts/lexend-600.json";
import lexend700 from "../src/text/fonts/lexend-700.json";
import symbols from "../src/text/fonts/symbols.json";

describe("text layout", () => {
  it("is deterministic and proportional to size", () => {
    const a = layoutText("Buoyant force", { size: 30 });
    const b = layoutText("Buoyant force", { size: 60 });
    expect(a.width).toBeGreaterThan(100);
    expect(b.width).toBeCloseTo(a.width * 2, 6);
    expect(layoutText("Buoyant force", { size: 30 })).toEqual(a);
  });

  it("wraps at word boundaries within maxWidth", () => {
    const l = layoutText("The block pushes water aside and the level rises", { size: 30, maxWidth: 300 });
    expect(l.lines.length).toBeGreaterThan(1);
    for (const line of l.lines) expect(line.width).toBeLessThanOrEqual(300 + 1e-6);
  });

  it("breaks words longer than the box", () => {
    const l = layoutText("Supercalifragilisticexpialidocious", { size: 40, maxWidth: 200 });
    expect(l.lines.length).toBeGreaterThan(1);
  });

  it("parses **emphasis** into bold runs", () => {
    expect(parseRuns("a **b** c")).toEqual([
      { text: "a ", emph: false, script: "base" },
      { text: "b", emph: true, script: "base" },
      { text: " c", emph: false, script: "base" },
    ]);
    expect(stripMarkup("a **b** c")).toBe("a b c");
    const l = layoutText("a **b**", { size: 30 });
    expect(l.lines[0]?.glyphs.find((g) => g.ch === "b")?.weight).toBe(700);
  });

  it("lays out subscripts and superscripts smaller and shifted", () => {
    expect(parseRuns("F_{b} = x^{2}").map((r) => [r.text, r.script])).toEqual([
      ["F", "base"],
      ["b", "sub"],
      [" = x", "base"],
      ["2", "sup"],
    ]);
    const l = layoutText("F_{b}", { size: 40 });
    const [f, b] = l.lines[0]?.glyphs ?? [];
    expect(b?.size).toBeCloseTo(28);
    expect(b?.dy).toBeGreaterThan(0);
    expect(f?.dy).toBe(0);
    expect(stripMarkup("V_{sub} in cm^{3}")).toBe("Vsub in cm3");
  });

  it("draws Unicode subscripts and superscripts (CO₂, m²) as script runs, not missing glyphs", () => {
    expect(parseRuns("CO₂ + H₂O, 10 m²").map((r) => [r.text, r.script])).toEqual([
      ["CO", "base"],
      ["2", "sub"],
      [" + H", "base"],
      ["2", "sub"],
      ["O, 10 m", "base"],
      ["2", "sup"],
    ]);
    expect(unsupportedChars("C₆H₁₂O₆ and x⁻¹")).toEqual([]);
  });

  it("supports Greek letters and arrows through the symbol fallback", () => {
    expect(unsupportedChars("ρ = 1000 kg/m³ → Δh ≈ 2 cm")).toEqual([]);
    expect(unsupportedChars("日本")).toEqual(["日", "本"]);
  });

  it("counts glyphs and words for write-on reveals", () => {
    const l = layoutText("one two  three", { size: 20 });
    expect(l.wordCount).toBe(3);
    expect(l.glyphCount).toBe(11);
  });
});

describe("font tables", () => {
  // Every glyph must be valid path data: the browser silently stops drawing a path at
  // the first error (opentype.js once glued "393" and "-0" into "3930", clipping "e" and "d").
  const tables = { lexend400, lexend600, lexend700, symbols } as Record<
    string,
    { glyphs: Record<string, { d: string }> }
  >;
  for (const [name, table] of Object.entries(tables)) {
    it(`${name}: every glyph outline parses strictly`, () => {
      const bad = Object.entries(table.glyphs)
        .filter(([, g]) => g.d !== "")
        .filter(([, g]) => {
          try {
            parsePath(g.d);
            return false;
          } catch {
            return true;
          }
        })
        .map(([ch]) => ch);
      expect(bad).toEqual([]);
    });
  }
});
