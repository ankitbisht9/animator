import fc from "fast-check";
import { describe, expect, it } from "vitest";
import {
  contrastRatio,
  EASING_NAMES,
  ease,
  evalExpr,
  exprRefs,
  formatNumber,
  mixHex,
  parseExpr,
  parseHex,
  parsePath,
  pathBounds,
  rand01,
  toHex,
} from "../src";

describe("easing", () => {
  it("hits exact endpoints for every named easing", () => {
    for (const name of EASING_NAMES) {
      expect(ease(name, 0)).toBe(0);
      expect(ease(name, 1)).toBe(1);
    }
  });

  it("clamps input outside [0, 1]", () => {
    expect(ease("linear", -2)).toBe(0);
    expect(ease("linear", 3)).toBe(1);
  });

  it("monotonic easings never decrease", () => {
    const monotonic = [
      "linear",
      "ease_in",
      "ease_out",
      "ease_in_out",
      "ease_in_quad",
      "ease_out_quad",
      "smooth",
      "ease_in_out_sine",
    ] as const;
    fc.assert(
      fc.property(
        fc.constantFrom(...monotonic),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        (name, a, b) => {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          return ease(name, lo) <= ease(name, hi) + 1e-12;
        },
      ),
    );
  });

  it("cubic-bezier matches CSS ease (0.25, 0.1, 0.25, 1) at known points", () => {
    const e = { cubic_bezier: [0.25, 0.1, 0.25, 1] };
    expect(ease(e, 0.5)).toBeCloseTo(0.8024, 3);
    expect(ease({ cubic_bezier: [0, 0, 1, 1] }, 0.3)).toBeCloseTo(0.3, 5);
  });

  it("overshooting easings overshoot then land", () => {
    expect(Math.max(...[0.6, 0.7, 0.8].map((u) => ease("back_out", u)))).toBeGreaterThan(1);
    expect(Math.max(...Array.from({ length: 50 }, (_, i) => ease("spring", i / 50)))).toBeGreaterThan(1);
  });
});

describe("color", () => {
  it("round-trips hex", () => {
    expect(toHex(parseHex("#1c2541")!)).toBe("#1c2541");
    expect(toHex(parseHex("#abc")!)).toBe("#aabbcc");
    expect(parseHex("#12345")).toBeNull();
  });

  it("mixes in OKLab with exact endpoints", () => {
    expect(mixHex("#ff0000", "#0000ff", 0)).toBe("#ff0000");
    expect(mixHex("#ff0000", "#0000ff", 1)).toBe("#0000ff");
    const mid = mixHex("#000000", "#ffffff", 0.5);
    expect(parseHex(mid)!.r).toBeGreaterThan(0.3);
  });

  it("computes WCAG contrast", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 1);
    expect(contrastRatio("#777777", "#777777")).toBeCloseTo(1, 5);
  });
});

describe("seeded randomness", () => {
  it("is a pure function of its keys", () => {
    fc.assert(
      fc.property(fc.integer(), fc.integer(), (s, k) => {
        const first = rand01(s, k);
        const second = rand01(s, k);
        return first === second && first >= 0 && first < 1;
      }),
    );
    expect(rand01(1, 2)).not.toBe(rand01(1, 3));
  });
});

describe("expressions", () => {
  const env = (name: string) => ({ a: 2, b: 3, "buoy.weight": 4.9 })[name];

  it("respects precedence and right-associative powers", () => {
    expect(evalExpr(parseExpr("1 + 2 * 3"), env)).toBe(7);
    expect(evalExpr(parseExpr("2 ^ 3 ^ 2"), env)).toBe(512);
    expect(evalExpr(parseExpr("-a ^ 2"), env)).toBe(-4);
    expect(evalExpr(parseExpr("(a + b) * 2"), env)).toBe(10);
    expect(evalExpr(parseExpr("sqrt(16) + max(1, b, 2)"), env)).toBe(7);
    expect(evalExpr(parseExpr("buoy.weight * 2"), env)).toBeCloseTo(9.8);
  });

  it("lists referenced identifiers", () => {
    expect([...exprRefs(parseExpr("rho * g * buoy.vol + pi"))].sort()).toEqual(["buoy.vol", "g", "rho"]);
  });

  it("rejects anything outside the grammar", () => {
    for (const bad of ["alert(1)", "a;b", "a.constructor", "x[0]", "1 +", "foo(1)", "`x`", "a = 2", "max()"]) {
      expect(() => evalExpr(parseExpr(bad), env), bad).toThrow();
    }
  });

  it("limits nesting depth", () => {
    expect(() => parseExpr(`${"(".repeat(40)}1${")".repeat(40)}`)).toThrow(/nested/);
  });

  it("formats numbers with patterns", () => {
    expect(formatNumber(4.905, "0.0 N")).toBe("4.9 N");
    expect(formatNumber(1234.5, "#,##0 kg")).toBe("1,235 kg");
    expect(formatNumber(-0.001, "0.00")).toBe("0.00");
    expect(formatNumber(-2.5, "0.0")).toBe("−2.5");
    expect(formatNumber(0.5, "v = 0.00 m/s")).toBe("v = 0.50 m/s");
    expect(formatNumber(1.23456, undefined)).toBe("1.23");
  });
});

describe("path parser", () => {
  it("normalizes all commands to M/L/C/Z", () => {
    const segs = parsePath("M0 0 h10 v10 H0 V0 q5 5 10 0 t10 0 c1 1 2 2 3 3 s4 4 5 5 a5 5 0 0 1 10 0 Z");
    expect(new Set(segs.map((s) => s.c))).toEqual(new Set(["M", "L", "C", "Z"]));
  });

  it("computes exact bounds for curves", () => {
    const b = pathBounds(parsePath("M0 0 C 0 100 100 100 100 0"));
    expect(b.h).toBeCloseTo(75, 5);
    expect(b.w).toBeCloseTo(100, 5);
  });

  it("rejects malformed data", () => {
    for (const bad of ["", "L10 10", "M0 0 L", "M0 0 X 1 2", "M0 0 A5 5 0 2 1 10 0", "M0 0 L1e999 0"]) {
      expect(() => parsePath(bad), bad).toThrow();
    }
  });
});
