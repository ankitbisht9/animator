/**
 * Writes the generated contracts (JSON Schemas, catalog, version manifest) and
 * generated docs. Output is committed; CI runs `make codegen` and fails on drift.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { DSL_VERSION, ENGINE_VERSION, PROTOCOL_VERSION } from "../src/version";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

function write(rel: string, content: string) {
  const path = resolve(root, rel);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content.endsWith("\n") ? content : `${content}\n`, "utf8");
  process.stdout.write(`wrote ${rel}\n`);
}

const json = (v: unknown) => JSON.stringify(v, null, 2);

write(
  "contracts/engine.json",
  json({ protocolVersion: PROTOCOL_VERSION, engineVersion: ENGINE_VERSION, dslVersion: DSL_VERSION }),
);
