/**
 * Solver declarations. A solver turns SI parameters into baked tracks and named
 * outputs (channels) that bindings can reference as `<solver_id>.<output>`.
 * Implementations live in ../solvers.
 */
import { z } from "zod";
import { Id } from "./primitives";

const SolverCommon = { id: Id };

export const BuoyancySolver = z
  .strictObject({
    ...SolverCommon,
    type: z.literal("buoyancy"),
    container: Id.describe("A container object holding the fluid"),
    body: Id.describe("A block or ball that enters the fluid"),
    bodyDensity: z
      .number()
      .min(1)
      .max(25000)
      .optional()
      .describe("kg/m^3. Defaults to the density of the body's material"),
    fluidDensity: z.number().min(1).max(25000).default(1000).describe("kg/m^3"),
    mode: z
      .enum(["drop", "lower", "hold"])
      .default("drop")
      .describe("drop: released and settles; lower: lowered on a string to holdDepth; hold: held still at holdDepth"),
    holdDepth: z
      .number()
      .min(0)
      .max(1)
      .default(1)
      .describe("For lower/hold: fraction of the body's height submerged at the end"),
    g: z.number().min(0.1).max(30).default(9.81),
    unitsPerMeter: z.number().min(10).max(5000).default(400).describe("Scene pixels per meter"),
    catchContainer: Id.optional().describe("Container that collects overflow (for an overflow/Eureka can)"),
    scale: Id.optional().describe("A spring_scale that reads the apparent weight"),
  })
  .meta({ id: "BuoyancySolver" });

export const FluidFlowSolver = z
  .strictObject({
    ...SolverCommon,
    type: z.literal("fluid_flow"),
    pipe: Id.describe("A pipe object with named sections"),
    fluidDensity: z.number().min(1).max(25000).default(1000),
    flowRate: z.number().min(0).max(100).describe("Volumetric flow rate Q in m^3/s"),
    inletPressure: z.number().min(0).max(1e8).describe("Pressure at the first section in Pa"),
    g: z.number().min(0.1).max(30).default(9.81),
    unitsPerMeter: z.number().min(10).max(5000).default(400),
  })
  .meta({ id: "FluidFlowSolver" });

export const ProjectileSolver = z
  .strictObject({
    ...SolverCommon,
    type: z.literal("projectile"),
    body: Id,
    speed: z.number().min(0).max(500).describe("Launch speed in m/s"),
    angle: z.number().min(-90).max(90).describe("Launch angle in degrees above horizontal"),
    g: z.number().min(0.1).max(30).default(9.81),
    drag: z.number().min(0).max(5).default(0).describe("Linear drag coefficient per second"),
    unitsPerMeter: z.number().min(1).max(5000).default(60),
    groundY: z.number().optional().describe("Scene y of the ground; defaults to the launch height"),
  })
  .meta({ id: "ProjectileSolver" });

export const PendulumSolver = z
  .strictObject({
    ...SolverCommon,
    type: z.literal("pendulum"),
    pendulum: Id.describe("A pendulum object"),
    length: z.number().min(0.05).max(100).describe("Length in meters"),
    angle: z.number().min(-170).max(170).describe("Initial angle in degrees from vertical"),
    g: z.number().min(0.1).max(30).default(9.81),
    damping: z.number().min(0).max(5).default(0),
  })
  .meta({ id: "PendulumSolver" });

export const SpringMassSolver = z
  .strictObject({
    ...SolverCommon,
    type: z.literal("spring_mass"),
    spring: Id,
    body: Id,
    stiffness: z.number().min(0.01).max(1e6).describe("k in N/m"),
    mass: z.number().min(0.001).max(1e5).describe("kg"),
    amplitude: z.number().min(-10).max(10).describe("Initial displacement in meters"),
    damping: z.number().min(0).max(1e4).default(0).describe("c in N·s/m"),
    unitsPerMeter: z.number().min(10).max(5000).default(400),
  })
  .meta({ id: "SpringMassSolver" });

export const SolverDef = z
  .discriminatedUnion("type", [BuoyancySolver, FluidFlowSolver, ProjectileSolver, PendulumSolver, SpringMassSolver])
  .meta({ id: "Solver" });

export type SolverDefT = z.infer<typeof SolverDef>;
export type BuoyancySolverT = z.infer<typeof BuoyancySolver>;
export type FluidFlowSolverT = z.infer<typeof FluidFlowSolver>;
export type ProjectileSolverT = z.infer<typeof ProjectileSolver>;
export type PendulumSolverT = z.infer<typeof PendulumSolver>;
export type SpringMassSolverT = z.infer<typeof SpringMassSolver>;
export const SOLVER_TYPES = ["buoyancy", "fluid_flow", "projectile", "pendulum", "spring_mass"] as const;
