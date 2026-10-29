// Bundles the worker and CLI into self-contained CommonJS files so the Python
// API can spawn `node dist/worker.cjs` without a node_modules lookup at runtime.
// `dist/frames.cjs` (keyframes without a browser, ADR-068) also bundles React and the scene components;
// resvg's WebAssembly sits beside it as `dist/resvg.wasm`.
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { build } from "esbuild";

const common = {
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node24",
  sourcemap: "linked",
  logLevel: "warning",
  legalComments: "none",
};

await build({ ...common, entryPoints: ["src/main.ts"], outfile: "dist/worker.cjs" });
await build({ ...common, entryPoints: ["src/cli.ts"], outfile: "dist/cli.cjs", banner: { js: "#!/usr/bin/env node" } });
await build({
  ...common,
  entryPoints: ["src/frames-main.ts"],
  outfile: "dist/frames.cjs",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' },
});
mkdirSync("dist", { recursive: true });
copyFileSync(createRequire(import.meta.url).resolve("@resvg/resvg-wasm/index_bg.wasm"), "dist/resvg.wasm");
