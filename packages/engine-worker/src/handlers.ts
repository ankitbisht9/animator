/**
 * JSON-RPC method handlers. Every call is stateless and idempotent: the request
 * carries the scene and the previous SceneState. Compilation is cached by
 * content hash purely as an optimization.
 */
import { createHash } from "node:crypto";
import {
  type CompileOutcome,
  catalogInfo,
  compileInput,
  compileResult,
  liftScene,
  protocol,
  type SceneState,
  sampleResult,
  schemaFor,
  stateAtResult,
  validateResult,
  versionInfo,
} from "@animator/engine";
import type { z } from "zod";
import { type Handler, RPC_ERRORS, RpcError } from "./rpc";

const startedAt = Date.now();

function parse<T extends z.ZodType>(schema: T, params: unknown): z.infer<T> {
  const r = schema.safeParse(params);
  if (!r.success) {
    throw new RpcError(
      RPC_ERRORS.INVALID_PARAMS,
      "Invalid params",
      r.error.issues.map((i) => ({ path: i.path.join("/"), message: i.message })),
    );
  }
  return r.data;
}

const CACHE_MAX = 32;
const cache = new Map<string, CompileOutcome>();

function compileCached(scene: unknown, prev: SceneState | null | undefined): CompileOutcome {
  const key = createHash("sha256")
    .update(JSON.stringify([scene, prev ?? null]))
    .digest("hex");
  const hit = cache.get(key);
  if (hit) {
    cache.delete(key);
    cache.set(key, hit);
    return hit;
  }
  const out = compileInput(scene, prev);
  cache.set(key, out);
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value as string);
  return out;
}

let catalogCache: ReturnType<typeof catalogInfo> | null = null;

export const handlers: Record<string, Handler> = {
  version: () => versionInfo(),
  "health.ping": () => ({ ok: true, uptimeMs: Date.now() - startedAt, rssBytes: process.memoryUsage().rss }),
  "schema.get": (params) => schemaFor(parse(protocol.SchemaGetParams, params).kind),
  "catalog.get": () => {
    catalogCache ??= catalogInfo();
    return catalogCache;
  },
  "scene.lift": (params) => liftScene(parse(protocol.LiftParams, params).scene),
  "scene.validate": (params) => {
    const p = parse(protocol.SceneParams, params);
    return validateResult(compileCached(p.scene, p.prevState as SceneState | null | undefined));
  },
  "scene.compile": (params) => {
    const p = parse(protocol.SceneParams, params);
    const prev = p.prevState as SceneState | null | undefined;
    return compileResult(compileCached(p.scene, prev), prev);
  },
  "scene.stateAt": (params) => {
    const p = parse(protocol.StateAtParams, params);
    const prev = p.prevState as SceneState | null | undefined;
    return stateAtResult(compileCached(p.scene, prev), prev, p.t);
  },
  "scene.sample": (params) => {
    const p = parse(protocol.SampleParams, params);
    return sampleResult(compileCached(p.scene, p.prevState as SceneState | null | undefined), p.times);
  },
};

// Failure-injection hooks for the Python contract tests (crash/restart, timeouts).
// Only registered when explicitly enabled.
if (process.env.ENGINE_WORKER_DEBUG === "1") {
  handlers["debug.crash"] = () => process.exit(99);
  handlers["debug.hang"] = () => {
    const until = Date.now() + 60_000;
    while (Date.now() < until) {
      // busy-wait on purpose: simulates a runaway compile
    }
    return null;
  };
}
