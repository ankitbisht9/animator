/**
 * Engine worker entry point: newline-delimited JSON-RPC 2.0 over stdio.
 * stdout carries protocol messages only; all diagnostics go to stderr.
 */
import { createInterface } from "node:readline";
import { handlers } from "./handlers";
import { createDispatcher } from "./rpc";

// Defense in depth: the worker never needs the network.
const blocked = () => {
  throw new Error("network access is disabled in the engine worker");
};
Object.defineProperty(globalThis, "fetch", { value: blocked, writable: false });

const dispatch = createDispatcher(handlers);
const rl = createInterface({ input: process.stdin, crlfDelay: Number.POSITIVE_INFINITY });

rl.on("line", (line) => {
  if (!line.trim()) return;
  const out = dispatch(line);
  if (out !== null) process.stdout.write(`${out}\n`);
});

rl.on("close", () => process.exit(0));

process.on("uncaughtException", (err) => {
  process.stderr.write(`engine-worker fatal: ${err.stack ?? err.message}\n`);
  process.exit(70);
});
