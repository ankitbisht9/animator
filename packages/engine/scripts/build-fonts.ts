/**
 * Extracts glyph outlines, advances and kerning from the bundled Lexend WOFF files
 * into compact JSON tables used for deterministic text layout and rendering
 * (ADR-004). Symbols Lexend lacks (Greek, arrows, relations) are taken from
 * MathJax's TeX fonts. Run with `pnpm --filter @animator/engine fonts`; output is committed.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import opentype from "opentype.js";
import { parsePath, serializePath, transformPath } from "../src/geom/path";
import { scaling } from "../src/math/matrix";
import { typesetTex } from "../src/mathjax/typeset";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const fontDir = dirname(require.resolve("@fontsource/lexend/files/lexend-latin-400-normal.woff"));
const outDir = resolve(here, "../src/text/fonts");

const WEIGHTS = [400, 600, 700] as const;

// Printable ASCII plus the typographic characters explainers need.
const EXTRA = "°×÷·−–—‘’“”…•±²³¹½¼¾µé€";
const CANDIDATES = [...new Set([...Array.from({ length: 95 }, (_, i) => String.fromCharCode(32 + i)), ...EXTRA])];

interface GlyphOut {
  a: number;
  d: string;
}

for (const weight of WEIGHTS) {
  const buf = readFileSync(resolve(fontDir, `lexend-latin-${weight}-normal.woff`));
  const font = opentype.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const upm = font.unitsPerEm;
  const glyphs: Record<string, GlyphOut> = {};
  const missing: string[] = [];
  for (const ch of CANDIDATES) {
    const g = font.charToGlyph(ch);
    if (!g || g.index === 0) {
      missing.push(ch);
      continue;
    }
    // getPath flips y so outlines are y-down with the baseline at y = 0.
    const d = g.getPath(0, 0, upm).toPathData(0);
    glyphs[ch] = { a: Math.round(g.advanceWidth ?? 0), d };
  }
  const kern: Record<string, number> = {};
  const chars = Object.keys(glyphs).filter((c) => c !== " ");
  for (const l of chars) {
    for (const r of chars) {
      const v = font.getKerningValue(font.charToGlyph(l), font.charToGlyph(r));
      if (v) kern[l + r] = v;
    }
  }
  const os2 = font.tables.os2 as { sCapHeight?: number; sxHeight?: number } | undefined;
  const table = {
    family: "Lexend",
    weight,
    license: "SIL Open Font License 1.1 (via @fontsource/lexend)",
    unitsPerEm: upm,
    ascender: font.ascender,
    descender: font.descender,
    capHeight: os2?.sCapHeight ?? Math.round(upm * 0.7),
    xHeight: os2?.sxHeight ?? Math.round(upm * 0.5),
    glyphs,
    kern,
  };
  writeFileSync(resolve(outDir, `lexend-${weight}.json`), `${JSON.stringify(table)}\n`);
  process.stdout.write(
    `lexend ${weight}: ${Object.keys(glyphs).length} glyphs, ${Object.keys(kern).length} kern pairs, missing: ${missing.join(" ") || "none"}\n`,
  );
}

// Scaled up slightly so the TeX glyphs' x-height sits closer to Lexend's.
const SYMBOLS: Record<string, string> = {
  "↑": String.raw`\uparrow`,
  "↓": String.raw`\downarrow`,
  "←": String.raw`\leftarrow`,
  "→": String.raw`\rightarrow`,
  "≈": String.raw`\approx`,
  "≠": String.raw`\neq`,
  "≤": String.raw`\leq`,
  "≥": String.raw`\geq`,
  "√": String.raw`\surd`,
  "∞": String.raw`\infty`,
  π: String.raw`\pi`,
  Δ: String.raw`\Delta`,
  θ: String.raw`\theta`,
  λ: String.raw`\lambda`,
  ρ: String.raw`\rho`,
  σ: String.raw`\sigma`,
  ω: String.raw`\omega`,
  Ω: String.raw`\Omega`,
  α: String.raw`\alpha`,
  β: String.raw`\beta`,
  γ: String.raw`\gamma`,
};
const SYMBOL_SCALE = 1.18;
const symbols: Record<string, GlyphOut> = {};
for (const [ch, tex] of Object.entries(SYMBOLS)) {
  const layout = typesetTex(tex);
  const segs = layout.parts.flatMap((p) => parsePath(p.d));
  symbols[ch] = {
    a: Math.round(layout.width * SYMBOL_SCALE),
    d: serializePath(transformPath(segs, scaling(SYMBOL_SCALE))),
  };
}
writeFileSync(
  resolve(outDir, "symbols.json"),
  `${JSON.stringify({ source: "MathJax TeX fonts (Apache-2.0), scaled", unitsPerEm: 1000, glyphs: symbols })}\n`,
);
process.stdout.write(`symbols: ${Object.keys(symbols).length} glyphs\n`);
