/** Every file in fixtures/invalid must fail with (at least) the expected error codes. */
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { compileScene, lift } from "../src";

const dir = resolve(__dirname, "../../../fixtures/invalid");
const cases = readdirSync(dir).filter((f) => f.endsWith(".json"));

function errorCodes(scene: unknown): string[] {
  const l = lift(scene);
  if (!l.ok || !l.scene) return l.issues.filter((i) => i.severity === "error").map((i) => i.code);
  return compileScene(l.scene)
    .issues.filter((i) => i.severity === "error")
    .map((i) => i.code);
}

describe("invalid DSL corpus", () => {
  it("has cases", () => expect(cases.length).toBeGreaterThan(10));
  for (const file of cases) {
    it(file, () => {
      const { expect: expected, scene } = JSON.parse(readFileSync(resolve(dir, file), "utf8")) as {
        expect: string[];
        scene: unknown;
      };
      const codes = errorCodes(scene);
      if (expected.length === 0) expect(codes).toEqual([]);
      for (const code of expected) expect(codes).toContain(code);
    });
  }
});
