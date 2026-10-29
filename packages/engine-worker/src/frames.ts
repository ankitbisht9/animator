/**
 * Keyframes without a browser (ADR-068): a lesson's scene drawn by the same <SceneView> the player and
 * the browser renderer use, written out as SVG on the server and rasterized by resvg (WebAssembly).
 * Text is already glyph outlines (ADR-004), so no fonts are needed. Used by the visual critic where no
 * browser is installed (the Render image).
 */
import { type FinishId, isFinish, SceneView } from "@animator/components";
import {
  CANVAS,
  type CompiledScene,
  compileScene,
  evaluate,
  lift,
  type SceneState,
  sceneEndState,
  type ValidationIssue,
} from "@animator/engine";
import { initWasm, Resvg } from "@resvg/resvg-wasm";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

export interface FramesRequest {
  /** The lesson's scenes so far (DSL); each starts from the one before's end state, as in the player. */
  scenes: unknown[];
  /** Which scene to draw. */
  index: number;
  /** Times in that scene (seconds; past its end draws its end). */
  times: number[];
  /** How drawings are finished (ADR-064); absent or unknown: classic. */
  finish?: string;
  /** Width of the PNGs in pixels (the height follows the stage, 16:9). */
  width?: number;
}

let ready: Promise<void> | null = null;

/** Loads resvg's WebAssembly once per process. */
export function loadResvg(wasm: Uint8Array | ArrayBuffer): Promise<void> {
  ready ??= initWasm(wasm);
  return ready;
}

/** A layout fault a scene was made with does not stop it being drawn (as in the player, ADR-060). */
function layoutOnly(issues: ValidationIssue[]): boolean {
  return issues.every((i) => i.severity !== "error" || i.source === "layout");
}

/** Scene `index` compiled after the scenes before it, as the player builds a lesson. */
export function compiledAt(scenes: unknown[], index: number): CompiledScene {
  let prev: SceneState | null = null;
  for (let i = 0; i <= index; i++) {
    const lifted = lift(scenes[i]);
    if (!lifted.ok || !lifted.scene) {
      if (i === index) throw new Error(`scene ${index} is not a valid scene`);
      continue;
    }
    const res = compileScene(lifted.scene, { prev });
    const ok = res.ok || (res.compiled !== undefined && layoutOnly(res.issues));
    if (!ok || !res.compiled) {
      if (i === index) {
        const first = res.issues.find((x) => x.severity === "error");
        throw new Error(`scene ${index} does not compile: ${first?.message ?? "unknown error"}`);
      }
      continue;
    }
    if (i === index) return res.compiled;
    prev = sceneEndState(res.compiled, prev, i);
  }
  throw new Error(`no scene ${index}`);
}

/** The scene's picture at `t` as an SVG document. */
export function sceneSvg(compiled: CompiledScene, t: number, finish: FinishId): string {
  const frame = evaluate(compiled, Math.max(0, Math.min(t, compiled.duration)));
  return renderToStaticMarkup(
    createElement(SceneView, { compiled, frame, finish, width: CANVAS.width, height: CANVAS.height }),
  );
}

/** PNG keyframes (base64) of scene `index` at each time. `loadResvg` first. */
export async function renderFrames(req: FramesRequest): Promise<string[]> {
  if (!ready) throw new Error("resvg is not loaded");
  await ready;
  const compiled = compiledAt(req.scenes, req.index);
  const finish: FinishId = isFinish(req.finish) ? req.finish : "classic";
  const width = Math.max(64, Math.min(3840, Math.round(req.width ?? 1280)));
  return req.times.map((t) => {
    const svg = sceneSvg(compiled, t, finish);
    const png = new Resvg(svg, { fitTo: { mode: "width", value: width } }).render().asPng();
    return Buffer.from(png).toString("base64");
  });
}
