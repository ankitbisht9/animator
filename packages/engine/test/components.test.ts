import { describe, expect, it } from "vitest";
import {
  compileScene,
  containerLayout,
  EDUCATIONAL_CLEAN,
  evaluateObjects,
  flowTable,
  gaugeLayout,
  getSpec,
  pipeLayout,
  readoutLayout,
  typesetEquation,
  vectorLayout,
} from "../src";
import { compiled, errorCodes, lifted } from "./helpers";

const theme = EDUCATIONAL_CLEAN;

describe("component geometry", () => {
  it("vector arrows are proportional to magnitude (within limits)", () => {
    const base = { from: { x: 0, y: 0 }, direction: "down", scale: 10, minLength: 20, maxLength: 400 };
    const a = vectorLayout({ ...base, magnitude: 5 }, theme);
    const b = vectorLayout({ ...base, magnitude: 10 }, theme);
    expect(a.length).toBe(50);
    expect(b.length).toBe(100);
    expect(b.tip).toEqual({ x: 0, y: 100 });
    expect(vectorLayout({ ...base, magnitude: 1000 }, theme).length).toBe(400);
    const neg = vectorLayout({ ...base, magnitude: -5 }, theme);
    expect(neg.tip.y).toBe(-50);
  });

  it("readouts honour minWidth so stacked readouts line up", () => {
    const base = { label: "Weight lost", value: 9.8, format: "0.0", unit: "N" };
    const natural = readoutLayout(base, theme).width;
    expect(readoutLayout({ ...base, minWidth: natural + 100 }, theme).width).toBe(natural + 100);
    expect(readoutLayout({ ...base, minWidth: 10 }, theme).width).toBe(natural);
  });

  it("a small gauge moves its reading beside the dial instead of spilling over it", () => {
    const big = gaugeLayout({ value: 145, radius: 100, format: "0.0" }, theme);
    const small = gaugeLayout({ value: 145, radius: 40, format: "0.0" }, theme);
    expect(big.valueInside).toBe(true);
    expect(small.valueInside).toBe(false);
    expect(small.valueAt.x).toBeGreaterThan(small.r);
    const spec = getSpec("pressure_gauge");
    if (!spec) throw new Error("pressure_gauge spec missing");
    const box = spec.geometry({ value: 145, radius: 40, format: "0.0" }, { theme, prep: null }).bbox;
    expect(box.x + box.w).toBeGreaterThanOrEqual(small.valueAt.x + small.value.width);
  });

  it("container liquid surface follows the level", () => {
    const half = containerLayout({ width: 400, height: 300, wall: 10, level: 0.5 });
    const full = containerLayout({ width: 400, height: 300, wall: 10, level: 1 });
    const innerH = half.inner.floor - half.inner.top;
    expect(half.surfaceY).toBeCloseTo(half.inner.floor - innerH / 2);
    expect(full.surfaceY).toBeCloseTo(half.inner.top);
    expect(containerLayout({ shape: "overflow_can", width: 300, height: 300 }).spout).not.toBeNull();
  });

  it("equation parts become term anchors", () => {
    const parts = [
      { id: "fb", tex: "F_b" },
      { id: "eq", tex: "=" },
      { id: "rho", tex: "\\rho" },
    ];
    const tex = typesetEquation(parts);
    expect(tex.parts.map((p) => p.id).filter(Boolean)).toEqual(["fb", "eq", "rho"]);
    const spec = getSpec("equation");
    const geo = spec?.geometry({ parts, size: 50 }, { theme, prep: tex });
    expect(Object.keys(geo?.anchors ?? {})).toEqual(["term_fb", "term_eq", "term_rho"]);
    const fb = geo?.anchors?.term_fb as { x: number };
    const rho = geo?.anchors?.term_rho as { x: number };
    expect(fb.x).toBeLessThan(rho.x);
  });

  it("rejects TeX commands outside the allowlist", () => {
    expect(
      errorCodes({
        objects: [
          {
            id: "e",
            type: "equation",
            params: { parts: [{ id: "a", tex: "\\href{x}{y}" }] },
            place: { slot: "stage.center" },
          },
        ],
      }),
    ).toContain("TEX_UNSUPPORTED");
    expect(
      errorCodes({
        objects: [
          {
            id: "e",
            type: "equation",
            params: { parts: [{ id: "a", tex: "\\def\\x{1}" }] },
            place: { slot: "stage.center" },
          },
        ],
      }),
    ).toContain("TEX_UNSUPPORTED");
  });

  it("readout digits are tabular: width does not change with the digits", () => {
    const a = readoutLayout({ value: 11.1, format: "0.0", unit: "N" }, theme);
    const b = readoutLayout({ value: 88.8, format: "0.0", unit: "N" }, theme);
    expect(a.valueWidth).toBeCloseTo(b.valueWidth, 6);
  });

  it("flow particles obey continuity: speed x width^2 is constant", () => {
    const l = pipeLayout({
      sections: [
        { id: "a", x: 0, width: 120 },
        { id: "b", x: 300, width: 60 },
        { id: "c", x: 600, width: 120 },
      ],
    });
    const table = flowTable(l, 100);
    for (const x of [-300, -120, 0, 150]) expect(table.speedAt(x) * l.widthAt(x) ** 2).toBeCloseTo(100 * 120 ** 2, 3);
    expect(table.speedAt(0)).toBeCloseTo(400, 3);
  });
});

describe("layout and camera", () => {
  it("anchors follow their objects: an arrow tail stays on a moving block", () => {
    const cs = compiled({
      objects: [
        { id: "blk", type: "block", place: { x: 400, y: 400 } },
        { id: "f", type: "vector_arrow", params: { from: "blk.center", direction: "down", magnitude: 5, scale: 10 } },
      ],
      timeline: [{ id: "m", action: "move", target: "blk", by: { x: 300, y: 0 }, start: { at: 1 }, duration: 2 }],
    });
    for (const t of [0, 2, 4]) {
      const ctx = evaluateObjects(cs, t);
      const blk = ctx.anchorOf("blk", "center");
      const tail = ctx.anchorOf("f", "tail");
      expect(tail).toEqual(blk);
    }
  });

  it("relative placement puts objects on the requested side", () => {
    const cs = compiled({
      objects: [
        { id: "tank", type: "container", place: { slot: "stage.center" } },
        { id: "blk", type: "block", place: { relativeTo: "tank", side: "above", gap: 50 } },
      ],
    });
    const ctx = evaluateObjects(cs, 0);
    const tank = ctx.bboxOf("tank");
    const blk = ctx.bboxOf("blk");
    expect(blk && tank && blk.y + blk.h).toBeCloseTo((tank?.y ?? 0) - 50, 4);
  });

  it("camera focus frames the chosen objects", () => {
    const cs = compiled({
      objects: [{ id: "blk", type: "block", place: { slot: "stage.left" } }],
      timeline: [{ id: "c", action: "camera_move", focus: ["blk"], padding: 100, start: { at: 1 }, duration: 1 }],
    });
    const ctx = evaluateObjects(cs, 3);
    const b = ctx.bboxOf("blk");
    const cam = cs.camera.ch;
    const cx = cam.cx.segs[0]?.k === "tween" ? cam.cx.segs[0].to : 0;
    expect(cx).toBeCloseTo((b?.x ?? 0) + (b?.w ?? 0) / 2, 3);
    expect(cam.zoom.segs[0]?.k === "tween" && cam.zoom.segs[0].to).toBeGreaterThan(1);
  });
});

describe("layout validation", () => {
  const codes = (extra: Record<string, unknown>) =>
    compileScene(lifted(extra)).issues.map((i) => `${i.severity}:${i.code}`);

  it("flags content outside the frame", () => {
    expect(codes({ objects: [{ id: "b", type: "block", place: { x: 1580, y: 450 } }] })).toContain(
      "error:OUT_OF_BOUNDS",
    );
  });

  it("says how to bring an off-frame object back, and following that clears the error", () => {
    const out = compileScene(lifted({ objects: [{ id: "b", type: "block", place: { x: 1580, y: 450 } }] }));
    const fix = out.issues.find((i) => i.code === "OUT_OF_BOUNDS")?.fix;
    expect(fix).toBeDefined();
    if (!fix) return;
    expect(fix.dx).toBeLessThan(0);
    expect(fix.dy).toBe(0);
    expect(fix.scale).toBeUndefined();
    const moved = { objects: [{ id: "b", type: "block", place: { x: 1580 + fix.dx, y: 450 } }] };
    expect(codes(moved)).not.toContain("error:OUT_OF_BOUNDS");
  });

  it("does not let text run even a little off the frame", () => {
    const text = (x: number) => ({
      objects: [{ id: "eq", type: "text", params: { text: "carbon dioxide + water -> sugar" }, place: { x, y: 450 } }],
    });
    // A diagram may overhang a little (under 10%); a line of text may not.
    const overhang = compileScene(lifted(text(1400))).issues.find((i) => i.code === "OUT_OF_BOUNDS");
    expect(overhang?.message).toMatch(/is [1-9]% outside/); // a few percent: once only a warning
    expect(codes(text(1000))).not.toContain("error:OUT_OF_BOUNDS");
  });

  it("asks to shrink an object larger than the frame", () => {
    const wide = {
      objects: [{ id: "b", type: "block", params: { width: 800, height: 100 }, scale: 3, place: { x: 800, y: 450 } }],
    };
    const fix = compileScene(lifted(wide)).issues.find((i) => i.code === "OUT_OF_BOUNDS")?.fix;
    expect(fix?.scale).toBeLessThan(1);
  });

  it("flags overlapping labels", () => {
    expect(
      codes({
        objects: [
          { id: "a", type: "text", params: { text: "Buoyant force" }, place: { x: 800, y: 400 } },
          { id: "b", type: "text", params: { text: "Weight of water" }, place: { x: 820, y: 410 } },
        ],
      }),
    ).toContain("error:LABEL_OVERLAP");
  });

  it("says how to move an overlapping label clear, and following that clears the overlap", () => {
    const labels = (dx: number, dy: number) => ({
      objects: [
        { id: "a", type: "text", params: { text: "Buoyant force" }, place: { x: 800 + dx, y: 400 + dy } },
        { id: "b", type: "text", params: { text: "Weight of water" }, place: { x: 820, y: 410 } },
      ],
    });
    const fix = compileScene(lifted(labels(0, 0))).issues.find((i) => i.code === "LABEL_OVERLAP")?.fix;
    expect(fix).toBeDefined();
    if (!fix) return;
    expect(Math.abs(fix.dx) + Math.abs(fix.dy)).toBeGreaterThan(0);
    expect(codes(labels(fix.dx, fix.dy))).not.toContain("error:LABEL_OVERLAP");
  });

  describe("tidy layout hints", () => {
    const block = (id: string, x: number, y = 430) => ({
      id,
      type: "block",
      params: { width: 120, height: 100 },
      place: { x, y },
    });
    const arrow = (id: string, from: string, to: string, curve = 0) => ({
      id,
      type: "arrow",
      params: { from, to, curve },
    });
    const issues = (objects: unknown[]) => compileScene(lifted({ objects })).issues;
    const find = (objects: unknown[], code: string) => issues(objects).find((i) => i.code === code);

    it("reroutes an arrow that runs through another object, and the new route clears it", () => {
      const scene = [block("a", 300), block("b", 800), block("c", 1300), arrow("go", "a.right", "c.left")];
      const through = find(scene, "CONNECTOR_CROSSES_OBJECT");
      expect(through?.message).toContain("'go' runs through 'b'");
      const fix = through?.fix?.params ?? {};
      expect(fix.curve).toBeTypeOf("number"); // bent (and maybe joined at other anchors: over the block)
      const bent = [
        ...scene.slice(0, 3),
        { ...arrow("go", "a.right", "c.left"), params: { from: "a.right", to: "c.left", ...fix } },
      ];
      expect(issues(bent).map((i) => i.code)).not.toContain("CONNECTOR_CROSSES_OBJECT");
    });

    it("reroutes an arc between words in a row over or under the words between them", () => {
      const words = ["The", "bird", "sat", "by", "the", "bank"].map((text, i) => ({
        id: `w${i}`,
        type: "label",
        params: { text, size: "heading" },
        place: { x: 360 + i * 180, y: 420 },
      }));
      const scene = [...words, arrow("attn", "w5.left", "w1.right", 60)];
      const across = find(scene, "CONNECTOR_CROSSES_LABEL");
      const params = across?.fix?.params ?? {};
      expect(params.from).toMatch(/^w5\.(top|bottom)$/);
      expect(params.to).toMatch(/^w1\.(top|bottom)$/);
      const rerouted = [
        ...words,
        { id: "attn", type: "arrow", params: { from: params.from, to: params.to, curve: params.curve } },
      ];
      expect(issues(rerouted).map((i) => i.code)).not.toContain("CONNECTOR_CROSSES_LABEL");
    });

    it("does not flag an arrow that ends at an object", () => {
      expect(
        find([block("a", 300), block("b", 800), arrow("go", "a.right", "b.left")], "CONNECTOR_CROSSES_OBJECT"),
      ).toBe(undefined);
    });

    it("flags arrows that cross each other, but not arrows leaving the same point", () => {
      const cross = [
        block("a", 400, 250),
        block("b", 1200, 650),
        block("c", 400, 650),
        block("d", 1200, 250),
        arrow("ab", "a.right", "b.left"),
        arrow("cd", "c.right", "d.left"),
      ];
      expect(find(cross, "CONNECTORS_CROSS")?.message).toContain("'ab' crosses 'cd'"); // an X cannot be bent apart: no hint
      const fan = [block("a", 400), block("b", 1200, 250), block("c", 1200, 650)];
      expect(find([...fan, arrow("x", "a.right", "b.left"), arrow("y", "a.right", "c.left")], "CONNECTORS_CROSS")).toBe(
        undefined,
      );
    });

    it("moves a label straddling an object's edge, not one written inside it", () => {
      const label = (x: number, y: number) => ({
        id: "l",
        type: "label",
        params: { text: "net force" },
        place: { x, y },
      });
      const edge = find([block("b", 800, 450), label(840, 430)], "LABEL_OCCLUDES");
      expect(edge?.fix).toBeDefined();
      const inside = find(
        [{ ...block("b", 800, 450), params: { width: 400, height: 200 } }, label(800, 450)],
        "LABEL_OCCLUDES",
      );
      expect(inside?.fix).toBeUndefined();
    });

    it("lifts content hidden behind the caption bar", () => {
      const low = find([{ ...block("b", 800, 800) }], "UNDER_CAPTION");
      expect(low?.fix?.dy).toBeLessThan(0);
    });
  });

  it("flags text that is too small at 1080p", () => {
    expect(
      codes({
        objects: [{ id: "a", type: "text", params: { text: "tiny", size: 10 }, place: { slot: "stage.center" } }],
      }),
    ).toContain("error:TEXT_TOO_SMALL");
  });

  it("flags low-contrast bare text", () => {
    expect(
      codes({
        objects: [
          {
            id: "a",
            type: "text",
            params: { text: "faint" },
            place: { slot: "stage.center" },
            style: { text: "grid" },
          },
        ],
      }),
    ).toContain("warning:LOW_CONTRAST");
  });

  it("flags clutter", () => {
    const objects = Array.from({ length: 14 }, (_, i) => ({
      id: `c${i}`,
      type: "circle",
      params: { radius: 20 },
      place: { x: 150 + i * 90, y: 400 },
    }));
    expect(codes({ objects })).toContain("warning:CLUTTER");
  });

  it("flags stage content covering the scene title, but not content near it", () => {
    const title = {
      id: "title",
      type: "text",
      params: { text: "Speed and pressure trade off", size: "heading", weight: "bold" },
      place: { slot: "title_bar" },
      layer: "overlay",
    };
    const gauge = (y: number) => ({
      id: "g",
      type: "pressure_gauge",
      params: { label: "Throat" },
      place: { x: 800, y },
    });
    expect(codes({ objects: [title, gauge(110)] })).toContain("error:TITLE_COVERED");
    expect(codes({ objects: [title, gauge(330)] })).not.toContain("error:TITLE_COVERED");
    // The issue says how far down clears the title, and moving by that much does.
    const fix = compileScene(lifted({ objects: [title, gauge(110)] })).issues.find(
      (i) => i.code === "TITLE_COVERED",
    )?.fix;
    expect(fix?.dx).toBe(0);
    expect(fix?.dy).toBeGreaterThan(0);
    expect(codes({ objects: [title, gauge(110 + (fix?.dy ?? 0))] })).not.toContain("error:TITLE_COVERED");
  });

  it("flags a connector running through a label, but not one that ends at its edge", () => {
    const tag = { id: "tag", type: "label", params: { text: "Weight of spilled water" }, place: { x: 800, y: 400 } };
    const through = { id: "ar", type: "arrow", params: { from: { x: 600, y: 300 }, to: { x: 1000, y: 500 } } };
    const toEdge = { id: "ar", type: "arrow", params: { from: { x: 400, y: 400 }, to: "tag.left" } };
    expect(codes({ objects: [tag, through] })).toContain("warning:CONNECTOR_CROSSES_LABEL");
    expect(codes({ objects: [tag, toEdge] })).not.toContain("warning:CONNECTOR_CROSSES_LABEL");
  });

  it("does not check objects while they are still entering", () => {
    expect(
      codes({
        duration: 3,
        objects: [{ id: "a", type: "text", params: { text: "hello" }, place: { slot: "stage.center" } }],
        timeline: [{ action: "create", target: "a", style: "zoom", start: { at: 0 }, duration: 1 }],
      }).filter((c) => c.startsWith("error")),
    ).toEqual([]);
  });
});
