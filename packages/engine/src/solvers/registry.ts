/**
 * Solver contract. A solver is deterministic: params (SI) + scene geometry at the
 * moment it starts -> sampled channels (in solver-local physical time), constant
 * outputs, and attachments that drive object channels from those samples.
 */
import type { SolverDefT } from "../dsl/solvers";
import type { EvalContext } from "../evaluate/evaluate";
import type { Theme } from "../theme/theme";
import type { ValidationIssue } from "../validation/issues";

export interface SolverAttachment {
  object: string;
  /** Object channel: "x", "y", "rotation", "params.level", ... */
  channel: string;
  /** Solver channel name. */
  source: string;
  gain?: number;
  offset?: number;
}

export interface SolverOutput {
  /** Sample spacing in solver-local seconds. */
  dt: number;
  /** Physical duration covered by the samples. */
  duration: number;
  channels: Record<string, number[]>;
  constants: Record<string, number>;
  /** Qualitative results used by validators (e.g. { state: "floats" }). */
  outcome: Record<string, string | number | boolean>;
  attach: SolverAttachment[];
  issues: ValidationIssue[];
}

export interface SolverRunContext {
  theme: Theme;
  /** Scene state at the moment the solver starts. */
  at: EvalContext;
  path: string;
  seed: number;
}

export interface SolverImpl {
  type: SolverDefT["type"];
  /** Output names available to bindings as `<solver id>.<name>`. */
  outputs: readonly { name: string; unit: string; description: string }[];
  /** Default run duration in scene seconds (when the action gives none). */
  naturalDuration(def: SolverDefT): number;
  /** Object ids the solver reads/writes (for reference validation). */
  objectRefs(def: SolverDefT): { id: string; role: string; types: readonly string[] }[];
  run(def: SolverDefT, ctx: SolverRunContext): SolverOutput;
}

const SOLVERS = new Map<string, SolverImpl>();

export function registerSolver(impl: SolverImpl): void {
  SOLVERS.set(impl.type, impl);
}

export const getSolver = (type: string): SolverImpl | undefined => SOLVERS.get(type);
export const allSolvers = (): SolverImpl[] => [...SOLVERS.values()];
