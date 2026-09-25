/**
 * Steady incompressible flow through a pipe with named sections (continuity +
 * Bernoulli). Circular cross-sections: A = π d² / 4, v = Q / A,
 *   p_i = p_1 + ½ ρ (v_1² − v_i²) + ρ g (h_1 − h_i).
 * Running it ramps the flow up from rest (for the particles); pressures and
 * speeds are exact constants available as `<id>.v_<section>`, `<id>.p_<section>`, ...
 */
import { pipeLayout } from "../catalog/physics";
import type { SceneT } from "../dsl/scene";
import type { FluidFlowSolverT, SolverDefT } from "../dsl/solvers";
import { roundTo } from "../math/num";
import { issue, type ValidationIssue } from "../validation/issues";
import { registerSolver, type SolverOutput, type SolverRunContext } from "./registry";

const DT = 1 / 60;
const RAMP = 1.5;

const PER_SECTION = ["v", "p", "p_kpa", "area", "diameter", "height"] as const;

function sectionIdsFromScene(def: FluidFlowSolverT, scene: SceneT): string[] {
  const pipe = scene.objects.find((o) => o.id === def.pipe);
  const sections = pipe?.params.sections;
  return Array.isArray(sections) ? (sections as { id: string }[]).map((s) => s.id) : [];
}

export function runFluidFlow(defIn: SolverDefT, ctx: SolverRunContext): SolverOutput {
  const def = defIn as FluidFlowSolverT;
  const issues: ValidationIssue[] = [];
  const pipe = ctx.at.objects.get(def.pipe);
  if (!pipe) {
    issues.push(issue("SOLVER_OBJECT_MISSING", ctx.path, `Pipe '${def.pipe}' is missing.`));
    return { dt: DT, duration: 0, channels: {}, constants: {}, outcome: {}, attach: [], issues };
  }
  const l = pipeLayout(pipe.params);
  const U = def.unitsPerMeter;
  const rho = def.fluidDensity;
  const Q = def.flowRate;
  const first = l.sections[0] as { id: string; width: number; y: number };
  const constants: Record<string, number> = { Q, rho, g: def.g, p_in: def.inletPressure };
  const areaOf = (w: number) => (Math.PI * (w / U) ** 2) / 4;
  const v1 = Q / areaOf(first.width);
  const h1 = -first.y / U;
  let pMin = Number.POSITIVE_INFINITY;
  let vMax = 0;
  for (const s of l.sections) {
    const A = areaOf(s.width);
    const v = Q / A;
    const h = -s.y / U;
    const p = def.inletPressure + 0.5 * rho * (v1 * v1 - v * v) + rho * def.g * (h1 - h);
    constants[`v_${s.id}`] = roundTo(v, 6);
    constants[`p_${s.id}`] = roundTo(p, 4);
    constants[`p_${s.id}_kpa`] = roundTo(p / 1000, 6);
    constants[`area_${s.id}`] = roundTo(A, 9);
    constants[`diameter_${s.id}`] = roundTo(s.width / U, 6);
    constants[`height_${s.id}`] = roundTo(h, 6);
    pMin = Math.min(pMin, p);
    vMax = Math.max(vMax, v);
    if (p < 0) {
      issues.push(
        issue(
          "SOLVER_NEGATIVE_PRESSURE",
          ctx.path,
          `Pressure at section '${s.id}' would be negative (${Math.round(p)} Pa): not physical.`,
          {
            severity: "warning",
            suggestion: "Lower the flow rate or raise the inlet pressure.",
          },
        ),
      );
    }
  }
  constants.v_max = roundTo(vMax, 6);
  constants.p_min = roundTo(pMin, 4);
  constants.pressureDrop = roundTo(def.inletPressure - pMin, 4);
  const n = Math.round(RAMP / DT);
  const ramp: number[] = [];
  for (let i = 0; i <= n; i++) {
    const u = i / n;
    ramp.push(roundTo(u * u * (3 - 2 * u), 6));
  }
  return {
    dt: DT,
    duration: RAMP,
    channels: { ramp },
    constants,
    outcome: { state: "steady" },
    attach: [],
    issues,
  };
}

registerSolver({
  type: "fluid_flow",
  outputs: [
    { name: "Q", unit: "m³/s", description: "Volumetric flow rate" },
    { name: "rho", unit: "kg/m³", description: "Fluid density" },
    { name: "g", unit: "m/s²", description: "Gravitational acceleration" },
    { name: "p_in", unit: "Pa", description: "Inlet pressure" },
    { name: "v_max", unit: "m/s", description: "Fastest speed along the pipe" },
    { name: "p_min", unit: "Pa", description: "Lowest pressure along the pipe" },
    { name: "pressureDrop", unit: "Pa", description: "Inlet pressure minus the lowest pressure" },
    { name: "ramp", unit: "", description: "Start-up ramp 0..1 while the solver runs" },
  ],
  dynamicOutputs: (def, scene) =>
    sectionIdsFromScene(def as FluidFlowSolverT, scene).flatMap((id) =>
      PER_SECTION.map((k) => (k === "p_kpa" ? `p_${id}_kpa` : `${k}_${id}`)),
    ),
  naturalDuration: () => RAMP,
  objectRefs: (def) => [{ id: (def as FluidFlowSolverT).pipe, role: "pipe", types: ["pipe"] }],
  run: runFluidFlow,
});
