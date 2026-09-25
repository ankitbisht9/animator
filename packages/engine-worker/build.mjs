// Bundles the worker and CLI into self-contained CommonJS files so the Python
// API can spawn `node dist/worker.cjs` without a node_modules lookup at runtime.
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
