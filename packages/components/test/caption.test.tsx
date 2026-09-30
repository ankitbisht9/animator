import { getTheme } from "@animator/engine";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CaptionBar } from "../src";

const theme = getTheme("default");
const TEXT = "Look at the **tank** now.";
const WORDS: [number, number][] = [
  [0.1, 0.3],
  [0.3, 0.5],
  [0.5, 0.7],
  [0.7, 1.0],
  [1.0, 1.3],
];

/** x of the highlight pill behind the word being read, or null when there is none. */
function marker(svg: string): number | null {
  const m = svg.match(/<rect x="([\d.-]+)" y="[\d.-]+" width="[\d.-]+" height="[\d.-]+" rx="10"/);
  return m ? Number(m[1]) : null;
}

const render = (props: { progress: number; words?: [number, number][]; elapsed?: number }) =>
  renderToStaticMarkup(
    <g>
      <CaptionBar text={TEXT} theme={theme} {...props} />
    </g>,
  );

describe("caption words follow the voice", () => {
  it("lights up the word being said, moving word by word", () => {
    const first = marker(render({ progress: 0.1, words: WORDS, elapsed: 0.2 }));
    const tank = marker(render({ progress: 0.1, words: WORDS, elapsed: 0.8 }));
    const now = marker(render({ progress: 0.1, words: WORDS, elapsed: 1.1 }));
    expect(first).not.toBeNull();
    expect(tank as number).toBeGreaterThan(first as number);
    expect(now as number).toBeGreaterThan(tank as number);
  });

  it("shows no highlight before the first word or once the sentence is over", () => {
    expect(marker(render({ progress: 0.01, words: WORDS, elapsed: 0.05 }))).toBeNull();
    expect(marker(render({ progress: 0.5, words: WORDS, elapsed: 2 }))).toBeNull();
    // A pause inside the sentence keeps the last word said highlighted.
    expect(marker(render({ progress: 0.2, words: WORDS, elapsed: 1.35 }))).not.toBeNull();
  });

  it("falls back to its own timing when there is no voice or the words do not match", () => {
    const plain = render({ progress: 0.4 });
    expect(render({ progress: 0.4, words: WORDS.slice(1), elapsed: 0.8 })).toBe(plain);
    expect(render({ progress: 0.4, words: WORDS })).toBe(plain);
  });
});
