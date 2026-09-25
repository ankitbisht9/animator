/**
 * Projectile, pendulum and spring-mass solvers. Numeric integration uses a fixed
 * 1/240 s RK4 step and is sampled at 60 Hz; nothing depends on frame rate.
 */
import type { PendulumSolverT, ProjectileSolverT, SolverDefT, SpringMassSolverT } from "../dsl/solvers";
import { clamp, deg2rad, rad2deg, roundTo } from "../math/num";
import { issue } from "../validation/issues";
import { registerSolver, type SolverOutput, type SolverRunContext } from "./registry";

const STEP = 1 / 240;
const SAMPLE = 1 / 60;

type Deriv = (s: number[]) => number[];

function rk4(s: number[], f: Deriv, h: number): number[] {
  const k1 = f(s);
  const k2 = f(s.map((v, i) => v + (h / 2) * (k1[i] as number)));
  const k3 = f(s.map((v, i) => v + (h / 2) * (k2[i] as number)));
  const k4 = f(s.map((v, i) => v + h * (k3[i] as number)));
  return s.map(
    (v, i) => v + (h / 6) * ((k1[i] as number) + 2 * (k2[i] as number) + 2 * (k3[i] as number) + (k4[i] as number)),
  );
}

/** Integrate for `duration` seconds, recording `record(state, t)` every SAMPLE. Stops early if `stop` returns true. */
function integrate(
  s0: number[],
  f: Deriv,
  duration: number,
  record: (s: number[], t: number) => void,
  stop?: (s: number[]) => boolean,
): number {
  let s = s0;
  let t = 0;
  const perSample = Math.round(SAMPLE / STEP);
  record(s, 0);
  let steps = 0;
  while (t < duration - 1e-9) {
    s = rk4(s, f, STEP);
    t = roundTo(t + STEP, 9);
    steps++;
    if (steps % perSample === 0) record(s, t);
    if (stop?.(s)) {
      if (steps % perSample !== 0) record(s, t);
      return t;
    }
  }
  return t;
}

const r6 = (v: number) => roundTo(v, 6);

// ------------------------------------------------------------------ projectile

function runProjectile(defIn: SolverDefT, ctx: SolverRunContext): SolverOutput {
  const def = defIn as ProjectileSolverT;
  const body = ctx.at.objects.get(def.body);
  if (!body) {
    return {
      dt: SAMPLE,
      duration: 0,
      channels: {},
      constants: {},
      outcome: {},
      attach: [],
      issues: [issue("SOLVER_OBJECT_MISSING", ctx.path, `Body '${def.body}' is missing.`)],
    };
  }
  const U = def.unitsPerMeter;
  const x0 = body.rawMatrix[4];
  const y0 = body.rawMatrix[5];
  const groundUp = def.groundY === undefined ? 0 : (y0 - def.groundY) / U; // ground height relative to launch (up +)
  const a = deg2rad(def.angle);
  const vx0 = def.speed * Math.cos(a);
  const vy0 = def.speed * Math.sin(a);
  const k = def.drag;
  const f: Deriv = ([, , vx = 0, vy = 0]) => [vx, vy, -k * vx, -def.g - k * vy];
  const ch: Record<string, number[]> = { x: [], y: [], vx: [], vy: [], speed: [], height: [], distance: [] };
  let maxH = 0;
  const T = integrate(
    [0, 0, vx0, vy0],
    f,
    30,
    (s) => {
      const [px = 0, py = 0, vx = 0, vy = 0] = s;
      maxH = Math.max(maxH, py - groundUp);
      ch.x?.push(r6(x0 + px * U));
      ch.y?.push(r6(y0 - py * U));
      ch.vx?.push(r6(vx));
      ch.vy?.push(r6(vy));
      ch.speed?.push(r6(Math.hypot(vx, vy)));
      ch.height?.push(r6(py - groundUp));
      ch.distance?.push(r6(px));
    },
    ([, py = 0, , vy = 0]) => vy < 0 && py <= groundUp,
  );
  const range = (ch.distance?.[ch.distance.length - 1] ?? 0) as number;
  return {
    dt: SAMPLE,
    duration: Math.max(SAMPLE, (ch.x?.length ?? 1) * SAMPLE - SAMPLE),
    channels: ch,
    constants: {
      flightTime: r6(T),
      range: r6(range),
      maxHeight: r6(maxH),
      speed0: def.speed,
      angle: def.angle,
      g: def.g,
    },
    outcome: { state: "landed" },
    attach: [
      { object: def.body, channel: "x", source: "x" },
      { object: def.body, channel: "y", source: "y" },
    ],
    issues: [],
  };
}

registerSolver({
  type: "projectile",
  outputs: [
    { name: "x", unit: "px", description: "Scene x of the body" },
    { name: "y", unit: "px", description: "Scene y of the body" },
    { name: "vx", unit: "m/s", description: "Horizontal velocity" },
    { name: "vy", unit: "m/s", description: "Vertical velocity (up +)" },
    { name: "speed", unit: "m/s", description: "Speed" },
    { name: "height", unit: "m", description: "Height above the ground" },
    { name: "distance", unit: "m", description: "Horizontal distance travelled" },
    { name: "flightTime", unit: "s", description: "Time until landing" },
    { name: "range", unit: "m", description: "Horizontal distance at landing" },
    { name: "maxHeight", unit: "m", description: "Highest point above the ground" },
    { name: "speed0", unit: "m/s", description: "Launch speed" },
    { name: "angle", unit: "°", description: "Launch angle" },
    { name: "g", unit: "m/s²", description: "Gravitational acceleration" },
  ],
  naturalDuration: (def) => {
    const d = def as ProjectileSolverT;
    return clamp((2 * d.speed * Math.sin(deg2rad(d.angle))) / d.g, 0.5, 12);
  },
  objectRefs: (def) => [{ id: (def as ProjectileSolverT).body, role: "body", types: [] }],
  run: runProjectile,
});

// ------------------------------------------------------------------ pendulum

function runPendulum(defIn: SolverDefT, _ctx: SolverRunContext): SolverOutput {
  const def = defIn as PendulumSolverT;
  const L = def.length;
  const period0 = 2 * Math.PI * Math.sqrt(L / def.g);
  const duration = clamp(3 * period0, 2, 20);
  const f: Deriv = ([th = 0, om = 0]) => [om, -(def.g / L) * Math.sin(th) - def.damping * om];
  const ch: Record<string, number[]> = { angle: [], omega: [], speed: [], height: [], kinetic: [], potential: [] };
  integrate([deg2rad(def.angle), 0], f, duration, ([th = 0, om = 0]) => {
    const v = L * om;
    const h = L * (1 - Math.cos(th));
    ch.angle?.push(r6(rad2deg(th)));
    ch.omega?.push(r6(om));
    ch.speed?.push(r6(Math.abs(v)));
    ch.height?.push(r6(h));
    ch.kinetic?.push(r6(0.5 * v * v));
    ch.potential?.push(r6(def.g * h));
  });
  return {
    dt: SAMPLE,
    duration,
    channels: ch,
    constants: { period: r6(period0), length: L, g: def.g },
    outcome: { state: "oscillating" },
    attach: [{ object: def.pendulum, channel: "params.angle", source: "angle" }],
    issues: [],
  };
}

registerSolver({
  type: "pendulum",
  outputs: [
    { name: "angle", unit: "°", description: "Angle from vertical" },
    { name: "omega", unit: "rad/s", description: "Angular velocity" },
    { name: "speed", unit: "m/s", description: "Speed of the bob" },
    { name: "height", unit: "m", description: "Height of the bob above its lowest point" },
    { name: "kinetic", unit: "J/kg", description: "Kinetic energy per kilogram" },
    { name: "potential", unit: "J/kg", description: "Potential energy per kilogram" },
    { name: "period", unit: "s", description: "Small-angle period 2π√(L/g)" },
    { name: "length", unit: "m", description: "Length" },
    { name: "g", unit: "m/s²", description: "Gravitational acceleration" },
  ],
  naturalDuration: (def) =>
    clamp(3 * 2 * Math.PI * Math.sqrt((def as PendulumSolverT).length / (def as PendulumSolverT).g), 2, 20),
  objectRefs: (def) => [{ id: (def as PendulumSolverT).pendulum, role: "pendulum", types: ["pendulum"] }],
  run: runPendulum,
});

// ------------------------------------------------------------------ spring-mass

function runSpringMass(defIn: SolverDefT, ctx: SolverRunContext): SolverOutput {
  const def = defIn as SpringMassSolverT;
  const body = ctx.at.objects.get(def.body);
  const y0 = body?.rawMatrix[5] ?? 0;
  const w0 = Math.sqrt(def.stiffness / def.mass);
  const zeta = def.damping / (2 * Math.sqrt(def.stiffness * def.mass));
  const period = (2 * Math.PI) / w0;
  const duration = clamp(3 * period, 2, 20);
  const A = def.amplitude;
  const disp = (t: number): number => {
    if (zeta < 1) {
      const wd = w0 * Math.sqrt(1 - zeta * zeta);
      return A * Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + ((zeta * w0) / wd) * Math.sin(wd * t));
    }
    if (zeta === 1) return A * (1 + w0 * t) * Math.exp(-w0 * t);
    const r1 = -w0 * (zeta - Math.sqrt(zeta * zeta - 1));
    const r2 = -w0 * (zeta + Math.sqrt(zeta * zeta - 1));
    return (A * (r2 * Math.exp(r1 * t) - r1 * Math.exp(r2 * t))) / (r2 - r1);
  };
  const ch: Record<string, number[]> = { displacement: [], y: [], velocity: [], force: [], kinetic: [], potential: [] };
  const n = Math.round(duration / SAMPLE);
  for (let i = 0; i <= n; i++) {
    const t = i * SAMPLE;
    const x = disp(t);
    const v = (disp(t + 1e-4) - disp(t - 1e-4)) / 2e-4;
    ch.displacement?.push(r6(x));
    ch.y?.push(r6(y0 + (x - A) * def.unitsPerMeter));
    ch.velocity?.push(r6(v));
    ch.force?.push(r6(-def.stiffness * x));
    ch.kinetic?.push(r6(0.5 * def.mass * v * v));
    ch.potential?.push(r6(0.5 * def.stiffness * x * x));
  }
  return {
    dt: SAMPLE,
    duration,
    channels: ch,
    constants: {
      period: r6(period),
      omega: r6(w0),
      frequency: r6(1 / period),
      stiffness: def.stiffness,
      mass: def.mass,
    },
    outcome: { state: zeta < 1 ? "oscillating" : "damped" },
    attach: body ? [{ object: def.body, channel: "y", source: "y" }] : [],
    issues: [],
  };
}

registerSolver({
  type: "spring_mass",
  outputs: [
    { name: "displacement", unit: "m", description: "Displacement from equilibrium (down +)" },
    { name: "y", unit: "px", description: "Scene y of the body" },
    { name: "velocity", unit: "m/s", description: "Velocity" },
    { name: "force", unit: "N", description: "Spring force −k·x" },
    { name: "kinetic", unit: "J", description: "Kinetic energy" },
    { name: "potential", unit: "J", description: "Elastic potential energy" },
    { name: "period", unit: "s", description: "Undamped period 2π√(m/k)" },
    { name: "omega", unit: "rad/s", description: "Natural angular frequency" },
    { name: "frequency", unit: "Hz", description: "Natural frequency" },
    { name: "stiffness", unit: "N/m", description: "Spring constant" },
    { name: "mass", unit: "kg", description: "Mass" },
  ],
  naturalDuration: (def) => {
    const d = def as SpringMassSolverT;
    return clamp(3 * 2 * Math.PI * Math.sqrt(d.mass / d.stiffness), 2, 20);
  },
  objectRefs: (def) => {
    const d = def as SpringMassSolverT;
    return [
      { id: d.spring, role: "spring", types: ["spring"] },
      { id: d.body, role: "body", types: [] },
    ];
  },
  run: runSpringMass,
});
