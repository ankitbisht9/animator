/**
 * Print object anchors/bounds of a fixture lesson at chosen scene-local times, to
 * help authors place things precisely.
 *   pnpm --filter @animator/engine exec tsx scripts/probe-lesson.ts <lesson> <sceneIndex> <t> [ids...]
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compileScene, evaluateObjects, lift, type SceneState, sceneEndState } from "../src";

const [slug = "archimedes", idxArg = "0", tArg = "0", ...ids] = process.argv.slice(2);
const dir = resolve(dirname(fileURLToPath(import.meta.url)), "../../../fixtures/scenes", slug);
const files = readdirSync(dir)
  .filter((f) => f.endsWith(".json") && f !== "lesson.json")
  .sort();
let prev: SceneState | null = null;
for (let i = 0; i <= Number(idxArg); i++) {
  const l = lift(JSON.parse(readFileSync(resolve(dir, files[i] as string), "utf8")));
  if (!l.ok || !l.scene) throw new Error(`${files[i]}: ${JSON.stringify(l.issues)}`);
  const c = compileScene(l.scene, { prev });
  if (!c.compiled) throw new Error(`${files[i]}: ${JSON.stringify(c.issues.filter((x) => x.severity === "error"))}`);
  if (i < Number(idxArg)) {
    prev = sceneEndState(c.compiled, prev, i);
    continue;
  }
  const ctx = evaluateObjects(c.compiled, Number(tArg));
  for (const [id, e] of ctx.objects) {
    if (ids.length && !ids.includes(id)) continue;
    const b = e.layoutBBox;
    const r = (v: number) => Math.round(v * 10) / 10;
    process.stdout.write(
      `${id} (${e.obj.type}) bbox x=${r(b.x)}..${r(b.x + b.w)} y=${r(b.y)}..${r(b.y + b.h)} visible=${e.visible}\n`,
    );
    for (const [k, v] of Object.entries(e.anchors))
      if (!["top_left", "top_right", "bottom_left", "bottom_right"].includes(k))
        process.stdout.write(`   ${k}: ${r(v.x)}, ${r(v.y)}\n`);
  }
  for (const s of c.compiled.solvers) process.stdout.write(`solver ${s.id}: ${JSON.stringify(s.constants)}\n`);
}
