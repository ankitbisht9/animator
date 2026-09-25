/**
 * Spike (b): is MathJax SVG output deterministic?
 *
 * Typesets a corpus of TeX with fontCache "none" (self-contained glyph paths, no
 * font dependency), hashes the SVG, and compares hashes across (1) repeated runs
 * in one process and (2) a fresh child process. Also reports typesetting cost.
 *
 *   pnpm --filter @animator/engine exec tsx scripts/spikes/mathjax_determinism.ts
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { liteAdaptor } from "mathjax-full/js/adaptors/liteAdaptor.js";
import { RegisterHTMLHandler } from "mathjax-full/js/handlers/html.js";
import { TeX } from "mathjax-full/js/input/tex.js";
import "mathjax-full/js/input/tex/ams/AmsConfiguration.js";
import "mathjax-full/js/input/tex/base/BaseConfiguration.js";
import "mathjax-full/js/input/tex/html/HtmlConfiguration.js";
import { mathjax } from "mathjax-full/js/mathjax.js";
import { SVG } from "mathjax-full/js/output/svg.js";

const CORPUS = [
  String.raw`F_b = \rho_f \, V_{sub} \, g`,
  String.raw`\cssId{t-w}{W} = \cssId{t-m}{m g}`,
  String.raw`\frac{1}{2}\rho v_1^2 + p_1 + \rho g h_1 = \frac{1}{2}\rho v_2^2 + p_2 + \rho g h_2`,
  "A_1 v_1 = A_2 v_2",
  String.raw`\Delta h = \frac{V_{sub}}{A_{tank}}`,
  String.raw`\sqrt{a^2 + b^2} = c`,
  String.raw`\text{apparent weight} = W - F_b`,
];

function typesetAll(): { hashes: string[]; ms: number } {
  const adaptor = liteAdaptor();
  RegisterHTMLHandler(adaptor);
  const tex = new TeX({ packages: ["base", "ams", "html"] });
  const svg = new SVG({ fontCache: "none" });
  const doc = mathjax.document("", { InputJax: tex, OutputJax: svg });
  const t0 = performance.now();
  const hashes = CORPUS.map((src) => {
    const node = doc.convert(src, { display: true, em: 16, ex: 8, containerWidth: 1280 });
    const out = adaptor.outerHTML(node);
    return createHash("sha256").update(out).digest("hex").slice(0, 16);
  });
  return { hashes, ms: performance.now() - t0 };
}

if (process.argv.includes("--child")) {
  process.stdout.write(JSON.stringify(typesetAll()));
} else {
  const first = typesetAll();
  const second = typesetAll(); // same process, warm
  const child = JSON.parse(
    execFileSync(process.execPath, [...process.execArgv, fileURLToPath(import.meta.url), "--child"], {
      encoding: "utf8",
    }),
  ) as { hashes: string[]; ms: number };
  const sameInProcess = first.hashes.every((h, i) => h === second.hashes[i]);
  const sameAcrossProcess = first.hashes.every((h, i) => h === child.hashes[i]);
  const report = {
    expressions: CORPUS.length,
    coldMs: Math.round(first.ms),
    warmMs: Math.round(second.ms),
    warmMsPerExpression: +(second.ms / CORPUS.length).toFixed(2),
    sameInProcess,
    sameAcrossProcess,
    hashes: first.hashes,
  };
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  if (!sameInProcess || !sameAcrossProcess) process.exit(1);
}
