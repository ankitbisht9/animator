/**
 * Debug CLI around the same handlers the worker exposes.
 *
 *   engine version
 *   engine validate <scene.json> [--prev state.json]
 *   engine compile  <scene.json> [--prev state.json] [--full]
 *   engine state    <scene.json> [--prev state.json] [--t 3.5]
 *   engine sample   <scene.json> [--prev state.json] --times 0,1.5,3
 *   engine schema   [llm|canonical]
 *   engine catalog
 *
 * Exit code: 0 ok, 1 scene has errors, 2 usage error.
 */
import { readFileSync } from "node:fs";
import { handlers } from "./handlers";

function flag(args: string[], name: string): string | undefined {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
}

const readJson = (path: string): unknown => JSON.parse(readFileSync(path, "utf8"));
const out = (v: unknown) => process.stdout.write(`${JSON.stringify(v, null, 2)}\n`);

function call(method: string, params: unknown): Record<string, unknown> {
  const h = handlers[method];
  if (!h) throw new Error(`no handler ${method}`);
  return h(params) as Record<string, unknown>;
}

function main(argv: string[]): number {
  const [cmd, file, ...rest] = argv;
  const prevPath = flag(rest, "--prev");
  const prevState = prevPath ? readJson(prevPath) : null;
  switch (cmd) {
    case "version":
      out(call("version", {}));
      return 0;
    case "schema":
      out(call("schema.get", { kind: file === "canonical" ? "canonical" : "llm" }).schema);
      return 0;
    case "catalog":
      out(call("catalog.get", {}));
      return 0;
    case "validate":
    case "compile":
    case "state":
    case "sample": {
      if (!file) break;
      const scene = readJson(file);
      let res: Record<string, unknown>;
      if (cmd === "validate") res = call("scene.validate", { scene, prevState });
      else if (cmd === "compile") {
        res = call("scene.compile", { scene, prevState });
        if (!rest.includes("--full")) delete res.compiled;
      } else if (cmd === "state")
        res = call("scene.stateAt", { scene, prevState, t: Number(flag(rest, "--t") ?? 1e9) });
      else
        res = call("scene.sample", { scene, prevState, times: (flag(rest, "--times") ?? "0").split(",").map(Number) });
      out(res);
      return res.ok ? 0 : 1;
    }
    default:
      break;
  }
  process.stderr.write(
    "usage: engine <version|schema|catalog|validate|compile|state|sample> [scene.json] [--prev state.json] [--t N] [--times a,b]\n",
  );
  return 2;
}

process.exit(main(process.argv.slice(2)));
