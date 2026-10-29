/**
 * `node dist/frames.cjs` (ADR-068): reads a FramesRequest as JSON on stdin and writes
 * `{ ok: true, frames: [base64 PNG, ...] }` or `{ ok: false, error }` on stdout. One drawing per process:
 * nothing stays running between critic looks.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { type FramesRequest, loadResvg, renderFrames } from "./frames";

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

async function main(): Promise<void> {
  try {
    const req = JSON.parse(await readStdin()) as FramesRequest;
    await loadResvg(readFileSync(join(__dirname, "resvg.wasm")));
    const frames = await renderFrames(req);
    process.stdout.write(JSON.stringify({ ok: true, frames }));
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: (e as Error).message ?? String(e) }));
  }
}

void main();
