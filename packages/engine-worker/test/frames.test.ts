import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { beforeAll, describe, expect, it } from "vitest";
import { compiledAt, loadResvg, renderFrames, sceneSvg } from "../src/frames";

const scene = (id: string, objects: unknown[], timeline: unknown[]) => ({
  dsl_version: "1.0",
  id,
  title: "Frames",
  duration: 4,
  narration: { beats: [{ id: "b", text: "A red ball." }] },
  objects,
  timeline,
});

const ball = {
  id: "ball",
  type: "circle",
  params: { radius: 120 },
  style: { fill: "red" },
  place: { slot: "stage.center" },
};
const first = scene("s1", [ball], [{ id: "in", action: "create", target: "ball" }]);
// The second scene only moves the ball the first one made: it needs the first's end state.
const second = scene("s2", [], [{ id: "go", action: "move", target: "ball", by: { x: 300, y: 0 } }]);

/** Width, height and the RGB of one pixel of a PNG. */
async function pixel(base64: string, x: number, y: number): Promise<{ w: number; h: number; rgb: number[] }> {
  const { Resvg } = await import("@resvg/resvg-wasm");
  expect(Resvg).toBeDefined();
  const png = Buffer.from(base64, "base64");
  expect(png.subarray(1, 4).toString("ascii")).toBe("PNG");
  const w = png.readUInt32BE(16);
  const h = png.readUInt32BE(20);
  // Decode through an SVG that embeds the PNG and is drawn 1:1 (resvg decodes PNGs).
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><image href="data:image/png;base64,${base64}" width="${w}" height="${h}"/></svg>`;
  const raw = new Resvg(svg).render();
  const i = (y * raw.width + x) * 4;
  return { w, h, rgb: [...raw.pixels.subarray(i, i + 3)] };
}

describe("keyframes without a browser (ADR-068)", () => {
  beforeAll(async () => {
    const wasm = readFileSync(createRequire(import.meta.url).resolve("@resvg/resvg-wasm/index_bg.wasm"));
    await loadResvg(wasm);
  });

  it("draws the scene the player draws, as a self-contained SVG", () => {
    const svg = sceneSvg(compiledAt([first], 0), 2, "classic");
    expect(svg.startsWith("<svg")).toBe(true);
    expect(svg).toContain('viewBox="0 0 1600 900"');
    expect(svg).not.toContain("<text"); // text is glyph outlines: no fonts needed
  });

  it("renders PNG keyframes at the asked width, in the lesson's finish", async () => {
    const [classic] = await renderFrames({ scenes: [first], index: 0, times: [3], width: 640 });
    const [rich] = await renderFrames({ scenes: [first], index: 0, times: [3], finish: "rich", width: 640 });
    const c = await pixel(classic as string, 320, 180);
    expect([c.w, c.h]).toEqual([640, 360]);
    const [r, g, b] = c.rgb as [number, number, number];
    expect(r).toBeGreaterThan(150);
    expect(g).toBeLessThan(120);
    expect(b).toBeLessThan(120); // the red ball in the middle of the stage
    expect(rich).not.toEqual(classic); // shaded, not flat
  });

  it("starts a scene from the scenes before it, as the player does", async () => {
    const [moved] = await renderFrames({ scenes: [first, second], index: 1, times: [10], width: 640 });
    const centre = await pixel(moved as string, 320, 180);
    const right = await pixel(moved as string, 320 + 120, 180); // 300 stage px to the right
    expect(centre.rgb[0]).toBeGreaterThan(200);
    expect(centre.rgb[1]).toBeGreaterThan(200); // the ball has left the centre
    expect(right.rgb[1]).toBeLessThan(120); // and is on the right
  });

  it("says why a scene cannot be drawn", async () => {
    await expect(renderFrames({ scenes: [{ nonsense: true }], index: 0, times: [1] })).rejects.toThrow(
      /not a valid scene/,
    );
    await expect(renderFrames({ scenes: [first], index: 3, times: [1] })).rejects.toThrow();
  });
});
