import { ICON_NAMES, parsePath } from "@animator/engine";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ICON_DRAWINGS, IconGlyph } from "../src/renderers/icons";
import { LUCIDE_ICONS } from "../src/renderers/icons-lucide";

describe("icons", () => {
  it("draws every icon name the engine accepts", () => {
    const missing = ICON_NAMES.filter((name) => !ICON_DRAWINGS.includes(name));
    expect(missing).toEqual([]);
  });

  it("has well-formed path data for every vendored icon", () => {
    for (const [name, d] of Object.entries(LUCIDE_ICONS)) {
      expect(() => parsePath(d), name).not.toThrow();
    }
  });

  it("renders a vendored icon rather than the fallback", () => {
    const hand = renderToStaticMarkup(
      <g>
        <IconGlyph name="hand" size={48} color="#000" />
      </g>,
    );
    const info = renderToStaticMarkup(
      <g>
        <IconGlyph name="info" size={48} color="#000" />
      </g>,
    );
    expect(hand).toContain(LUCIDE_ICONS.hand);
    expect(hand).not.toEqual(info);
  });
});
