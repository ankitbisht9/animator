import { type CompiledScene, compileScene, lift, type SceneState, type SceneT, type ValidationIssue } from "../src";

export function base(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    dsl_version: "1.0",
    id: "s1",
    title: "Test scene",
    duration: 6,
    narration: { beats: [{ id: "b1", text: "Hello." }] },
    ...extra,
  };
}

export function lifted(extra: Record<string, unknown> = {}): SceneT {
  const r = lift(base(extra));
  if (!r.ok || !r.scene) throw new Error(`lift failed: ${JSON.stringify(r.issues, null, 1)}`);
  return r.scene;
}

export function compiled(extra: Record<string, unknown> = {}, prev?: SceneState): CompiledScene {
  const r = compileScene(lifted(extra), { prev: prev ?? null });
  if (!r.ok || !r.compiled)
    throw new Error(
      `compile failed: ${JSON.stringify(
        r.issues.filter((i) => i.severity === "error"),
        null,
        1,
      )}`,
    );
  return r.compiled;
}

/** Compile and return error codes (lift errors included). */
export function errorCodes(extra: Record<string, unknown>, prev?: SceneState): string[] {
  const r = lift(base(extra));
  if (!r.ok || !r.scene) return r.issues.map((i) => i.code);
  const c = compileScene(r.scene, { prev: prev ?? null });
  return c.issues.filter((i: ValidationIssue) => i.severity === "error").map((i) => i.code);
}
