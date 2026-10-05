import { describe, expect, it } from "vitest";
import { handlers } from "../src/handlers";
import { createDispatcher, RPC_ERRORS } from "../src/rpc";

const dispatch = createDispatcher(handlers);
let id = 0;
const call = (method: string, params: unknown) =>
  JSON.parse(dispatch(JSON.stringify({ jsonrpc: "2.0", id: ++id, method, params })) ?? "null");

const scene = {
  dsl_version: "1.0",
  id: "w",
  title: "Worker",
  duration: 3,
  narration: { beats: [{ id: "b", text: "Hi." }] },
  objects: [{ id: "dot", type: "circle", place: { slot: "stage.center" } }],
  timeline: [{ action: "create", target: "dot" }],
};

describe("worker protocol", () => {
  it("exposes every protocol method", () => {
    for (const m of [
      "version",
      "health.ping",
      "schema.get",
      "catalog.get",
      "scene.lift",
      "scene.validate",
      "scene.compile",
      "scene.stateAt",
      "scene.sample",
    ]) {
      expect(Object.keys(handlers)).toContain(m);
    }
  });

  it("returns schemas and the catalog", () => {
    expect(call("schema.get", { kind: "llm" }).result.schema.type).toBe("object");
    expect(call("schema.get", {}).result.kind).toBe("llm");
    const cat = call("catalog.get", {}).result;
    expect(cat.components.length).toBeGreaterThan(40);
    expect(cat.solvers.map((s: { type: string }) => s.type)).toContain("buoyancy");
  });

  it("validates, compiles, samples and snapshots state", () => {
    expect(call("scene.validate", { scene }).result.ok).toBe(true);
    const c = call("scene.compile", { scene }).result;
    expect(c.ok).toBe(true);
    expect(c.endState.objects.dot.visible).toBe(true);
    expect(call("scene.sample", { scene, times: [0, 3] }).result.frames).toHaveLength(2);
    expect(call("scene.stateAt", { scene, t: 0 }).result.state.objects.dot.visible).toBe(false);
  });

  it("samples a scene that failed only its layout checks when asked to (ADR-047)", () => {
    const off = { ...scene, objects: [{ id: "dot", type: "circle", place: { x: -300, y: 450 } }] };
    expect(call("scene.compile", { scene: off }).result.ok).toBe(false); // outside the frame
    expect(call("scene.sample", { scene: off, times: [2] }).result.frames).toHaveLength(0);
    const lenient = call("scene.sample", { scene: off, times: [2], lenient: true }).result;
    expect(lenient.ok).toBe(false);
    expect(lenient.frames).toHaveLength(1);
    const broken = { ...scene, objects: [] }; // does not compile: still nothing to sample
    expect(call("scene.sample", { scene: broken, times: [2], lenient: true }).result.frames).toHaveLength(0);
    const compiled = call("scene.compile", { scene: off, lenient: true }).result;
    expect(compiled.ok).toBe(false);
    expect(compiled.compiled.actions).toBeDefined();
    expect(compiled.endState).toBeUndefined(); // never mistaken for a valid scene
    expect(call("scene.compile", { scene: off }).result.compiled).toBeUndefined();
  });

  it("reports scene problems as issues, not RPC errors", () => {
    const res = call("scene.compile", { scene: { ...scene, objects: [] } }).result;
    expect(res.ok).toBe(false);
    expect(res.issues[0].code).toBe("UNKNOWN_OBJECT");
  });

  it("rejects malformed params with -32602", () => {
    expect(call("scene.stateAt", { scene }).error.code).toBe(RPC_ERRORS.INVALID_PARAMS);
    expect(call("schema.get", { kind: "yaml" }).error.code).toBe(RPC_ERRORS.INVALID_PARAMS);
  });

  it("is idempotent: the same request gives the same answer", () => {
    const a = JSON.stringify(call("scene.compile", { scene }).result);
    const b = JSON.stringify(call("scene.compile", { scene }).result);
    expect(a).toBe(b);
  });
});
