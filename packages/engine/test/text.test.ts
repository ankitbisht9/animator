import { describe, expect, it } from "vitest";
import { layoutText, parseRuns, stripMarkup, unsupportedChars } from "../src";

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
      { text: "a ", emph: false },
      { text: "b", emph: true },
      { text: " c", emph: false },
    ]);
    expect(stripMarkup("a **b** c")).toBe("a b c");
    const l = layoutText("a **b**", { size: 30 });
    expect(l.lines[0]?.glyphs.find((g) => g.ch === "b")?.weight).toBe(700);
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
