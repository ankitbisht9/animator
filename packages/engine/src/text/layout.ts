/**
 * Deterministic text layout from bundled font tables (ADR-004). No DOM, no
 * canvas measureText: widths come from glyph advances and kerning, so the
 * worker, the browser and headless Chromium all agree to the last pixel.
 *
 * Markup: `**strong**` marks emphasis (bold + accent color when rendered).
 */
import lexend400 from "./fonts/lexend-400.json";
import lexend600 from "./fonts/lexend-600.json";
import lexend700 from "./fonts/lexend-700.json";
import symbolsTable from "./fonts/symbols.json";

export type FontWeight = 400 | 600 | 700;
export const FONT_WEIGHTS: readonly FontWeight[] = [400, 600, 700];

export interface Glyph {
  a: number;
  d: string;
}

interface FontTable {
  unitsPerEm: number;
  ascender: number;
  descender: number;
  capHeight: number;
  xHeight: number;
  glyphs: Record<string, Glyph>;
  kern: Record<string, number>;
}

const FONTS: Record<FontWeight, FontTable> = {
  400: lexend400 as FontTable,
  600: lexend600 as FontTable,
  700: lexend700 as FontTable,
};
const SYMBOLS = (symbolsTable as { glyphs: Record<string, Glyph> }).glyphs;
const UPM = 1000;

export const FONT_METRICS = {
  unitsPerEm: UPM,
  ascender: FONTS[400].ascender,
  descender: FONTS[400].descender,
  capHeight: FONTS[400].capHeight,
  xHeight: FONTS[400].xHeight,
};

const REPLACEMENT = "?";

export function hasGlyph(ch: string): boolean {
  return ch === "\n" || Object.hasOwn(FONTS[400].glyphs, ch) || Object.hasOwn(SYMBOLS, ch);
}

export function glyph(ch: string, weight: FontWeight): Glyph {
  const table = FONTS[weight].glyphs;
  if (Object.hasOwn(table, ch)) return table[ch] as Glyph;
  if (Object.hasOwn(SYMBOLS, ch)) return SYMBOLS[ch] as Glyph;
  return table[REPLACEMENT] as Glyph;
}

/** Characters in `text` that no bundled font can draw. */
export function unsupportedChars(text: string): string[] {
  const out = new Set<string>();
  for (const ch of text.replace(/\*\*/g, "")) if (!hasGlyph(ch)) out.add(ch);
  return [...out];
}

export interface Run {
  text: string;
  emph: boolean;
}

/** Split `**emphasis**` markup into runs. Unbalanced markers are kept literally. */
export function parseRuns(text: string): Run[] {
  const parts = text.split("**");
  if (parts.length % 2 === 0) return [{ text, emph: false }];
  return parts.map((t, i) => ({ text: t, emph: i % 2 === 1 })).filter((r) => r.text.length > 0);
}

export const stripMarkup = (text: string): string =>
  parseRuns(text)
    .map((r) => r.text)
    .join("");

export interface PlacedGlyph {
  ch: string;
  /** x of the glyph origin, relative to the line start (px). */
  x: number;
  /** Advance width (px). */
  w: number;
  weight: FontWeight;
  emph: boolean;
  /** Index of the word this glyph belongs to (spaces belong to no word: -1). */
  word: number;
}

export interface TextLine {
  glyphs: PlacedGlyph[];
  width: number;
  /** x offset of the line inside the text box (alignment). */
  x: number;
  /** Baseline y relative to the top of the text box. */
  baseline: number;
}

export type TextAlign = "left" | "center" | "right";

export interface TextLayout {
  width: number;
  height: number;
  size: number;
  lineHeight: number;
  align: TextAlign;
  lines: TextLine[];
  /** Number of drawable (non-space) glyphs, for write-on reveals. */
  glyphCount: number;
  wordCount: number;
  /** Characters replaced by `?` because no font has them. */
  missing: string[];
}

export interface TextStyle {
  size: number;
  weight?: FontWeight;
  maxWidth?: number;
  lineHeight?: number;
  align?: TextAlign;
  letterSpacing?: number;
}

interface Token {
  kind: "word" | "space" | "newline";
  glyphs: { ch: string; weight: FontWeight; emph: boolean }[];
}

function tokenize(text: string, baseWeight: FontWeight): Token[] {
  const tokens: Token[] = [];
  let cur: Token | null = null;
  for (const run of parseRuns(text)) {
    const weight: FontWeight = run.emph ? 700 : baseWeight;
    for (const ch of run.text) {
      const kind: Token["kind"] = ch === "\n" ? "newline" : ch === " " ? "space" : "word";
      if (kind === "newline") {
        cur = null;
        tokens.push({ kind, glyphs: [] });
        continue;
      }
      if (!cur || cur.kind !== kind) {
        cur = { kind, glyphs: [] };
        tokens.push(cur);
      }
      cur.glyphs.push({ ch, weight, emph: run.emph });
    }
  }
  return tokens;
}

const cache = new Map<string, TextLayout>();
const CACHE_MAX = 2000;

export function layoutText(text: string, style: TextStyle): TextLayout {
  const key = `${text}\u0000${style.size}|${style.weight ?? 400}|${style.maxWidth ?? ""}|${style.lineHeight ?? ""}|${style.align ?? ""}|${style.letterSpacing ?? 0}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const result = computeLayout(text, style);
  if (cache.size >= CACHE_MAX) cache.clear();
  cache.set(key, result);
  return result;
}

function computeLayout(text: string, style: TextStyle): TextLayout {
  const size = style.size;
  const scale = size / UPM;
  const lineHeight = style.lineHeight ?? 1.3;
  const align = style.align ?? "left";
  const spacing = style.letterSpacing ?? 0;
  const maxWidth = style.maxWidth && style.maxWidth > 0 ? style.maxWidth : Number.POSITIVE_INFINITY;
  const tokens = tokenize(text, style.weight ?? 400);
  const missing = unsupportedChars(text);

  const measure = (glyphs: Token["glyphs"], prev: string | null) => {
    const out: { ch: string; x: number; w: number; weight: FontWeight; emph: boolean }[] = [];
    let x = 0;
    let last = prev;
    for (const g of glyphs) {
      const kern = last ? (FONTS[g.weight].kern[last + g.ch] ?? 0) * scale : 0;
      x += kern;
      const w = glyph(g.ch, g.weight).a * scale + spacing;
      out.push({ ch: g.ch, x, w, weight: g.weight, emph: g.emph });
      x += w;
      last = g.ch;
    }
    return { glyphs: out, width: x };
  };

  const lines: { glyphs: PlacedGlyph[]; width: number }[] = [];
  let line: PlacedGlyph[] = [];
  let lineWidth = 0;
  let pendingSpace: Token | null = null;
  let wordIndex = 0;

  const flush = () => {
    lines.push({ glyphs: line, width: lineWidth });
    line = [];
    lineWidth = 0;
    pendingSpace = null;
  };

  for (const tok of tokens) {
    if (tok.kind === "newline") {
      flush();
      continue;
    }
    if (tok.kind === "space") {
      if (line.length > 0) pendingSpace = tok;
      continue;
    }
    const prevCh = line.length > 0 ? (line[line.length - 1]?.ch ?? null) : null;
    const spaceM = pendingSpace ? measure(pendingSpace.glyphs, prevCh) : { glyphs: [], width: 0 };
    const wordM = measure(tok.glyphs, pendingSpace ? " " : prevCh);
    if (line.length > 0 && lineWidth + spaceM.width + wordM.width > maxWidth) {
      flush();
    }
    if (line.length > 0 && pendingSpace) {
      for (const g of spaceM.glyphs) line.push({ ...g, x: g.x + lineWidth, word: -1 });
      lineWidth += spaceM.width;
    }
    pendingSpace = null;
    const word = line.length === 0 ? measure(tok.glyphs, null) : wordM;
    if (word.width > maxWidth && line.length === 0) {
      // A single word wider than the box: break it by character.
      for (const g of word.glyphs) {
        if (line.length > 0 && lineWidth + g.w > maxWidth) flush();
        line.push({ ...g, x: lineWidth, word: wordIndex });
        lineWidth += g.w;
      }
    } else {
      for (const g of word.glyphs) line.push({ ...g, x: g.x + lineWidth, word: wordIndex });
      lineWidth += word.width;
    }
    wordIndex++;
  }
  if (line.length > 0 || lines.length === 0) flush();

  const lineBox = lineHeight * size;
  const capH = FONT_METRICS.capHeight * scale;
  const width = Math.max(0, ...lines.map((l) => l.width));
  const outLines: TextLine[] = lines.map((l, i) => ({
    glyphs: l.glyphs,
    width: l.width,
    x: align === "center" ? (width - l.width) / 2 : align === "right" ? width - l.width : 0,
    // Center cap height in the line box so boxes look optically balanced.
    baseline: i * lineBox + (lineBox + capH) / 2,
  }));
  let glyphCount = 0;
  for (const l of outLines) for (const g of l.glyphs) if (g.ch !== " ") glyphCount++;
  return {
    width,
    height: lines.length * lineBox,
    size,
    lineHeight,
    align,
    lines: outLines,
    glyphCount,
    wordCount: wordIndex,
    missing,
  };
}
