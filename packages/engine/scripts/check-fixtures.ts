/**
 * Validate fixture lessons scene by scene (with continuity) and print every issue.
 *   pnpm --filter @animator/engine exec tsx scripts/check-fixtures.ts [lesson ...] [--warnings]
 * Exit code 1 when any scene has errors.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compileScene, lift, type SceneState, sceneEndState } from "../src";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../fixtures/scenes");
const args = process.argv.slice(2);
const showWarnings = args.includes("--warnings");
const lessons = args.filter((a) => !a.startsWith("--"));
const targets = lessons.length ? lessons : readdirSync(root);
let failed = false;

for (const slug of targets) {
  const dir = resolve(root, slug);
  const files = readdirSync(dir)
    .filter((f) => f.endsWith(".json") && f !== "lesson.json")
    .sort();
  let prev: SceneState | null = null;
  process.stdout.write(`\n== ${slug}\n`);
  files.forEach((file, i) => {
    const raw = JSON.parse(readFileSync(resolve(dir, file), "utf8"));
    const l = lift(raw);
    const issues = l.ok && l.scene ? compileScene(l.scene, { prev }).issues : l.issues;
    const errors = issues.filter((x) => x.severity === "error");
    const warnings = issues.filter((x) => x.severity === "warning");
    process.stdout.write(
      `${errors.length ? "FAIL" : "ok  "} ${file}  (${errors.length} errors, ${warnings.length} warnings)\n`,
    );
    for (const x of showWarnings ? [...errors, ...warnings] : errors) {
      process.stdout.write(
        `     ${x.severity === "error" ? "E" : "w"} ${x.code} ${x.path} ${x.message}${x.suggestion ? ` -> ${x.suggestion}` : ""}\n`,
      );
    }
    if (errors.length) failed = true;
    if (l.ok && l.scene) {
      const c = compileScene(l.scene, { prev });
      if (c.compiled && c.ok) prev = sceneEndState(c.compiled, prev, i);
    }
  });
}
process.exit(failed ? 1 : 0);
