import { DSL_VERSION, ENGINE_VERSION, PROTOCOL_VERSION } from "@animator/engine";
import type { Handler } from "./rpc";

const startedAt = Date.now();

export const handlers: Record<string, Handler> = {
  version: () => ({
    protocolVersion: PROTOCOL_VERSION,
    engineVersion: ENGINE_VERSION,
    dslVersion: DSL_VERSION,
  }),
  "health.ping": () => ({
    ok: true,
    uptimeMs: Date.now() - startedAt,
    rssBytes: process.memoryUsage().rss,
  }),
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
