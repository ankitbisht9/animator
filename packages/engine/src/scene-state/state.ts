/**
 * Computes a scene's end state (P6: engine-computed, never LLM-reported), the
 * continuity checks between consecutive scenes, and the compact summary the LLM
 * sees instead of full JSON.
 */
import { getSpec } from "../catalog/registry";
import type { SceneT } from "../dsl/scene";
import { cameraAt, evaluateObjects, solverValueAt } from "../evaluate/evaluate";
import { rad2deg, roundTo } from "../math/num";
import { sampleChannel, sampleNumber } from "../timeline/channel";
import type { CompiledScene } from "../timeline/types";
import { issue, type ValidationIssue } from "../validation/issues";
import type { Lifecycle, ObjectState, RegistryEntry, SceneState } from "./types";

const r = (v: number) => roundTo(v, 4);

export function sceneEndState(cs: CompiledScene, prev: SceneState | null, index = 0, at?: number): SceneState {
  const T = Math.max(0, Math.min(cs.duration, at ?? cs.duration));
  const ctx = evaluateObjects(cs, T);
  const objects: Record<string, ObjectState> = {};
  const registry: Record<string, RegistryEntry> = { ...(prev?.registry ?? {}) };

  for (const o of cs.objects) {
    const ev = ctx.objects.get(o.id);
    if (!ev) continue;
    const presence = sampleNumber(o.ch.presence, T, ctx.env, 1);
    const spec = getSpec(o.type);
    // A one-shot (e.g. a burst) that has finished playing is gone, not "visible".
    const played = spec?.oneShot === true && presence >= 0.5 && sampleNumber(o.ch.reveal, T, ctx.env, 1) >= 0.999;
    const removed = played || (o.removedAt !== undefined && o.removedAt <= T + 1e-9);
    const lifecycle: Lifecycle = removed ? "removed" : presence < 0.5 ? "hidden" : "active";

    // Freeze animated and bound params; keep anchor references so followers keep following.
    const params: Record<string, unknown> = { ...ev.params };
    for (const k of spec?.pointParams ?? []) if (typeof o.params[k] === "string") params[k] = o.params[k];
    if (typeof params.__morphD === "string") {
      if (o.type === "path") params.d = params.__morphD;
      delete params.__morphD;
    }

    // Group links active at the end are baked into world transforms.
    const link = o.links.find((l) => T >= l.t0 && T < l.t1);
    let x = sampleNumber(o.ch.x, T, ctx.env, 0);
    let y = sampleNumber(o.ch.y, T, ctx.env, 0);
    let rotation = sampleNumber(o.ch.rotation, T, ctx.env, 0);
    let scale = sampleNumber(o.ch.scale, T, ctx.env, 1);
    if (link || o.followPlace) {
      const m = ev.rawMatrix;
      x = m[4];
      y = m[5];
      if (link) {
        rotation = rad2deg(Math.atan2(m[1], m[0]));
        scale = Math.hypot(m[0], m[1]);
      }
    }

    const state: ObjectState = {
      id: o.id,
      type: o.type,
      params,
      x: r(x),
      y: r(y),
      rotation: r(rotation),
      scale: r(scale),
      opacity: r(sampleNumber(o.ch.opacity, T, ctx.env, 1)),
      visible: lifecycle === "active",
      fill: String(sampleChannel(o.ch.fill, T, ctx.env) ?? o.ch.fill.base),
      stroke: String(sampleChannel(o.ch.stroke, T, ctx.env) ?? o.ch.stroke.base),
      text: String(sampleChannel(o.ch.text, T, ctx.env) ?? o.ch.text.base),
      strokeWidth: sampleNumber(o.ch.strokeWidth, T, ctx.env, 4),
      shadow: sampleChannel(o.ch.shadow, T, ctx.env) === true,
      dashed: sampleChannel(o.ch.dashed, T, ctx.env) === true,
      layer: o.layer,
      z: o.z,
      persist: o.persist,
      decorative: o.decorative,
      lifecycle,
      bbox: { x: r(ev.bbox.x), y: r(ev.bbox.y), w: r(ev.bbox.w), h: r(ev.bbox.h) },
    };
    if (o.parent) state.parent = o.parent;
    if (o.followPlace) state.followPlace = o.followPlace;
    if (o.metadata) state.metadata = o.metadata;
    objects[o.id] = state;
    registry[o.id] = {
      type: o.type,
      createdIn: registry[o.id]?.createdIn ?? cs.id,
      lastScene: cs.id,
      lifecycle: o.persist ? lifecycle : "removed",
    };
  }

  const solvers: Record<string, Record<string, number>> = {};
  for (const s of cs.solvers) {
    const out: Record<string, number> = { ...s.constants };
    for (const name of Object.keys(s.channels)) out[name] = r(solverValueAt(s, name, T) ?? 0);
    solvers[s.id] = out;
  }

  const cam = cameraAt(cs, T, ctx.env);
  return {
    sceneId: cs.id,
    sceneIndex: index,
    time: roundTo(T, 6),
    themeId: cs.themeId,
    camera: { cx: r(cam.cx), cy: r(cam.cy), zoom: r(cam.zoom) },
    objects,
    registry,
    solvers,
    values: { ...cs.values },
  };
}

/**
 * Continuity between a previous end state and the next scene:
 *  - abrupt disappearance: visible at the end of scene N, not carried into N+1,
 *    with no exit animation and no transition;
 *  - identity drift: an object redeclared with the same id but a different look
 *    and no transition (a type change is a hard error, see semantic validation).
 */
export function validateContinuity(prev: SceneState | null, next: SceneT): ValidationIssue[] {
  if (!prev) return [];
  const out: ValidationIssue[] = [];
  const continuing = next.transition_in.type === "continue";
  for (const s of Object.values(prev.objects)) {
    if (s.visible && !s.persist && !s.decorative && continuing) {
      out.push(
        issue(
          "ABRUPT_DISAPPEARANCE",
          "/transition_in",
          `'${s.id}' is visible at the end of scene '${prev.sceneId}' but does not persist, so it would vanish at the cut.`,
          {
            severity: "warning",
            suggestion: `Remove it with an exit animation in '${prev.sceneId}', or give this scene a fade/wipe transition_in.`,
            source: "semantic",
          },
        ),
      );
    }
  }
  next.objects.forEach((def, i) => {
    const s = prev.objects[def.id];
    if (!s || s.type !== def.type || !continuing || !s.visible) return;
    const spec = getSpec(def.type);
    const changed = Object.keys(spec?.params.shape ?? {}).filter((k) => {
      const a = def.params[k];
      const b = s.params[k];
      return a !== undefined && typeof a !== "object" && typeof b !== "object" && a !== b;
    });
    if (changed.length) {
      out.push(
        issue(
          "IDENTITY_DRIFT",
          `/objects/${i}/params`,
          `'${def.id}' carries over from '${prev.sceneId}' but its ${changed.join(", ")} changed without a transform.`,
          {
            severity: "warning",
            suggestion: "Do not redeclare carried objects; animate the change with a transform action instead.",
          },
        ),
      );
    }
  });
  return out;
}

/** Compact, LLM-friendly summary of what is on stage (not the full JSON). */
export function summarizeState(state: SceneState): string {
  const lines = Object.values(state.objects)
    .filter((o) => o.lifecycle !== "removed" && o.persist)
    .map((o) => {
      const b = o.bbox;
      const vis = o.visible ? "visible" : "hidden";
      const name = o.metadata?.name ? ` "${o.metadata.name}"` : "";
      return `- ${o.id} (${o.type}${name}, ${vis}) at x=${Math.round(b.x + b.w / 2)}, y=${Math.round(b.y + b.h / 2)}, size ${Math.round(b.w)}x${Math.round(b.h)}`;
    });
  const solvers = Object.entries(state.solvers).map(
    ([id, v]) =>
      `- ${id}: ${Object.entries(v)
        .slice(0, 8)
        .map(([k, x]) => `${k}=${roundTo(x, 3)}`)
        .join(", ")}`,
  );
  return [
    `Previous scene: ${state.sceneId}. Camera center (${Math.round(state.camera.cx)}, ${Math.round(state.camera.cy)}), zoom ${state.camera.zoom}.`,
    "Objects on stage (reuse these ids; do not redeclare them):",
    ...(lines.length ? lines : ["- none"]),
    ...(solvers.length ? ["Solver results from the previous scene:", ...solvers] : []),
  ].join("\n");
}
