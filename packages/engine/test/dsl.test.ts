import { describe, expect, it } from "vitest";
import { canonicalJsonSchema, dropNulls, lift, llmJsonSchema } from "../src";
import { base } from "./helpers";

type Json = Record<string, unknown>;

/** Visit every schema node (not property-name maps). */
function walk(node: unknown, visit: (n: Json) => void) {
  if (!node || typeof node !== "object" || Array.isArray(node)) return;
  const n = node as Json;
  visit(n);
  for (const key of ["properties", "$defs"] as const) {
    const map = n[key] as Record<string, unknown> | undefined;
    if (map) for (const v of Object.values(map)) walk(v, visit);
  }
  if (n.items) walk(n.items, visit);
  for (const v of (n.anyOf as unknown[] | undefined) ?? []) walk(v, visit);
}

describe("canonical DSL", () => {
  it("fills defaults on lift", () => {
    const r = lift(base({ objects: [{ id: "c", type: "circle" }] }));
    expect(r.ok).toBe(true);
    expect(r.scene?.theme).toBe("educational_clean");
    expect(r.scene?.objects[0]).toMatchObject({
      scale: 1,
      opacity: 1,
      layer: "main",
      persist: true,
      params: { radius: 50 },
    });
  });

  it("rejects unknown component types with a suggestion", () => {
    const r = lift(base({ objects: [{ id: "c", type: "circel" }] }));
    expect(r.ok).toBe(false);
    expect(r.issues[0]).toMatchObject({ code: "UNKNOWN_TYPE", path: "/objects/0/type" });
    expect(r.issues[0]?.suggestion).toContain("circle");
  });

  it("rejects unknown fields", () => {
    const r = lift(base({ objects: [{ id: "c", type: "circle", colour: "red" }] }));
    expect(r.issues.map((i) => i.code)).toContain("UNKNOWN_FIELD");
  });

  it("rejects bad ids and wrong dsl_version", () => {
    expect(lift(base({ id: "Bad Id" })).ok).toBe(false);
    expect(lift(base({ dsl_version: "2.0" })).ok).toBe(false);
  });

  it("assigns action ids and sequential starts", () => {
    const r = lift(
      base({
        objects: [{ id: "c", type: "circle" }],
        timeline: [{ action: "create", target: "c" }, { action: "wait" }],
      }),
    );
    expect(r.scene?.timeline.map((a) => [a.id, a.start])).toEqual([
      ["a1", { at: 0 }],
      ["a2", { after: "a1" }],
    ]);
  });

  it("is idempotent", () => {
    const once = lift(base({ objects: [{ id: "c", type: "circle" }], timeline: [{ action: "create", target: "c" }] }));
    const twice = lift(once.scene);
    expect(twice.scene).toEqual(once.scene);
  });

  it("drops nulls produced by strict structured outputs", () => {
    expect(dropNulls({ a: null, b: { c: null, d: 1 }, e: [null, 2] })).toEqual({ b: { d: 1 }, e: [2] });
    const r = lift({
      ...base(),
      camera: null,
      objects: [{ id: "c", type: "circle", params: { radius: null }, place: null, style: null }],
    });
    expect(r.ok).toBe(true);
    expect(r.scene?.objects[0]?.params.radius).toBe(50);
  });
});

describe("LLM-facing schema", () => {
  const schema = llmJsonSchema();

  it("has an object root and strict objects everywhere", () => {
    expect(schema.type).toBe("object");
    walk(schema, (n) => {
      if (n.type === "object") {
        expect(n.additionalProperties).toBe(false);
        expect(new Set(n.required as string[])).toEqual(new Set(Object.keys(n.properties as Json)));
      }
    });
  });

  it("leaves out the fields the pipeline owns", () => {
    const defs = schema.$defs as Record<string, Json>;
    const transition = defs.TransitionIn as Json;
    expect(Object.keys(transition.properties as Json)).toEqual(["type", "duration", "keep"]);
    expect(JSON.stringify(schema)).not.toContain("x-llm-omit");
  });

  it("never puts a keyword next to $ref (OpenAI rejects the whole schema)", () => {
    walk(schema, (n) => {
      if (typeof n.$ref === "string") expect(Object.keys(n)).toEqual(["$ref"]);
    });
  });

  it("keeps descriptions of optional references on their nullable wrapper", () => {
    const defs = schema.$defs as Record<string, Json>;
    const arrow = defs.Object_vector_arrow;
    if (!arrow) throw new Error("vector_arrow missing from the schema");
    const wrapper = (arrow.properties as Json).params as Json; // params is optional
    const params = (wrapper.anyOf as Json[])[0] as Json;
    expect((params.properties as Json).from).toEqual({ $ref: "#/$defs/Point" }); // required: a bare ref
    let described = 0;
    walk(schema, (n) => {
      const alts = n.anyOf as Json[] | undefined;
      if (alts?.length === 2 && typeof alts[0]?.$ref === "string" && alts[1]?.type === "null" && n.description)
        described++;
    });
    expect(described).toBeGreaterThan(0);
  });

  it("drops keywords strict mode does not need", () => {
    walk(schema, (n) => {
      for (const k of ["default", "minimum", "maximum", "pattern", "oneOf", "const", "minLength", "maxItems"])
        expect(n).not.toHaveProperty(k);
    });
  });

  it("offers color roles as an enum", () => {
    const defs = schema.$defs as Record<string, Json>;
    expect(defs.ColorRef?.enum).toContain("force_buoyant");
    expect(defs.ColorRef?.enum).toContain("fluid.soft");
  });

  it("keeps the canonical schema's field order: identity first", () => {
    const defs = canonicalJsonSchema().$defs as Record<string, Json>;
    expect(Object.keys(defs.Object_circle?.properties as Json).slice(0, 3)).toEqual(["id", "type", "params"]);
  });
});
