/**
 * Buoyancy (Archimedes). Statics are exact at every sample; the path the body
 * takes is a stylized, teachable one (fall, then a damped bob to equilibrium, or
 * a gentle sink to the floor, or a slow lowering on a string).
 *
 * Geometry comes from the scene at the moment the solver starts: container inner
 * size (px), body size (px), `unitsPerMeter`. The container is treated as square
 * in plan view (depth = inner width); a block's depth equals its width; a ball is
 * a sphere. Per sample: body bottom height b -> water level L from volume
 * conservation  A·L − V_sub(L − b) = V_water  (overflow caps L at the spout and
 * the excess goes to the catch container) -> submerged volume -> F_b = ρ_f·V_sub·g.
 */

import { materialOf } from "../catalog/materials";
import { containerLayout } from "../catalog/physics";
import { pNum, pStr } from "../catalog/spec";
import type { BuoyancySolverT, SolverDefT } from "../dsl/solvers";
import { clamp, roundTo } from "../math/num";
import { issue, type ValidationIssue } from "../validation/issues";
import { registerSolver, type SolverAttachment, type SolverOutput, type SolverRunContext } from "./registry";

const DT = 1 / 60;
const DURATION = { drop: 3.6, lower: 3.2, hold: 1 } as const;

interface Body {
  kind: "block" | "ball";
  /** Height of the body (m). */
  h: number;
  /** Block footprint area (m²) or ball radius (m). */
  a: number;
  r: number;
  volume: number;
}

function vSub(body: Body, s: number): number {
  const d = clamp(s, 0, body.h);
  if (body.kind === "ball") return (Math.PI * d * d * (3 * body.r - d)) / 3;
  return body.a * d;
}

/** Submerged depth that displaces volume v (inverse of vSub). */
function depthFor(body: Body, v: number): number {
  if (body.kind === "block") return clamp(v / body.a, 0, body.h);
  let lo = 0;
  let hi = body.h;
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (vSub(body, mid) < v) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

/** Water level for body bottom height b and water volume vw (A·L − V_sub(L−b) = vw). */
function levelFor(A: number, body: Body, b: number, vw: number): number {
  let lo = 0;
  let hi = (vw + body.volume) / A + body.h + 1;
  for (let i = 0; i < 64; i++) {
    const mid = (lo + hi) / 2;
    if (A * mid - vSub(body, mid - b) < vw) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

const ease = {
  inQuad: (u: number) => u * u,
  inOut: (u: number) => (u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2),
};

export const OUTPUTS = [
  { name: "buoyantForce", unit: "N", description: "Upward push of the fluid, ρ_f·V_sub·g" },
  { name: "weight", unit: "N", description: "Weight of the body, m·g" },
  { name: "apparentWeight", unit: "N", description: "Weight minus buoyant force (what a spring scale reads)" },
  { name: "netForce", unit: "N", description: "Buoyant force minus weight (positive = upward)" },
  { name: "submergedVolume", unit: "m³", description: "Volume of the body below the surface" },
  { name: "submergedVolumeCm3", unit: "cm³", description: "Submerged volume in cubic centimetres" },
  { name: "displacedVolume", unit: "m³", description: "Volume of fluid pushed aside (= submerged volume)" },
  { name: "displacedVolumeCm3", unit: "cm³", description: "Displaced volume in cubic centimetres" },
  { name: "displacedMass", unit: "kg", description: "Mass of the displaced fluid" },
  { name: "displacedWeight", unit: "N", description: "Weight of the displaced fluid (= buoyant force)" },
  { name: "submergedFraction", unit: "", description: "Fraction of the body below the surface (0..1)" },
  { name: "submergedDepth", unit: "m", description: "Depth of the body's bottom below the surface" },
  { name: "submergedPx", unit: "px", description: "Submerged depth in scene pixels (for overlays)" },
  { name: "level", unit: "", description: "Container fill fraction (0..1)" },
  { name: "levelRise", unit: "m", description: "Rise of the water level since the solver started" },
  { name: "levelRiseCm", unit: "cm", description: "Rise of the water level in centimetres" },
  { name: "levelHeightCm", unit: "cm", description: "Height of the water surface above the floor" },
  { name: "innerHeight", unit: "m", description: "Inner height of the container" },
  { name: "innerHeightCm", unit: "cm", description: "Inner height of the container in centimetres" },
  { name: "containerArea", unit: "m²", description: "Plan area of the container (width x depth)" },
  { name: "spilledVolume", unit: "m³", description: "Fluid that left through the spout" },
  { name: "spilledVolumeCm3", unit: "cm³", description: "Spilled fluid in cubic centimetres" },
  { name: "spilledWeight", unit: "N", description: "Weight of the spilled fluid" },
  { name: "bodyVolume", unit: "m³", description: "Volume of the body" },
  { name: "bodyVolumeCm3", unit: "cm³", description: "Volume of the body in cubic centimetres" },
  { name: "mass", unit: "kg", description: "Mass of the body" },
  { name: "bodyDensity", unit: "kg/m³", description: "Density of the body" },
  { name: "fluidDensity", unit: "kg/m³", description: "Density of the fluid" },
  { name: "g", unit: "m/s²", description: "Gravitational acceleration" },
  {
    name: "equilibriumFraction",
    unit: "",
    description: "Fraction submerged when floating freely (ρ_b/ρ_f, capped at 1)",
  },
  { name: "floats", unit: "", description: "1 if the body floats, 0 if it sinks" },
] as const;

export function runBuoyancy(defIn: SolverDefT, ctx: SolverRunContext): SolverOutput {
  const def = defIn as BuoyancySolverT;
  const issues: ValidationIssue[] = [];
  const fail = (code: string, message: string, suggestion?: string): SolverOutput => {
    issues.push(issue(code, ctx.path, message, suggestion ? { suggestion } : {}));
    return { dt: DT, duration: 0, channels: {}, constants: {}, outcome: {}, attach: [], issues };
  };
  const cont = ctx.at.objects.get(def.container);
  const bodyObj = ctx.at.objects.get(def.body);
  if (!cont || !bodyObj) return fail("SOLVER_OBJECT_MISSING", "Buoyancy solver objects are missing.");
  if (bodyObj.obj.parent || bodyObj.obj.links.length) {
    return fail(
      "SOLVER_BODY_PARENTED",
      `Body '${def.body}' is inside a group; the solver needs to move it directly.`,
      "Ungroup the body first.",
    );
  }

  const cl = containerLayout(cont.params);
  const cm = cont.rawMatrix;
  const s = Math.hypot(cm[0], cm[1]) || 1;
  const U = def.unitsPerMeter * s; // world px per meter
  const floorY = cm[5] + s * cl.inner.floor;
  const innerW = (cl.inner.x1 - cl.inner.x0) * s;
  const innerH = (cl.inner.floor - cl.inner.top) * s;
  const x0 = cm[4] + s * cl.inner.x0;
  const x1 = cm[4] + s * cl.inner.x1;
  const depthPx = (typeof cont.params.depth === "number" ? cont.params.depth : innerW / s) * s;
  const A = (innerW / U) * (depthPx / U);

  const bs = Math.hypot(bodyObj.rawMatrix[0], bodyObj.rawMatrix[1]) || 1;
  const bp = bodyObj.params;
  let body: Body;
  let bodyWpx: number;
  let bodyHpx: number;
  if (bodyObj.obj.type === "ball") {
    const r = (pNum(bp, "radius", 48) * bs) / U;
    body = { kind: "ball", h: 2 * r, a: Math.PI * r * r, r, volume: (4 / 3) * Math.PI * r ** 3 };
    bodyWpx = bodyHpx = 2 * pNum(bp, "radius", 48) * bs;
  } else {
    bodyWpx = pNum(bp, "width", 120) * bs;
    bodyHpx = pNum(bp, "height", 100) * bs;
    const w = bodyWpx / U;
    const h = bodyHpx / U;
    body = { kind: "block", h, a: w * w, r: 0, volume: w * w * h };
  }
  const center = { x: bodyObj.rawMatrix[4], y: bodyObj.rawMatrix[5] };
  if (bodyWpx >= innerW - 2 || bodyWpx >= depthPx) {
    return fail(
      "SOLVER_BODY_TOO_BIG",
      `Body '${def.body}' (${Math.round(bodyWpx)} px wide) does not fit inside '${def.container}' (${Math.round(innerW)} px inside).`,
      "Make the body narrower or the container wider.",
    );
  }
  if (center.x - bodyWpx / 2 < x0 - 1 || center.x + bodyWpx / 2 > x1 + 1) {
    return fail(
      "SOLVER_BODY_OUTSIDE",
      `Body '${def.body}' is not above the opening of '${def.container}'.`,
      `Place the body with {relativeTo: "${def.container}", side: "above"} or pin it to "${def.container}.water_surface".`,
    );
  }

  const materialName = pStr(bp, "material", "custom");
  const rhoB = def.bodyDensity ?? materialOf(materialName).density;
  const rhoF = def.fluidDensity;
  const g = def.g;
  const mass = rhoB * body.volume;
  const weight = mass * g;

  const level0 = clamp(pNum(cont.params, "level", 0.5), 0, 1);
  const hIn = innerH / U; // inner height (m)
  const b0 = (floorY - (center.y + bodyHpx / 2)) / U; // body bottom above floor (m)
  const L0 = level0 * hIn;
  const vw0 = A * L0 - vSub(body, L0 - b0);
  const spoutL = cl.spout ? clamp(pNum(cont.params, "spoutLevel", 0.75), 0.1, 0.98) * hIn : Number.POSITIVE_INFINITY;

  const floats = rhoB < rhoF * 0.999;
  const eqVolume = floats ? mass / rhoF : body.volume;
  const sEq = depthFor(body, eqVolume);

  // Equilibrium body position.
  let bEq: number;
  let state: string;
  if (def.mode === "hold") {
    bEq = b0;
    state = "held";
  } else if (def.mode === "lower") {
    const sTarget = def.holdDepth * body.h;
    const Lt = Math.min((vw0 + vSub(body, sTarget)) / A, spoutL);
    bEq = Math.max(0, Lt - sTarget);
    state = "held";
  } else if (floats) {
    const Leq = Math.min((vw0 + eqVolume) / A, spoutL);
    bEq = Leq - sEq;
    state = bEq < 0 ? "grounded" : "floats";
    bEq = Math.max(0, bEq);
  } else {
    bEq = 0;
    state = Math.abs(rhoB - rhoF) / rhoF < 0.01 ? "neutral" : "sinks";
  }

  // Stylized path b(t) over the solver's physical duration.
  const P = DURATION[def.mode];
  const n = Math.round(P / DT);
  const uA = def.mode === "drop" && b0 > L0 + 1e-6 ? 0.28 : 0;
  const tA = uA * P;
  const fallV = tA > 0 ? (2 * (b0 - L0)) / tA : 0; // speed at contact (m/s, downward)
  const bContact = uA > 0 ? L0 : b0;
  const path = (t: number): number => {
    if (def.mode === "hold") return b0;
    if (def.mode === "lower") return b0 + (bEq - b0) * ease.inOut(t / P);
    if (t < tA) return b0 - (b0 - L0) * ease.inQuad(t / tA);
    const tau = t - tA;
    if (state === "floats") {
      const w = (2 * Math.PI) / 1.25;
      const zeta = 0.3;
      const wd = w * Math.sqrt(1 - zeta * zeta);
      const x0v = bContact - bEq;
      const v0 = -fallV;
      const x =
        Math.exp(-zeta * w * tau) * (x0v * Math.cos(wd * tau) + ((v0 + zeta * w * x0v) / wd) * Math.sin(wd * tau));
      // Fade out the last of the bobbing so the run ends exactly at equilibrium.
      const u = clamp((t / P - 0.85) / 0.15, 0, 1);
      return Math.max(0, bEq + x * (1 - u * u * (3 - 2 * u)));
    }
    const span = Math.max(0.3, (P - tA) * 0.8);
    const u = clamp(tau / span, 0, 1);
    return bEq + (bContact - bEq) * (1 - u) ** 2;
  };

  const ch: Record<string, number[]> = {};
  const push = (k: string, v: number) => {
    const arr = ch[k] ?? [];
    arr.push(roundTo(v, 6));
    ch[k] = arr;
  };
  let spilled = 0;
  let outflow = 0;
  let prevSpilled = 0;
  let contactT: number | null = null;
  const catchObj = def.catchContainer ? ctx.at.objects.get(def.catchContainer) : undefined;
  let catchA = 0;
  let catchH = 1;
  let catchL0 = 0;
  if (catchObj) {
    const cc = containerLayout(catchObj.params);
    const cs = Math.hypot(catchObj.rawMatrix[0], catchObj.rawMatrix[1]) || 1;
    const cw = (cc.inner.x1 - cc.inner.x0) * cs;
    const cd = (typeof catchObj.params.depth === "number" ? catchObj.params.depth : cw / cs) * cs;
    catchA = (cw / U) * (cd / U);
    catchH = ((cc.inner.floor - cc.inner.top) * cs) / U;
    catchL0 = clamp(pNum(catchObj.params, "level", 0), 0, 1);
  }
  let overflowWarned = false;

  for (let i = 0; i <= n; i++) {
    const t = i * DT;
    const b = path(t);
    let vw = vw0 - spilled;
    let L = levelFor(A, body, b, vw);
    if (L > spoutL) {
      const inCan = A * spoutL - vSub(body, spoutL - b);
      spilled = Math.max(spilled, vw0 - inCan);
      vw = vw0 - spilled;
      L = levelFor(A, body, b, vw);
    }
    if (L > hIn && !overflowWarned) {
      overflowWarned = true;
      issues.push(
        issue("SOLVER_OVERFLOW", ctx.path, `The water in '${def.container}' would rise above the rim.`, {
          suggestion: "Start with less water, use a smaller body, or use an overflow_can with a catchContainer.",
        }),
      );
    }
    const sDepth = clamp(L - b, 0, body.h);
    const vs = vSub(body, sDepth);
    const fb = rhoF * vs * g;
    if (contactT === null && sDepth > 1e-5) contactT = t;
    const rate = (spilled - prevSpilled) / DT;
    prevSpilled = spilled;
    outflow = rate > 1e-9 ? 1 : Math.max(0, outflow - DT / 0.35);

    push("bodyY", floorY - b * U - bodyHpx / 2);
    push("level", clamp(L / hIn, 0, 1));
    push("buoyantForce", fb);
    push("apparentWeight", weight - fb);
    push("netForce", fb - weight);
    push("submergedVolume", vs);
    push("submergedVolumeCm3", vs * 1e6);
    push("displacedVolume", vs);
    push("displacedVolumeCm3", vs * 1e6);
    push("displacedMass", rhoF * vs);
    push("displacedWeight", fb);
    push("submergedFraction", body.h > 0 ? sDepth / body.h : 0);
    push("submergedDepth", sDepth);
    push("submergedPx", sDepth * U);
    push("levelRise", L - L0);
    push("levelRiseCm", (L - L0) * 100);
    push("levelHeightCm", L * 100);
    push("spilledVolume", spilled);
    push("spilledVolumeCm3", spilled * 1e6);
    push("spilledWeight", rhoF * spilled * g);
    // Ripples decay and are gone by the end of the run (the end state is calm water).
    const calm = clamp((t / P - 0.8) / 0.2, 0, 1);
    const rippleAmp = contactT === null ? 0 : (def.mode === "drop" ? 13 : 5) * Math.exp(-(t - contactT) / 0.9);
    push("ripple", rippleAmp * (1 - calm * calm * (3 - 2 * calm)));
    push("rippleX", (center.x - cm[4]) / s);
    push("outflow", outflow);
    if (catchObj) push("catchLevel", clamp(catchL0 + spilled / Math.max(1e-12, catchA) / catchH, 0, 1));
  }

  const attach: SolverAttachment[] = [];
  if (def.mode !== "hold") attach.push({ object: def.body, channel: "y", source: "bodyY" });
  attach.push({ object: def.container, channel: "params.level", source: "level" });
  attach.push({ object: def.container, channel: "params.ripple", source: "ripple" });
  attach.push({ object: def.container, channel: "params.rippleX", source: "rippleX" });
  if (cl.spout) attach.push({ object: def.container, channel: "params.outflow", source: "outflow" });
  if (catchObj && def.catchContainer)
    attach.push({ object: def.catchContainer, channel: "params.level", source: "catchLevel" });
  if (def.scale) attach.push({ object: def.scale, channel: "params.reading", source: "apparentWeight" });

  return {
    dt: DT,
    duration: P,
    channels: ch,
    constants: {
      weight: roundTo(weight, 6),
      mass: roundTo(mass, 6),
      bodyVolume: roundTo(body.volume, 9),
      bodyVolumeCm3: roundTo(body.volume * 1e6, 4),
      bodyDensity: rhoB,
      fluidDensity: rhoF,
      g,
      equilibriumFraction: roundTo(Math.min(1, rhoB / rhoF), 6),
      innerHeight: roundTo(hIn, 6),
      innerHeightCm: roundTo(hIn * 100, 4),
      containerArea: roundTo(A, 8),
      floats: floats ? 1 : 0,
    },
    outcome: { state, floats, material: materialName },
    attach,
    issues,
  };
}

registerSolver({
  type: "buoyancy",
  outputs: OUTPUTS,
  naturalDuration: (def) => DURATION[(def as BuoyancySolverT).mode],
  objectRefs: (def) => {
    const d = def as BuoyancySolverT;
    return [
      { id: d.container, role: "container", types: ["container"] },
      { id: d.body, role: "body", types: ["block", "ball"] },
      ...(d.catchContainer ? [{ id: d.catchContainer, role: "catchContainer", types: ["container"] }] : []),
      ...(d.scale ? [{ id: d.scale, role: "scale", types: ["spring_scale"] }] : []),
    ];
  },
  run: runBuoyancy,
});
