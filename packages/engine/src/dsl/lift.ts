/**
 * lift(): LLM-facing JSON -> canonical DSL.
 *   1. drop nulls (strict mode's "not provided")
 *   2. expand shorthand (missing action ids and starts, equation `tex` -> parts)
 *   3. parse with the canonical schema (fills defaults)
 * Deterministic and idempotent: lift(lift(x)) === lift(x).
 */
import { fromZodIssues, type ValidationIssue } from "../validation/issues";
import { type SceneT, sceneSchema } from "./scene";

type Json = Record<string, unknown>;

export function dropNulls(v: unknown): unknown {
  if (Array.isArray(v)) return v.filter((x) => x !== null).map(dropNulls);
  if (typeof v === "object" && v !== null) {
    const out: Json = {};
    for (const [k, x] of Object.entries(v)) if (x !== null) out[k] = dropNulls(x);
    return out;
  }
  return v;
}

function expandShorthand(scene: Json): Json {
  const timeline = Array.isArray(scene.timeline) ? (scene.timeline as Json[]) : null;
  if (timeline) {
    const used = new Set(timeline.map((a) => a?.id).filter((x): x is string => typeof x === "string"));
    let n = 1;
    let prev: string | null = null;
    scene.timeline = timeline.map((a) => {
      if (typeof a !== "object" || a === null) return a;
      const out: Json = { ...a };
      if (typeof out.id !== "string") {
        while (used.has(`a${n}`)) n++;
        out.id = `a${n}`;
        used.add(out.id as string);
      }
      if (out.start === undefined) out.start = prev === null ? { at: 0 } : { after: prev };
      prev = out.id as string;
      return out;
    });
  }
  const objects = Array.isArray(scene.objects) ? (scene.objects as Json[]) : null;
  if (objects) {
    scene.objects = objects.map((o) => {
      if (o?.type !== "equation" || typeof o.params !== "object" || o.params === null) return o;
      const params = { ...(o.params as Json) };
      if (typeof params.tex === "string" && params.parts === undefined) {
        params.parts = [{ id: "all", tex: params.tex }];
      }
      delete params.tex;
      return { ...o, params };
    });
  }
  return scene;
}

export interface LiftResult {
  ok: boolean;
  scene?: SceneT;
  issues: ValidationIssue[];
}

export function lift(input: unknown): LiftResult {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    return {
      ok: false,
      issues: [
        { code: "SCHEMA", path: "", message: "Scene must be a JSON object.", severity: "error", source: "schema" },
      ],
    };
  }
  const cleaned = expandShorthand(dropNulls(structuredClone(input)) as Json);
  const parsed = sceneSchema().safeParse(cleaned);
  if (!parsed.success) return { ok: false, issues: fromZodIssues(parsed.error.issues, cleaned) };
  return { ok: true, scene: parsed.data as unknown as SceneT, issues: [] };
}
