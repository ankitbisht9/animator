/**
 * The engine service behind the worker protocol. Pure functions of their input
 * (the worker adds transport, caching and failure handling).
 */
import { z } from "zod";
import { allSpecs } from "../catalog/registry";
import { ENTRANCE_STYLES, EXIT_STYLES } from "../catalog/spec";
import { compileScene } from "../compile/compile";
import { ACTION_TYPES, HIGHLIGHT_STYLES } from "../dsl/actions";
import { lift } from "../dsl/lift";
import { canonicalJsonSchema, llmJsonSchema } from "../dsl/llm-schema";
import { LIMITS } from "../dsl/scene";
import { evaluate } from "../evaluate/evaluate";
import { EASING_NAMES } from "../interp/easing";
import { LAYOUTS } from "../layout/templates";
import { sceneEndState, summarizeState } from "../scene-state/state";
import type { SceneState } from "../scene-state/types";
import { allSolvers } from "../solvers";
import { COLOR_ROLES, COLOR_VARIANTS } from "../theme/theme";
import { THEME_IDS } from "../theme/themes";
import type { CompiledScene } from "../timeline/types";
import type { ValidationIssue } from "../validation/issues";
import { hasErrors } from "../validation/issues";
import { DSL_VERSION, ENGINE_VERSION, PROTOCOL_VERSION } from "../version";

export function versionInfo() {
  return { protocolVersion: PROTOCOL_VERSION, engineVersion: ENGINE_VERSION, dslVersion: DSL_VERSION };
}

export function schemaFor(kind: "canonical" | "llm") {
  return { kind, schema: kind === "llm" ? llmJsonSchema() : canonicalJsonSchema() };
}

export function catalogInfo() {
  return {
    engineVersion: ENGINE_VERSION,
    dslVersion: DSL_VERSION,
    // Hidden components (the pipeline's `figure`) are not the model's to use.
    components: allSpecs()
      .filter((s) => !s.hidden)
      .map((s) => {
        const params = z.toJSONSchema(s.params, { io: "input", unrepresentable: "any" }) as Record<string, unknown>;
        delete params.$schema;
        return {
          type: s.type,
          category: s.category,
          description: s.description,
          params,
          anchors: [...(s.anchors ?? [])],
          dynamicAnchors: !!s.dynamicAnchors,
          entrance: s.entrance,
          draw: !!s.draw,
          write: !!s.write,
          labelLike: !!s.labelLike,
          decorative: !!s.decorative,
          worldSpace: !!s.worldSpace,
          semanticRoles: [...s.semanticRoles],
          examples: s.examples.map((e) => ({ ...e })),
        };
      }),
    actions: [...ACTION_TYPES],
    easings: [...EASING_NAMES],
    colorRoles: [...COLOR_ROLES],
    colorVariants: [...COLOR_VARIANTS],
    layouts: Object.values(LAYOUTS).map((l) => ({ id: l.id, slots: Object.keys(l.slots) })),
    solvers: allSolvers()
      .map((s) => ({ type: s.type, outputs: s.outputs.map((o) => ({ ...o })), dynamicOutputs: !!s.dynamicOutputs }))
      .sort((a, b) => a.type.localeCompare(b.type)),
    themes: [...THEME_IDS],
    entranceStyles: [...ENTRANCE_STYLES],
    exitStyles: [...EXIT_STYLES],
    highlightStyles: [...HIGHLIGHT_STYLES],
    limits: { ...LIMITS },
  };
}

export function liftScene(scene: unknown) {
  const r = lift(scene);
  return r.ok && r.scene
    ? { ok: true, scene: r.scene as unknown as Record<string, unknown>, issues: r.issues }
    : { ok: false, issues: r.issues };
}

export interface CompileOutcome {
  ok: boolean;
  issues: ValidationIssue[];
  compiled?: CompiledScene;
  canonical?: Record<string, unknown>;
}

/** Lift + compile (the unit every scene.* method builds on). */
export function compileInput(scene: unknown, prev: SceneState | null | undefined): CompileOutcome {
  const l = lift(scene);
  if (!l.ok || !l.scene) return { ok: false, issues: l.issues };
  const c = compileScene(l.scene, { prev: prev ?? null });
  const out: CompileOutcome = { ok: c.ok, issues: c.issues, canonical: l.scene as unknown as Record<string, unknown> };
  if (c.compiled) out.compiled = c.compiled;
  return out;
}

export function validateResult(o: CompileOutcome) {
  return { ok: o.ok && !hasErrors(o.issues), issues: o.issues, ...(o.canonical ? { scene: o.canonical } : {}) };
}

export function compileResult(o: CompileOutcome, prev: SceneState | null | undefined, lenient = false) {
  // Lenient: a scene that compiled but failed its layout checks comes back with its compiled form
  // (for a layout planner to measure), still not ok and without an end state.
  if (lenient && !o.ok && o.compiled) {
    return {
      ok: false,
      issues: o.issues,
      compiled: o.compiled as unknown as Record<string, unknown>,
      duration: o.compiled.duration,
      beats: o.compiled.beats,
    };
  }
  if (!o.ok || !o.compiled) return { ok: false, issues: o.issues };
  const endState = sceneEndState(o.compiled, prev ?? null, prev ? prev.sceneIndex + 1 : 0);
  return {
    ok: true,
    issues: o.issues,
    compiled: o.compiled as unknown as Record<string, unknown>,
    endState,
    stateSummary: summarizeState(endState),
    duration: o.compiled.duration,
    beats: o.compiled.beats,
  };
}

export function stateAtResult(o: CompileOutcome, prev: SceneState | null | undefined, t: number) {
  if (!o.ok || !o.compiled) return { ok: false, issues: o.issues };
  return {
    ok: true,
    issues: o.issues,
    state: sceneEndState(o.compiled, prev ?? null, prev ? prev.sceneIndex + 1 : 0, t),
  };
}

export function sampleResult(o: CompileOutcome, times: number[], lenient = false) {
  // Lenient: a scene that compiled but failed its layout checks is still sampled (ok stays false),
  // so a layout planner can measure it. A scene that did not compile has nothing to sample.
  if (!o.compiled || (!o.ok && !lenient)) return { ok: false, issues: o.issues, frames: [] };
  const cs = o.compiled;
  return {
    ok: o.ok,
    issues: o.issues,
    frames: times.map((t) => evaluate(cs, t) as unknown as Record<string, unknown>),
  };
}
